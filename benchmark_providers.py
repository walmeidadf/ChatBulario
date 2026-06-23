"""
Benchmark de providers LLM para extração de metadados de bulas.

Roda N bulas em cada provider e compara qualidade, latência e custo.

Uso:
    uv run python benchmark_providers.py [--n 10] [--providers openai groq cerebras]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent / "process"))

from extract import extract_text   # noqa: E402
from segment import segment        # noqa: E402
from split import split_primeira_bula  # noqa: E402

RAW_DIR = Path("dataset/raw_data/anvisa_medicine_leaflets")

# ---------------------------------------------------------------------------
# Rate limits por provider (para calcular ETA do dataset completo)
# ---------------------------------------------------------------------------
RATE_LIMITS = {
    "openai":   {"rpm": 500,  "tpm": 200_000, "rpd": None,   "tpd": None},
    "groq":     {"rpm": 30,   "tpm": 6_000,   "rpd": 14_400, "tpd": 500_000},
    "cerebras": {"rpm": 5,    "tpm": 30_000,  "rpd": None,   "tpd": 1_000_000},
}

CAMPOS = [
    "nome_comercial", "fabricante", "principio_ativo",
    "forma_farmaceutica", "via_administracao", "apresentacao",
    "composicao", "uso",
]


def carregar_amostras(n: int) -> list[tuple[str, str, str]]:
    """Retorna lista de (registro, nome_produto, texto_identificacao)."""
    idx = {}
    with (RAW_DIR / "index.jsonl").open() as f:
        for linha in f:
            d = json.loads(linha)
            idx[d["numeroRegistro"]] = d

    amostras = []
    pdfs_dir = RAW_DIR / "pdfs"
    for reg in sorted(os.listdir(pdfs_dir)):
        pdf = pdfs_dir / reg / "paciente.pdf"
        if not pdf.exists():
            continue
        ext = extract_text(pdf)
        if ext["erro"] or ext["provavel_scan"]:
            continue
        texto = split_primeira_bula(ext["text"])
        seg = segment(texto)
        if not seg["identificacao"] or len(seg["identificacao"]) < 100:
            continue
        nome = idx.get(reg, {}).get("nomeProdutoBulario") or idx.get(reg, {}).get("nomeProdutoCSV") or "?"
        amostras.append((reg, nome, seg["identificacao"]))
        if len(amostras) >= n:
            break

    return amostras


def chamar_provider(provider: str, identificacao: str) -> tuple[dict, float, int, int]:
    """Chama um provider e retorna (resultado, latência_s, tokens_in, tokens_out)."""
    from meta_llm import _build_messages, _cfg, _PROVIDERS, _parse_resposta
    from openai import OpenAI

    # força o provider para este call
    orig = os.environ.get("LLM_PROVIDER")
    os.environ["LLM_PROVIDER"] = provider

    cfg = _cfg()
    kwargs = {"api_key": cfg["api_key"]}
    if cfg["base_url"]:
        kwargs["base_url"] = cfg["base_url"]

    client = OpenAI(**kwargs)
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=cfg["model"],
        messages=_build_messages(identificacao),
        max_tokens=512,
        temperature=0,
    )
    latencia = time.monotonic() - t0

    if orig is None:
        del os.environ["LLM_PROVIDER"]
    else:
        os.environ["LLM_PROVIDER"] = orig

    resultado = _parse_resposta(resp.choices[0].message.content or "")
    tok_in = resp.usage.prompt_tokens if resp.usage else 0
    tok_out = resp.usage.completion_tokens if resp.usage else 0
    return resultado, latencia, tok_in, tok_out


def campos_preenchidos(meta: dict) -> int:
    return sum(1 for v in meta.values() if v is not None and str(v).strip())


def custo_estimado(provider: str, tok_in: int, tok_out: int) -> float:
    from process.meta_llm import _PROVIDERS
    p = _PROVIDERS[provider]
    return (tok_in * p["price_in"] + tok_out * p["price_out"]) / 1_000_000


def eta_dataset(provider: str, n_total: int, avg_tok_total: float) -> str:
    rl = RATE_LIMITS[provider]
    # req/min efetivos: mínimo entre RPM e o que cabe no TPM
    rpm_by_rpm = rl["rpm"]
    rpm_by_tpm = rl["tpm"] / max(avg_tok_total, 1)
    rpm_efetivo = min(rpm_by_rpm, rpm_by_tpm)
    limitante = "RPM" if rpm_by_rpm <= rpm_by_tpm else "TPM"

    minutos = n_total / rpm_efetivo

    # Cerebras: TPD é o limitante real (1M tokens/dia com modelo de raciocínio)
    if provider == "cerebras" and rl.get("tpd"):
        req_por_dia = rl["tpd"] / max(avg_tok_total, 1)
        if req_por_dia < n_total:
            dias = n_total / req_por_dia
            return f"~{dias:.1f} dias (TPD)"

    if minutos < 60:
        return f"~{minutos:.0f} min ({limitante})"
    return f"~{minutos/60:.1f} h ({limitante})"


def benchmark(providers: list[str], n: int) -> None:
    print(f"\nCarregando {n} amostras de bulas...")
    amostras = carregar_amostras(n)
    print(f"  {len(amostras)} amostras prontas\n")

    # cada provider → lista de métricas por amostra
    resultados: dict[str, list[dict]] = {p: [] for p in providers}

    for i, (reg, nome, identificacao) in enumerate(amostras):
        print(f"[{i+1:02d}/{len(amostras)}] {reg} — {nome[:45]}")
        for provider in providers:
            try:
                meta, lat, tok_in, tok_out = chamar_provider(provider, identificacao)
                preenchidos = campos_preenchidos(meta)
                custo = custo_estimado(provider, tok_in, tok_out)
                resultados[provider].append({
                    "reg": reg,
                    "latencia": lat,
                    "tok_in": tok_in,
                    "tok_out": tok_out,
                    "custo": custo,
                    "preenchidos": preenchidos,
                    "meta": meta,
                    "ok": True,
                })
                print(f"    {provider:10s} {lat:.2f}s  {preenchidos}/8 campos  tok={tok_in}+{tok_out}")
            except Exception as exc:
                print(f"    {provider:10s} ERRO: {exc}")
                resultados[provider].append({"ok": False, "erro": str(exc)})

            # pausa respeitando rate limits mais restritivos
            if provider == "cerebras":
                time.sleep(13)   # 5 RPM → 12s entre requests + margem
            elif provider == "groq":
                time.sleep(2.5)  # 30 RPM → 2s entre requests + margem
            else:
                time.sleep(0.3)

        print()

    # -----------------------------------------------------------------------
    # Relatório final
    # -----------------------------------------------------------------------
    N_DATASET = 5285  # PDFs disponíveis

    print("=" * 70)
    print("RESUMO DO BENCHMARK")
    print("=" * 70)

    for provider in providers:
        dados = [d for d in resultados[provider] if d.get("ok")]
        if not dados:
            print(f"\n{provider.upper()}: sem dados (todos falharam)")
            continue

        avg_lat = sum(d["latencia"] for d in dados) / len(dados)
        avg_in  = sum(d["tok_in"] for d in dados) / len(dados)
        avg_out = sum(d["tok_out"] for d in dados) / len(dados)
        avg_campos = sum(d["preenchidos"] for d in dados) / len(dados)
        total_custo_amostra = sum(d["custo"] for d in dados)

        from process.meta_llm import _PROVIDERS
        model = _PROVIDERS[provider]["model"]
        avg_tok_total = avg_in + avg_out
        eta = eta_dataset(provider, N_DATASET, avg_tok_total)
        custo_total = total_custo_amostra / len(dados) * N_DATASET

        print(f"\n{'─'*70}")
        print(f"  Provider : {provider}  ({model})")
        print(f"  Latência : {avg_lat:.2f}s / req")
        print(f"  Tokens   : {avg_in:.0f} in + {avg_out:.0f} out (média)")
        print(f"  Campos   : {avg_campos:.1f}/8 preenchidos (média)")
        print(f"  Custo    : ${total_custo_amostra:.4f} nesta amostra")
        print(f"  Custo    : ~${custo_total:.2f} dataset completo ({N_DATASET} bulas)")
        print(f"  ETA full : {eta} (respeitando rate limits)")

        rl = RATE_LIMITS[provider]
        print(f"  Rate lim : {rl['rpm']} RPM / {rl['tpm']:,} TPM", end="")
        if rl.get("rpd"):
            print(f" / {rl['rpd']:,} RPD", end="")
        print()

    print(f"\n{'─'*70}")
    print("\nDETALHE POR BULA (nome_comercial extraído)")
    print(f"{'registro':<12} {'nome CSV (esperado)':<30}", end="")
    for p in providers:
        print(f"  {p:<20}", end="")
    print()

    for i, (reg, nome, _) in enumerate(amostras):
        print(f"{reg:<12} {nome[:29]:<30}", end="")
        for p in providers:
            d = resultados[p][i] if i < len(resultados[p]) else {}
            if d.get("ok"):
                val = (d["meta"].get("nome_comercial") or "—")[:19]
                print(f"  {val:<20}", end="")
            else:
                print(f"  {'ERRO':<20}", end="")
        print()

    # salva JSON completo para inspeção
    out = Path("dataset/work_data/benchmark_providers.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "amostras": [{"reg": r, "nome": n} for r, n, _ in amostras],
        "resultados": {
            p: [
                {k: v for k, v in d.items() if k != "meta"}
                | {"meta": d.get("meta", {})}
                for d in resultados[p]
            ]
            for p in providers
        },
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nResultados completos → {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10,
                        help="Número de bulas a testar (padrão: 10)")
    parser.add_argument("--providers", nargs="+",
                        default=["openai", "groq", "cerebras"],
                        choices=["openai", "groq", "cerebras"],
                        help="Providers a testar")
    args = parser.parse_args()
    benchmark(args.providers, args.n)
