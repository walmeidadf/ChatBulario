"""
Importa os PDFs já baixados para o index.jsonl e checkpoint do coletor,
evitando re-download.

Regra de mapeamento:
  reg13 (nome do arquivo, 13d CMED) -> reg9 (9d) = reg13[:9]
  reg9 casa com NUMERO_REGISTRO_PRODUTO do DADOS_ABERTOS e com
  numeroRegistro da API do bulário.

Uso:
    uv run python import_existing.py
"""

import csv
import json
import re
from pathlib import Path

CMED_CSV = Path("dataset/raw_data/anvisa_tables/medicamentos_anvisa_dez_2023.csv")
DA_CSV   = Path("dataset/raw_data/anvisa_tables/DADOS_ABERTOS_MEDICAMENTOS.csv")
PDF_ROOT = Path("dataset/raw_data/anvisa_medicine_leaflets/pdfs")
INDEX    = Path("dataset/raw_data/anvisa_medicine_leaflets/index.jsonl")
CHECKPOINT = Path("dataset/raw_data/anvisa_medicine_leaflets/checkpoint.json")


def load_cmed():
    d = {}
    with open(CMED_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            reg13 = row["REGISTRO"].strip()
            if reg13:
                d[reg13] = {
                    "produto": row["PRODUTO"].strip(),
                    "substancia": row["SUBSTÂNCIA"].strip(),
                    "classe": row["CLASSE TERAPÊUTICA"].strip(),
                }
    return d


def load_dados_abertos():
    d = {}
    with open(DA_CSV, encoding="latin-1") as f:
        for row in csv.DictReader(f, delimiter=";"):
            reg9 = row["NUMERO_REGISTRO_PRODUTO"].strip()
            if reg9:
                d[reg9] = {
                    "categoriaRegulatoria": row["CATEGORIA_REGULATORIA"].strip(),
                    "principioAtivo": row["PRINCIPIO_ATIVO"].strip(),
                    "classeTerapeutica": row["CLASSE_TERAPEUTICA"].strip(),
                    "situacao": row["SITUACAO_REGISTRO"].strip(),
                    "nomeProdutoDA": row["NOME_PRODUTO"].strip(),
                }
    return d


def main():
    cmed = load_cmed()
    dados = load_dados_abertos()

    # carrega checkpoint e index já existentes para não duplicar
    done = set()
    if CHECKPOINT.exists():
        done = set(json.loads(CHECKPOINT.read_text())["done"])

    indexed = set()
    if INDEX.exists():
        for line in INDEX.read_text(encoding="utf-8").splitlines():
            if line.strip():
                indexed.add(json.loads(line).get("numeroRegistro", ""))

    pdfs = sorted(PDF_ROOT.rglob("bula_paciente_*.pdf"))
    novos = importados = ja_existia = 0

    entries = []
    for pdf in pdfs:
        m = re.search(r"bula_paciente_(\d+)\.pdf", pdf.name)
        if not m:
            continue
        reg13 = m.group(1)
        reg9  = reg13[:9]

        if reg9 in done or reg9 in indexed:
            ja_existia += 1
            continue

        meta_cmed = cmed.get(reg13, {})
        meta_da   = dados.get(reg9, {})

        # procura se há também bula profissional na mesma pasta
        prof_pdf = pdf.parent / pdf.name.replace("paciente", "profissional")

        entry = {
            "status": "ok",
            "source": "import_existing",
            "numeroRegistro": reg9,
            "reg13_cmed": reg13,
            "nomeProdutoBulario": None,          # não consultamos a API aqui
            "nomeProdutoCSV": (
                meta_da.get("nomeProdutoDA") or meta_cmed.get("produto")
            ),
            "expediente": None,
            "categoriaRegulatoria": meta_da.get("categoriaRegulatoria")
                                    or meta_cmed.get("classe"),
            "principioAtivo": meta_da.get("principioAtivo")
                              or meta_cmed.get("substancia"),
            "classeTerapeutica": meta_da.get("classeTerapeutica")
                                 or meta_cmed.get("classe"),
            "idBulaPaciente": None,
            "pdfPaciente": str(pdf),
        }
        entries.append(entry)
        done.add(reg9)
        novos += 1

    # grava
    with INDEX.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    CHECKPOINT.write_text(json.dumps({"done": list(done)}))

    print(f"PDFs encontrados   : {len(pdfs)}")
    print(f"Importados agora   : {novos}")
    print(f"Já estavam no index: {ja_existia}")
    print(f"Total no checkpoint: {len(done)}")
    print(f"Index: {INDEX}")


if __name__ == "__main__":
    main()
