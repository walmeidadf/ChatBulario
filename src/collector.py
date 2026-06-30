"""
Coletor de bulas do Bulário Eletrônico da ANVISA (projeto ChatBulário).

Driver: DADOS_ABERTOS_MEDICAMENTOS.csv  (filter[numeroRegistro] = match exato)
Saída:  dataset/raw_data/anvisa_medicine_leaflets/
          index.jsonl          — uma linha por produto coletado
          pdfs/<reg>/paciente.pdf
          pdfs/<reg>/profissional.pdf

Retomável: salva checkpoint.json a cada CHECKPOINT_EVERY itens.

Uso:
    uv run python collector.py [--limit N] [--only-ativo] [--dry-run]

Flags:
    --limit N       coleta apenas os N primeiros registros (teste)
    --only-ativo    filtra SITUACAO_REGISTRO == Ativo (default True)
    --dry-run       apenas busca metadados, não baixa PDFs
"""

import argparse
import base64
import csv
import json
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE = "https://consultas.anvisa.gov.br"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CSV_PATH = Path("dataset/raw_data/anvisa_tables/DADOS_ABERTOS_MEDICAMENTOS.csv")
OUT_DIR = Path("dataset/raw_data/anvisa_medicine_leaflets")
PDF_DIR = OUT_DIR / "pdfs"
INDEX = OUT_DIR / "index.jsonl"
CHECKPOINT = OUT_DIR / "checkpoint.json"

PACE = 1.5        # segundos entre requisições
JITTER = 0.4      # ±jitter aleatório
CHECKPOINT_EVERY = 100
RETRY_MAX = 4


# ---------------------------------------------------------------------------
# JS helpers (executados dentro do contexto do browser)
# ---------------------------------------------------------------------------
JS_JSON = """
async (url) => {
  try {
    const r = await fetch(url, { headers: { Authorization: 'Guest' } });
    const t = await r.text();
    let b; try { b = JSON.parse(t); } catch (e) { b = null; }
    return { status: r.status, body: b };
  } catch (e) { return { status: -1, body: null }; }
}
"""

JS_PDF = """
async (url) => {
  try {
    const r = await fetch(url, { headers: { Authorization: 'Guest' } });
    if (!r.ok) return { ok: false, status: r.status };
    const buf = await r.arrayBuffer();
    let bin = '', bytes = new Uint8Array(buf), chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk)
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    return { ok: true, status: r.status, b64: btoa(bin) };
  } catch (e) { return { ok: false, status: -1 }; }
}
"""


# ---------------------------------------------------------------------------
# Funções de requisição com retry/backoff
# ---------------------------------------------------------------------------
def fetch_json(page, path):
    url = BASE + path
    for attempt in range(RETRY_MAX):
        r = page.evaluate(JS_JSON, url)
        if r["status"] == 200 and isinstance(r["body"], dict):
            return r["body"]
        wait = (2 ** attempt) + random.random()
        time.sleep(wait)
    return None


def fetch_pdf(page, id_bula):
    path = f"/api/consulta/medicamentos/arquivo/bula/parecer/{id_bula}/?Authorization="
    url = BASE + path
    for attempt in range(RETRY_MAX):
        r = page.evaluate(JS_PDF, url)
        if r.get("ok") and r.get("b64"):
            return base64.b64decode(r["b64"])
        wait = (2 ** attempt) + random.random()
        time.sleep(wait)
    return None


def pace():
    time.sleep(PACE + random.uniform(-JITTER, JITTER))


# ---------------------------------------------------------------------------
# Carrega CSV driver
# ---------------------------------------------------------------------------
def load_registros(only_ativo=True):
    rows = []
    with open(CSV_PATH, encoding="latin-1") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if only_ativo and row["SITUACAO_REGISTRO"] != "Ativo":
                continue
            reg = row["NUMERO_REGISTRO_PRODUTO"].strip()
            if not reg:
                continue
            rows.append({
                "numeroRegistro": reg,
                "nomeProduto": row["NOME_PRODUTO"].strip(),
                "categoriaRegulatoria": row["CATEGORIA_REGULATORIA"].strip(),
                "principioAtivo": row["PRINCIPIO_ATIVO"].strip(),
                "classeTerapeutica": row["CLASSE_TERAPEUTICA"].strip(),
                "situacao": row["SITUACAO_REGISTRO"].strip(),
            })
    return rows


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------
def load_checkpoint():
    if CHECKPOINT.exists():
        return set(json.loads(CHECKPOINT.read_text())["done"])
    return set()


