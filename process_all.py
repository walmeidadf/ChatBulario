"""
Pipeline completo: PDFs -> dataset flat (um registro × pergunta por linha).

Lê index.jsonl para saber quais PDFs estão disponíveis, processa cada um
via process/structure.py (extract + split + segment + meta_llm) e produz:

  dataset/work_data/dataset.jsonl   — uma linha por (registro × pergunta)
  dataset/work_data/qc.jsonl        — uma linha por registro com dados de QC

Flags:
  --sem-llm       pula extração LLM de metadados (mais rápido, para testes)
  --limite N      processa apenas os N primeiros registros
  --re-run        reprocessa mesmo registros já presentes no output
  --concorrencia N número de workers paralelos para LLM (padrão: 20)
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# permite importar módulos de process/ sem instalar como pacote
sys.path.insert(0, str(Path(__file__).parent / "process"))

from extract import extract_text  # noqa: E402
from meta_llm import extract_meta_llm_stream  # noqa: E402
from segment import segment  # noqa: E402
from split import split_primeira_bula  # noqa: E402

RAW_DIR = Path("dataset/raw_data/anvisa_medicine_leaflets")
WORK_DIR = Path("dataset/work_data")
INDEX = RAW_DIR / "index.jsonl"
DATASET_OUT = WORK_DIR / "dataset.jsonl"
QC_OUT = WORK_DIR / "qc.jsonl"


def carregar_index() -> list[dict]:
    entradas = []
    with INDEX.open() as f:
        for linha in f:
            linha = linha.strip()
            if linha:
                entradas.append(json.loads(linha))
    return entradas


def pdf_path(entrada: dict) -> Path | None:
    reg = entrada.get("numeroRegistro", "")
    p = RAW_DIR / "pdfs" / reg / "paciente.pdf"
    return p if p.exists() else None


def ja_processados(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    regs = set()
    with out_path.open() as f:
        for linha in f:
            try:
                d = json.loads(linha)
                regs.add(d.get("registro", ""))
            except json.JSONDecodeError:
                pass
    return regs


def processar_texto(texto: str, entrada: dict, meta_llm: dict) -> tuple[list[dict], dict]:
    """Transforma texto + meta numa lista de linhas de dataset e uma linha de QC."""
    seg = segment(texto)
    encontradas = {s["id"] for s in seg["secoes"]}
    faltantes = [n for n in range(1, 10) if n not in encontradas]

    registro = entrada.get("numeroRegistro", "")
    meta_base = {
        "registro": registro,
        "nome_produto": (
            meta_llm.get("nome_comercial")
            or entrada.get("nomeProdutoBulario")
            or entrada.get("nomeProdutoCSV")
        ),
        "categoria_regulatoria": entrada.get("categoriaRegulatoria"),
        "principio_ativo_csv": entrada.get("principioAtivo"),
        "classe_terapeutica": entrada.get("classeTerapeutica"),
        "expediente": entrada.get("expediente"),
        # LLM
        "nome_comercial": meta_llm.get("nome_comercial"),
        "fabricante": meta_llm.get("fabricante"),
        "principio_ativo": meta_llm.get("principio_ativo"),
        "forma_farmaceutica": meta_llm.get("forma_farmaceutica"),
        "via_administracao": meta_llm.get("via_administracao"),
        "apresentacao": meta_llm.get("apresentacao"),
        "composicao": meta_llm.get("composicao"),
        "uso": meta_llm.get("uso"),
    }

    linhas_dataset = []
    for secao in seg["secoes"]:
        linhas_dataset.append({
            **meta_base,
            "secao_id": secao["id"],
            "pergunta": secao["pergunta"],
            "resposta": secao["resposta"],
            "fuzzy_score": secao["score"],
        })

    qc = {
        "registro": registro,
        "secoes_encontradas": seg["secoes_encontradas"],
        "secoes_faltantes": faltantes,
        "completa": seg["secoes_encontradas"] == 9,
    }

    return linhas_dataset, qc


async def main(args: argparse.Namespace) -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    entradas = carregar_index()
    print(f"index.jsonl: {len(entradas)} registros")

    # filtra para registros com PDF disponível
    disponiveis = [(e, pdf_path(e)) for e in entradas if pdf_path(e)]
    print(f"PDFs disponíveis: {len(disponiveis)}")

    if not args.re_run:
        ja_feitos = ja_processados(DATASET_OUT)
        disponiveis = [(e, p) for e, p in disponiveis if e.get("numeroRegistro") not in ja_feitos]
        print(f"A processar (novos): {len(disponiveis)}")

    if args.limite:
        disponiveis = disponiveis[:args.limite]
        print(f"Limitado a {args.limite} registros")

    if not disponiveis:
        print("Nada a processar.")
        return

    # 1) extração de texto (CPU — síncrona, rápida)
    print("Extraindo texto dos PDFs...")
    textos_extraidos = []
    entradas_validas = []
    for entrada, p in disponiveis:
        ext = extract_text(p)
        if ext["erro"] or ext["provavel_scan"]:
            print(f"  skip {entrada['numeroRegistro']}: erro={ext['erro']} scan={ext['provavel_scan']}")
            continue
        textos_extraidos.append(split_primeira_bula(ext["text"]))
        entradas_validas.append((entrada, ext))

    print(f"Textos extraídos com sucesso: {len(textos_extraidos)}")

    # 2+3) extração LLM + segmentação + escrita incremental do output
    #
    # Cada bula é gravada assim que fica pronta (flush por item), com progresso
    # impresso periodicamente. Se o run for interrompido, o que já foi escrito
    # está salvo — e na próxima execução esses registros são pulados.
    total = len(entradas_validas)
    n_linhas = 0
    n_completas = 0
    n_feitas = 0

    def escrever(idx: int, meta_llm: dict, f_ds, f_qc) -> None:
        nonlocal n_linhas, n_completas
        entrada, ext = entradas_validas[idx]
        linhas, qc = processar_texto(textos_extraidos[idx], entrada, meta_llm)
        qc["n_caracteres"] = ext["n_caracteres"]
        qc["n_paginas"] = ext["n_paginas"]
        for linha in linhas:
            f_ds.write(json.dumps(linha, ensure_ascii=False) + "\n")
        f_qc.write(json.dumps(qc, ensure_ascii=False) + "\n")
        f_ds.flush()
        f_qc.flush()
        n_linhas += len(linhas)
        if qc["completa"]:
            n_completas += 1

    with DATASET_OUT.open("a") as f_ds, QC_OUT.open("a") as f_qc:
        if args.sem_llm:
            print("LLM ignorado (--sem-llm)")
            for idx in range(total):
                escrever(idx, {}, f_ds, f_qc)
                n_feitas += 1
                if n_feitas % 100 == 0 or n_feitas == total:
                    print(f"  {n_feitas}/{total} bulas ({n_linhas} linhas)", flush=True)
        else:
            print(f"Extraindo metadados com LLM (concorrência={args.concorrencia})...")
            identificacoes = [segment(t)["identificacao"] for t in textos_extraidos]
            async for idx, meta_llm in extract_meta_llm_stream(
                identificacoes, concorrencia=args.concorrencia
            ):
                escrever(idx, meta_llm, f_ds, f_qc)
                n_feitas += 1
                if n_feitas % 25 == 0 or n_feitas == total:
                    print(f"  LLM {n_feitas}/{total} bulas ({n_linhas} linhas)", flush=True)

    print(f"\nConcluído: {total} bulas processadas")
    print(f"  completas (9/9 seções): {n_completas} ({100*n_completas//max(total,1)}%)")
    print(f"  linhas no dataset: {n_linhas}")
    print(f"  output: {DATASET_OUT}")
    print(f"  QC:     {QC_OUT}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Processa todas as bulas coletadas.")
    parser.add_argument("--sem-llm", action="store_true",
                        help="Pula extração LLM de metadados")
    parser.add_argument("--limite", type=int, default=None,
                        help="Processa apenas os N primeiros registros")
    parser.add_argument("--re-run", action="store_true",
                        help="Reprocessa registros já presentes no output")
    parser.add_argument("--concorrencia", type=int, default=20,
                        help="Workers paralelos para LLM (padrão: 20)")
    args = parser.parse_args()

    asyncio.run(main(args))
