"""
Estágio A — segmentação: PDF → segments.jsonl + qc.jsonl

Sem LLM. Re-rodável à vontade ao tunar process/segment.py.
Pula registros já em segments.jsonl (retomável).

Flags:
  --limite N    Processa só os N primeiros
  --re-run      Reprocessa mesmo os já segmentados
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "process"))

from extract import extract_text      # noqa: E402
from segment import segment           # noqa: E402
from split import split_primeira_bula # noqa: E402

RAW_DIR = Path("dataset/raw_data/anvisa_medicine_leaflets")
WORK_DIR = Path("dataset/work_data")
INDEX    = RAW_DIR / "index.jsonl"
SEGMENTS_OUT = WORK_DIR / "segments.jsonl"
QC_OUT       = WORK_DIR / "qc.jsonl"


def carregar_index() -> list[dict]:
    out = []
    with INDEX.open() as f:
        for l in f:
            l = l.strip()
            if l:
                out.append(json.loads(l))
    return out


def pdf_path(e: dict) -> Path | None:
    p = RAW_DIR / "pdfs" / e.get("numeroRegistro", "") / "paciente.pdf"
    return p if p.exists() else None


def ja_segmentados() -> set[str]:
    if not SEGMENTS_OUT.exists():
        return set()
    regs: set[str] = set()
    with SEGMENTS_OUT.open() as f:
        for l in f:
            try:
                regs.add(json.loads(l)["registro"])
            except (json.JSONDecodeError, KeyError):
                pass
    return regs


def main() -> None:
    ap = argparse.ArgumentParser(description="Segmenta todos os PDFs → segments.jsonl")
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument("--re-run", action="store_true", help="Reprocessa já segmentados")
    args = ap.parse_args()

    WORK_DIR.mkdir(parents=True, exist_ok=True)

    entradas = carregar_index()
    disponiveis = [(e, pdf_path(e)) for e in entradas if pdf_path(e)]
    print(f"index.jsonl: {len(entradas)} registros | PDFs disponíveis: {len(disponiveis)}")

    if not args.re_run:
        ja = ja_segmentados()
        disponiveis = [(e, p) for e, p in disponiveis if e.get("numeroRegistro") not in ja]
        print(f"A segmentar (novos): {len(disponiveis)}")
    else:
        print(f"--re-run: reprocessando todos os {len(disponiveis)}")

    if args.limite:
        disponiveis = disponiveis[:args.limite]
        print(f"Limitado a {args.limite}")

    if not disponiveis:
        print("Nada a segmentar.")
        return

    total = len(disponiveis)
    n_ok = n_scan = n_erro = n_completas = 0

    with SEGMENTS_OUT.open("a") as f_seg, QC_OUT.open("a") as f_qc:
        for i, (entrada, p) in enumerate(disponiveis, 1):
            reg = entrada.get("numeroRegistro", "")
            ext = extract_text(p)

            if ext["erro"] or ext["provavel_scan"]:
                flag = "scan" if ext["provavel_scan"] else f"erro={ext['erro']}"
                print(f"  skip {reg}: {flag}")
                n_scan += bool(ext["provavel_scan"])
                n_erro += bool(ext["erro"])
                entry = {
                    "registro": reg,
                    "erro": ext["erro"],
                    "provavel_scan": ext["provavel_scan"],
                    "secoes_encontradas": 0,
                    "secoes": [],
                    "identificacao": "",
                    "n_caracteres": ext.get("n_caracteres", 0),
                    "n_paginas": ext.get("n_paginas", 0),
                }
                f_seg.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f_seg.flush()
                continue

            texto = split_primeira_bula(ext["text"])
            seg = segment(texto)

            nome = (entrada.get("nomeProduto")
                    or entrada.get("nomeProdutoCSV")
                    or entrada.get("nomeProdutoBulario"))

            entry = {
                "registro": reg,
                "nomeProduto": nome,
                "categoriaRegulatoria": entrada.get("categoriaRegulatoria"),
                "principioAtivo": entrada.get("principioAtivo"),
                "classeTerapeutica": entrada.get("classeTerapeutica"),
                "expediente": entrada.get("expediente"),
                "identificacao": seg["identificacao"],
                "secoes": seg["secoes"],
                "secoes_encontradas": seg["secoes_encontradas"],
                "dizeres_legais": seg.get("dizeres_legais"),
                "n_caracteres": ext["n_caracteres"],
                "n_paginas": ext["n_paginas"],
                "provavel_scan": False,
                "erro": None,
            }
            qc = {
                "registro": reg,
                "secoes_encontradas": seg["secoes_encontradas"],
                "secoes_faltantes": [n for n in range(1, 10)
                                     if n not in {s["id"] for s in seg["secoes"]}],
                "completa": seg["secoes_encontradas"] == 9,
                "n_caracteres": ext["n_caracteres"],
                "n_paginas": ext["n_paginas"],
            }

            f_seg.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f_seg.flush()
            f_qc.write(json.dumps(qc, ensure_ascii=False) + "\n")
            f_qc.flush()

            n_ok += 1
            if seg["secoes_encontradas"] == 9:
                n_completas += 1

            if i % 200 == 0 or i == total:
                pct = 100 * n_completas // max(n_ok, 1)
                print(f"  {i}/{total} | 9/9: {n_completas} ({pct}%) "
                      f"| skip: {n_scan + n_erro}", flush=True)

    print(f"\nConcluído: {n_ok} segmentadas, {n_scan} scans, {n_erro} erros")
    print(f"  9/9 completas: {n_completas} ({100 * n_completas // max(n_ok, 1)}%)")
    print(f"  output: {SEGMENTS_OUT}")
    print(f"\nPróximo passo: uv run python enrich_all.py --async")


if __name__ == "__main__":
    main()
