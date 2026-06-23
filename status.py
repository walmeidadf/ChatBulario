"""
Mostra o progresso da coleta em relação ao universo de ativos com registro.

Uso:
    uv run python status.py
"""

import csv
import json
from pathlib import Path

DA_CSV     = Path("dataset/raw_data/anvisa_tables/DADOS_ABERTOS_MEDICAMENTOS.csv")
CHECKPOINT = Path("dataset/raw_data/anvisa_medicine_leaflets/checkpoint.json")
INDEX      = Path("dataset/raw_data/anvisa_medicine_leaflets/index.jsonl")


def main():
    # universo: ativos com NUMERO_REGISTRO_PRODUTO preenchido
    universo = {}
    with open(DA_CSV, encoding="latin-1") as f:
        for row in csv.DictReader(f, delimiter=";"):
            reg = row["NUMERO_REGISTRO_PRODUTO"].strip()
            if reg and row["SITUACAO_REGISTRO"] == "Ativo":
                universo[reg] = row["CATEGORIA_REGULATORIA"].strip()

    total = len(universo)

    # coletados: checkpoint
    done = set()
    if CHECKPOINT.exists():
        done = set(json.loads(CHECKPOINT.read_text())["done"])

    # breakdown por status no index
    com_pdf = sem_bula = importado = 0
    if INDEX.exists():
        for line in INDEX.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("source") == "import_existing":
                importado += 1
            elif e.get("status") == "ok":
                com_pdf += 1
            elif e.get("status") == "sem_bula":
                sem_bula += 1

    coletados   = len(done)
    faltam      = total - coletados
    pct         = coletados / total * 100 if total else 0

    # breakdown do universo por categoria
    from collections import Counter
    cats = Counter(universo.values())

    print("=" * 50)
    print("PROGRESSO DA COLETA — Bulário ANVISA")
    print("=" * 50)
    print(f"Universo (ativos c/ registro): {total:>6}")
    print(f"Coletados (checkpoint)        : {coletados:>6}  ({pct:.1f}%)")
    print(f"  └ importados (PDFs antigos) : {importado:>6}")
    print(f"  └ coletados agora (c/ PDF)  : {com_pdf:>6}")
    print(f"  └ sem bula no bulário       : {sem_bula:>6}")
    print(f"Faltam                        : {faltam:>6}")
    print()
    bar_len = 40
    filled = int(bar_len * pct / 100)
    print(f"[{'█' * filled}{'░' * (bar_len - filled)}] {pct:.1f}%")
    print()
    print("Universo por categoria regulatória:")
    for cat, n in cats.most_common(8):
        label = cat if cat else "(sem categoria)"
        print(f"  {label:<30} {n:>5}")


if __name__ == "__main__":
    main()
