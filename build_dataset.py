"""
Estágio C — build: segments.jsonl ⋈ meta.jsonl → dataset.jsonl

Determinístico: sempre reescreve dataset.jsonl do zero.
dataset.jsonl é um artefato derivado — nunca editar à mão.

Requer: segments.jsonl e meta.jsonl já gerados.
"""
import json
import sys
from pathlib import Path

WORK_DIR = Path("dataset/work_data")
SEGMENTS = WORK_DIR / "segments.jsonl"
META     = WORK_DIR / "meta.jsonl"
DATASET_OUT = WORK_DIR / "dataset.jsonl"


def carregar_meta() -> dict[str, dict]:
    meta: dict[str, dict] = {}
    if not META.exists():
        print("Aviso: meta.jsonl não encontrado — metadados LLM ficarão nulos.")
        return meta
    with META.open() as f:
        for l in f:
            try:
                d = json.loads(l)
                meta[d["registro"]] = d
            except (json.JSONDecodeError, KeyError):
                pass
    return meta


def main() -> None:
    if not SEGMENTS.exists():
        print("segments.jsonl não encontrado. Rode segment_all.py primeiro.")
        sys.exit(1)

    meta_map = carregar_meta()
    print(f"segments.jsonl encontrado | meta.jsonl: {len(meta_map)} registros")

    n_linhas = n_bulas = n_completas = n_sem_meta = 0

    with DATASET_OUT.open("w") as f_out, SEGMENTS.open() as f_seg:
        for linha in f_seg:
            try:
                seg = json.loads(linha)
            except json.JSONDecodeError:
                continue

            if not seg.get("secoes"):
                continue

            reg = seg["registro"]
            meta = meta_map.get(reg, {})
            if not meta:
                n_sem_meta += 1

            meta_base = {
                "registro": reg,
                "nome_produto": meta.get("nome_comercial") or seg.get("nomeProduto"),
                "categoria_regulatoria": seg.get("categoriaRegulatoria"),
                "principio_ativo_csv": seg.get("principioAtivo"),
                "classe_terapeutica": seg.get("classeTerapeutica"),
                "expediente": seg.get("expediente"),
                # campos LLM
                "nome_comercial":    meta.get("nome_comercial"),
                "fabricante":        meta.get("fabricante"),
                "principio_ativo":   meta.get("principio_ativo"),
                "forma_farmaceutica": meta.get("forma_farmaceutica"),
                "via_administracao": meta.get("via_administracao"),
                "apresentacao":      meta.get("apresentacao"),
                "composicao":        meta.get("composicao"),
                "uso":               meta.get("uso"),
            }

            for secao in seg["secoes"]:
                f_out.write(json.dumps({
                    **meta_base,
                    "secao_id":    secao["id"],
                    "pergunta":    secao["pergunta"],
                    "resposta":    secao["resposta"],
                    "fuzzy_score": secao["score"],
                }, ensure_ascii=False) + "\n")
                n_linhas += 1

            n_bulas += 1
            if seg.get("secoes_encontradas") == 9:
                n_completas += 1

    pct_c = 100 * n_completas // max(n_bulas, 1)
    pct_m = 100 * n_sem_meta  // max(n_bulas, 1)
    print(f"\nConcluído: {n_bulas} bulas → {n_linhas} linhas")
    print(f"  9/9 completas:  {n_completas} ({pct_c}%)")
    print(f"  sem meta LLM:   {n_sem_meta} ({pct_m}%)")
    print(f"  output: {DATASET_OUT}")


if __name__ == "__main__":
    main()