def save_checkpoint(done: set):
    CHECKPOINT.write_text(json.dumps({"done": list(done)}))


# ---------------------------------------------------------------------------
# Coleta de um produto
# ---------------------------------------------------------------------------
def collect_one(page, meta, dry_run=False):
    reg = meta["numeroRegistro"]
    body = fetch_json(
        page,
        f"/api/consulta/bulario?count=1&page=1&filter[numeroRegistro]={reg}",
    )
    pace()

    if not body or not body.get("content"):
        return {"status": "sem_bula", **meta}

    item = body["content"][0]
    id_pac = item.get("idBulaPacienteProtegido")
    entry = {
        "status": "ok",
        "numeroRegistro": reg,
        "nomeProdutoBulario": item.get("nomeProduto"),
        "nomeProdutoCSV": meta["nomeProduto"],
        "expediente": item.get("expediente"),
        "categoriaRegulatoria": meta["categoriaRegulatoria"],
        "principioAtivo": meta["principioAtivo"],
        "classeTerapeutica": meta["classeTerapeutica"],
        "idBulaPaciente": id_pac,
        "pdfPaciente": None,
    }

    if dry_run:
        return entry

    pdf_dir = PDF_DIR / reg
    pdf_dir.mkdir(parents=True, exist_ok=True)

    if id_pac:
        pdf = fetch_pdf(page, id_pac)
        pace()
        if pdf:
            dest = pdf_dir / "paciente.pdf"
            dest.write_bytes(pdf)
            entry["pdfPaciente"] = str(dest)

    return entry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-ativo", action="store_true", default=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    registros = load_registros(only_ativo=args.only_ativo)
    if args.limit:
        registros = registros[: args.limit]

    done = load_checkpoint()
    pendentes = [r for r in registros if r["numeroRegistro"] not in done]

    print(f"Registros no CSV (ativos): {len(registros)}")
    print(f"Já coletados (checkpoint): {len(done)}")
    print(f"A coletar agora:           {len(pendentes)}")
    if args.dry_run:
        print("(dry-run: sem download de PDF)")
    eta = len(pendentes) * (PACE * (1 if args.dry_run else 2)) / 3600
    print(f"ETA estimado:              {eta:.1f}h\n")

    contadores = {"ok": 0, "sem_bula": 0, "erro": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(user_agent=UA, locale="pt-BR")
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = ctx.new_page()

        print("Abrindo browser e aguardando Cloudflare...")
        page.goto(f"{BASE}/#/bulario/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)

        # sanity check
        chk = fetch_json(page, "/api/consulta/bulario?count=1&page=1&filter[nomeProduto]=dipirona")
        if not chk:
            print("!! Canal bloqueado após boot. Abortando.")
            browser.close()
            return 1
        print("Canal ok. Iniciando coleta...\n")

        index_fh = INDEX.open("a", encoding="utf-8")

        for i, meta in enumerate(pendentes, 1):
            reg = meta["numeroRegistro"]
            try:
                entry = collect_one(page, meta, dry_run=args.dry_run)
            except Exception as exc:
                entry = {"status": "erro", "erro": str(exc), **meta}
                contadores["erro"] += 1
            else:
                contadores[entry["status"]] += 1

            index_fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            index_fh.flush()
            done.add(reg)

            if i % CHECKPOINT_EVERY == 0:
                save_checkpoint(done)
                total = sum(contadores.values())
                print(
                    f"[{i:>5}/{len(pendentes)}] "
                    f"ok={contadores['ok']} sem_bula={contadores['sem_bula']} "
                    f"erro={contadores['erro']} "
                    f"({contadores['ok']/total*100:.0f}% com bula)"
                )

        index_fh.close()
        save_checkpoint(done)
        browser.close()

    print("\n=== COLETA CONCLUÍDA ===")
    total = sum(contadores.values())
    print(f"ok (com bula) : {contadores['ok']}  ({contadores['ok']/total*100:.1f}%)")
    print(f"sem bula      : {contadores['sem_bula']}")
    print(f"erro          : {contadores['erro']}")
    print(f"index         : {INDEX}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
