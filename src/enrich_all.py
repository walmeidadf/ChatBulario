"""
Estágio B — enriquecimento: segments.jsonl → meta.jsonl via OpenAI Batch API.

A Batch API não tem RPD — aceita até 50.000 requisições por submissão,
processa em até 24h com 50% de desconto vs. API síncrona. Cada registro
é chamado no máximo 1×: registros já em meta.jsonl são pulados.

Fluxo padrão (submete e aguarda):
    uv run python enrich_all.py

Fluxo assíncrono (submete, retorna imediatamente com o batch_id):
    uv run python enrich_all.py --async
    # ... espera 24h ...
    uv run python enrich_all.py --retrieve <batch_id>

Flags:
  --async             Submete o batch e imprime o batch_id. Não aguarda.
  --retrieve BATCH_ID Baixa e processa resultado de um batch já concluído.
  --min-secoes N      Só enriquece bulas com >= N seções encontradas (padrão: 1).
  --status BATCH_ID   Consulta o status de um batch sem baixar resultados.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent / "process"))
from meta_llm import _build_messages, _cfg, _parse_resposta, _CAMPOS  # noqa: E402

from openai import OpenAI  # noqa: E402

WORK_DIR = Path("dataset/work_data")
SEGMENTS = WORK_DIR / "segments.jsonl"
META_OUT = WORK_DIR / "meta.jsonl"

_POLL_INTERVAL = 60  # segundos entre verificações de status


def carregar_ja_enriquecidos() -> set[str]:
    if not META_OUT.exists():
        return set()
    regs: set[str] = set()
    with META_OUT.open() as f:
        for l in f:
            try:
                regs.add(json.loads(l)["registro"])
            except (json.JSONDecodeError, KeyError):
                pass
    return regs


def carregar_pendentes(min_secoes: int) -> list[dict]:
    if not SEGMENTS.exists():
        print("segments.jsonl não encontrado. Rode segment_all.py primeiro.")
        sys.exit(1)
    ja = carregar_ja_enriquecidos()
    pendentes = []
    with SEGMENTS.open() as f:
        for l in f:
            try:
                d = json.loads(l)
            except json.JSONDecodeError:
                continue
            if (d.get("registro") not in ja
                    and d.get("secoes_encontradas", 0) >= min_secoes
                    and d.get("identificacao", "").strip()):
                pendentes.append(d)
    return pendentes


def _batch_input_jsonl(pendentes: list[dict], cfg: dict) -> bytes:
    linhas = []
    for d in pendentes:
        req = {
            "custom_id": d["registro"],
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": cfg["model"],
                "messages": _build_messages(d["identificacao"]),
                "max_tokens": cfg["max_tokens"],
                "temperature": 0,
            },
        }
        linhas.append(json.dumps(req, ensure_ascii=False))
    return "\n".join(linhas).encode()


_CHUNK_SIZE = 800  # requisições por batch (limite: ~2M tokens enfileirados por org)


def _submeter_chunk(client: OpenAI, chunk: list[dict], cfg: dict, idx: int) -> str:
    conteudo = _batch_input_jsonl(chunk, cfg)
    tamanho_mb = len(conteudo) / 1024 / 1024
    print(f"  chunk {idx}: {len(chunk)} registros, {tamanho_mb:.1f} MB")
    arquivo = client.files.create(
        file=(f"batch_input_{idx}.jsonl", conteudo),
        purpose="batch",
    )
    batch = client.batches.create(
        input_file_id=arquivo.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    print(f"  batch criado: {batch.id}  status={batch.status}")
    return batch.id


def submeter(client: OpenAI, pendentes: list[dict], cfg: dict) -> str:
    """Submete em chunks para respeitar o limite de 2M tokens enfileirados da OpenAI."""
    chunks = [pendentes[i:i + _CHUNK_SIZE] for i in range(0, len(pendentes), _CHUNK_SIZE)]
    print(f"Preparando {len(chunks)} batch(es) com {len(pendentes)} registros total...")

    ids = []
    for i, chunk in enumerate(chunks, 1):
        batch_id = _submeter_chunk(client, chunk, cfg, i)
        ids.append(batch_id)

    if len(ids) == 1:
        return ids[0]

    # Salva todos os ids para acompanhamento
    ids_path = Path("dataset/work_data/batch_ids.txt")
    with ids_path.open("w") as f:
        for bid in ids:
            f.write(bid + "\n")
    print(f"\n{len(ids)} batches submetidos. IDs salvos em {ids_path}")
    return ids[0]  # retorna o primeiro; retrieve precisa ser rodado para cada um


def status(client: OpenAI, batch_id: str) -> None:
    b = client.batches.retrieve(batch_id)
    c = b.request_counts
    print(f"Batch {batch_id}")
    print(f"  status:     {b.status}")
    print(f"  progresso:  {c.completed}/{c.total} ({c.failed} falhas)")
    if b.status == "completed":
        print(f"  output_file_id: {b.output_file_id}")
        print(f"\nPara processar: uv run python enrich_all.py --retrieve {batch_id}")


def aguardar(client: OpenAI, batch_id: str) -> str:
    print("Aguardando conclusão do batch (polling a cada 60s)...")
    while True:
        b = client.batches.retrieve(batch_id)
        c = b.request_counts
        pct = f" {c.completed}/{c.total}" if c.total else ""
        print(f"  status={b.status}{pct}", flush=True)
        if b.status == "completed":
            return b.output_file_id
        if b.status in ("failed", "expired", "cancelled"):
            raise RuntimeError(f"Batch {batch_id} terminou com status={b.status}")
        time.sleep(_POLL_INTERVAL)


def processar_resultado(client: OpenAI, batch_id: str) -> None:
    b = client.batches.retrieve(batch_id)
    if b.status != "completed":
        print(f"Batch {batch_id} ainda não concluído (status={b.status}).")
        c = b.request_counts
        print(f"  progresso: {c.completed}/{c.total}")
        sys.exit(1)

    print(f"Baixando resultado (output_file_id={b.output_file_id})...")
    conteudo = client.files.content(b.output_file_id).text

    n_ok = n_vazio = n_erro = 0
    with META_OUT.open("a") as f:
        for linha in conteudo.splitlines():
            if not linha.strip():
                continue
            try:
                r = json.loads(linha)
                registro = r["custom_id"]
                choices = (r.get("response") or {}).get("body", {}).get("choices", [])
                if choices:
                    texto = choices[0].get("message", {}).get("content", "") or ""
                    meta = _parse_resposta(texto)
                    n_ok += 1
                else:
                    meta = {c: None for c in _CAMPOS}
                    n_vazio += 1
                f.write(json.dumps({"registro": registro, **meta}, ensure_ascii=False) + "\n")
                f.flush()
            except Exception as exc:
                print(f"  ! erro parsing linha: {exc}")
                n_erro += 1

    print(f"meta.jsonl: +{n_ok} registros ({n_vazio} sem resposta, {n_erro} erros de parsing)")
    print(f"\nPróximo passo: uv run python build_dataset.py")


def main() -> None:
    ap = argparse.ArgumentParser(description="Enriquece segments.jsonl com LLM via Batch API")
    ap.add_argument("--async", dest="async_mode", action="store_true",
                    help="Submete o batch e imprime o batch_id. Não aguarda.")
    ap.add_argument("--retrieve", metavar="BATCH_ID",
                    help="Baixa e processa resultado de um batch já concluído.")
    ap.add_argument("--status", metavar="BATCH_ID",
                    help="Consulta status de um batch sem baixar resultados.")
    ap.add_argument("--min-secoes", type=int, default=1,
                    help="Só enriquece bulas com >= N seções encontradas (padrão: 1)")
    args = ap.parse_args()

    cfg = _cfg()
    if cfg.get("provider") != "openai":
        print("A Batch API só está disponível com LLM_PROVIDER=openai.")
        print("Defina LLM_PROVIDER=openai no .env e tente novamente.")
        sys.exit(1)

    client = OpenAI(api_key=cfg["api_key"])

    if args.status:
        status(client, args.status)
        return

    if args.retrieve:
        processar_resultado(client, args.retrieve)
        return

    pendentes = carregar_pendentes(args.min_secoes)
    if not pendentes:
        print("Nada a enriquecer — todos os registros já estão em meta.jsonl.")
        return

    ja = len(carregar_ja_enriquecidos())
    print(f"meta.jsonl: {ja} já enriquecidos | pendentes: {len(pendentes)}")

    batch_id = submeter(client, pendentes, cfg)

    if args.async_mode:
        ids_path = Path("dataset/work_data/batch_ids.txt")
        if ids_path.exists():
            ids = ids_path.read_text().splitlines()
        else:
            ids = [batch_id]
        print(f"\nBatches submetidos. Retorne em até 24h e rode para cada um:")
        for bid in ids:
            print(f"  uv run python enrich_all.py --retrieve {bid}")
        print(f"\nPara verificar status:")
        for bid in ids:
            print(f"  uv run python enrich_all.py --status {bid}")
        return

    output_file_id = aguardar(client, batch_id)
    processar_resultado(client, batch_id)


if __name__ == "__main__":
    main()
