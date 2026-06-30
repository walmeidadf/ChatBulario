"""
Script único (one-time): extrai metadados LLM do dataset.jsonl existente → meta.jsonl.

Preserva o trabalho de LLM já pago antes de migrar para o pipeline em 3 estágios.
Rodar uma vez; apaga-se depois.
"""
import json
from pathlib import Path

WORK_DIR = Path("dataset/work_data")
DATASET = WORK_DIR / "dataset.jsonl"
META_OUT = WORK_DIR / "meta.jsonl"

_CAMPOS = ["nome_comercial", "fabricante", "principio_ativo",
           "forma_farmaceutica", "via_administracao", "apresentacao", "composicao", "uso"]


def main():
    if not DATASET.exists():
        print("dataset.jsonl não encontrado.")
        return

    if META_OUT.exists():
        existing = sum(1 for _ in META_OUT.open())
        print(f"meta.jsonl já existe com {existing} registros. Apague-o antes de rodar migrate.")
        return

    # Cada registro tem N linhas (1/seção) com os mesmos metadados — pegamos a primeira.
    visto: dict[str, dict] = {}
    with DATASET.open() as f:
        for linha in f:
            try:
                d = json.loads(linha)
            except json.JSONDecodeError:
                continue
            reg = d.get("registro")
            if not reg or reg in visto:
                continue
            visto[reg] = {c: d.get(c) for c in _CAMPOS}

    with META_OUT.open("w") as f:
        for reg, meta in visto.items():
            f.write(json.dumps({"registro": reg, **meta}, ensure_ascii=False) + "\n")

    print(f"meta.jsonl criado: {len(visto)} registros extraídos do dataset.jsonl existente.")
    print(f"Próximo passo: uv run python segment_all.py")


if __name__ == "__main__":
    main()
