"""
Estágio D — export: dataset.jsonl → parquet com splits + upload HuggingFace.

Input:  dataset/work_data/dataset.jsonl  (flat, 1 linha por registro × pergunta)
Output: dataset/work_data/hf_dataset/{train,validation,test}.parquet

Config único e flat: cada linha é um par pergunta/resposta carregando os
metadados da bula denormalizados. Split 80/10/10 agrupado por `registro`
(as 9 perguntas de uma bula ficam sempre no mesmo split — sem vazamento),
estratificado por `classe_terapeutica`. Classes com < 10 bulas vão inteiras
para treino.

Uso:
  uv run python export.py
  uv run python export.py --upload          # gera parquets e envia ao HF Hub
  uv run python export.py --input <path> --output-dir <dir>
"""
import argparse
import json
import logging
import os
from collections import Counter
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "dataset" / "work_data"

SPLIT_RATIOS = {"train": 0.80, "validation": 0.10, "test": 0.10}
SEED = 42

# Ordem canônica das colunas no parquet final
COLUMNS = [
    # identificação
    "registro", "nome_produto",
    # metadados do CSV (ANVISA dados abertos)
    "categoria_regulatoria", "principio_ativo_csv", "classe_terapeutica", "expediente",
    # metadados extraídos pelo LLM
    "nome_comercial", "fabricante", "principio_ativo", "forma_farmaceutica",
    "via_administracao", "apresentacao", "composicao", "uso",
    # par pergunta/resposta
    "secao_id", "pergunta", "resposta", "fuzzy_score",
]


def stratified_group_split(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Split 80/10/10 agrupado por `registro` e estratificado por `classe_terapeutica`.

    A unidade de split é a BULA (registro), não a linha — todas as perguntas de
    uma bula caem no mesmo split. Classes com < 10 bulas vão inteiras para treino
    (estrato pequeno demais para particionar).
    """
    # 1 linha por registro, preservando a classe
    bulas = (df[["registro", "classe_terapeutica"]]
             .drop_duplicates(subset="registro")
             .reset_index(drop=True))
    bulas["_classe"] = bulas["classe_terapeutica"].fillna("SEM_CLASSE")

    train_regs, val_regs, test_regs = [], [], []
    for _classe, group in bulas.groupby("_classe"):
        regs = group["registro"].sample(frac=1, random_state=SEED).tolist()
        n = len(regs)
        if n < 10:
            train_regs.extend(regs)
            continue
        n_val = max(1, int(n * SPLIT_RATIOS["validation"]))
        n_test = max(1, int(n * SPLIT_RATIOS["test"]))
        test_regs.extend(regs[:n_test])
        val_regs.extend(regs[n_test: n_test + n_val])
        train_regs.extend(regs[n_test + n_val:])

    reg_to_split = {}
    for r in train_regs:
        reg_to_split[r] = "train"
    for r in val_regs:
        reg_to_split[r] = "validation"
    for r in test_regs:
        reg_to_split[r] = "test"

    split_col = df["registro"].map(reg_to_split)
    return {
        "train":      df[split_col == "train"].reset_index(drop=True),
        "validation": df[split_col == "validation"].reset_index(drop=True),
        "test":       df[split_col == "test"].reset_index(drop=True),
    }


def print_stats(df: pd.DataFrame, label: str) -> None:
    n_bulas = df["registro"].nunique()
    log.info(f"--- {label} ---")
    log.info(f"  Linhas (Q/A): {len(df):,} | bulas: {n_bulas:,}")
    if "fabricante" in df.columns:
        for campo in ("nome_comercial", "principio_ativo", "fabricante", "composicao"):
            n = df[campo].notna().sum()
            log.info(f"  {campo} preenchido: {100*n/max(len(df),1):.0f}%")


def run(input_path: Path, output_dir: Path, upload: bool = False) -> None:
    with input_path.open(encoding="utf-8") as f:
        records = [json.loads(l) for l in f if l.strip()]
    df = pd.DataFrame(records)

    # Garantir todas as colunas (robustez a amostras parciais) e ordem canônica
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[COLUMNS]

    log.info(f"Registros carregados: {len(df):,} linhas | {df['registro'].nunique():,} bulas")
    print_stats(df, "Dataset completo")

    output_dir.mkdir(parents=True, exist_ok=True)
    splits = stratified_group_split(df)

    # Sanidade: nenhum registro pode aparecer em mais de um split
    reg_sets = {name: set(s["registro"]) for name, s in splits.items()}
    assert not (reg_sets["train"] & reg_sets["test"]), "vazamento train/test!"
    assert not (reg_sets["train"] & reg_sets["validation"]), "vazamento train/val!"
    assert not (reg_sets["validation"] & reg_sets["test"]), "vazamento val/test!"

    for name, split_df in splits.items():
        out = output_dir / f"{name}.parquet"
        split_df.to_parquet(out, index=False, row_group_size=5_000)
        print_stats(split_df, name)
        log.info(f"  → {out.name}")

    if upload:
        _upload_to_hub(output_dir)


def _upload_to_hub(output_dir: Path) -> None:
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("HF_TOKEN")
    if not token:
        log.error("HF_TOKEN não encontrado em .env")
        return

    try:
        from huggingface_hub import HfApi
    except ImportError:
        log.error("huggingface_hub não instalado (uv add huggingface_hub)")
        return

    repo_id = os.getenv("HF_REPO_ID", "walmeidadf/ChatBulario")
    api = HfApi(token=token)
    log.info(f"Upload para {repo_id}…")
    api.create_repo(repo_id, repo_type="dataset", exist_ok=True)

    # Dataset card → README.md no repo
    card_path = BASE_DIR / "dataset_card.md"
    if card_path.exists():
        api.upload_file(
            path_or_fileobj=str(card_path),
            path_in_repo="README.md",
            repo_id=repo_id,
            repo_type="dataset",
        )
        log.info("  Upload: README.md (dataset card)")

    for parquet in sorted(output_dir.glob("*.parquet")):
        api.upload_file(
            path_or_fileobj=str(parquet),
            path_in_repo=f"data/{parquet.name}",
            repo_id=repo_id,
            repo_type="dataset",
        )
        log.info(f"  Upload: data/{parquet.name}")

    log.info("Upload concluído.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Exporta dataset.jsonl para parquet + HuggingFace")
    ap.add_argument("--input", type=Path, default=WORK_DIR / "dataset.jsonl")
    ap.add_argument("--output-dir", type=Path, default=WORK_DIR / "hf_dataset")
    ap.add_argument("--upload", action="store_true",
                    help="Faz upload para o HuggingFace Hub após gerar os parquets")
    args = ap.parse_args()
    run(args.input, args.output_dir, upload=args.upload)


if __name__ == "__main__":
    main()
