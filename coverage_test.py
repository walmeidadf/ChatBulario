"""
Teste de cobertura: a coluna SUBSTÂNCIA da tabela CMED (dez/2023) serve como
driver de coleta do Bulário Eletrônico da ANVISA?

Amostra N substâncias distintas do CSV, normaliza e consulta o bulário,
medindo quantas retornam >=1 bula e qual estratégia de normalização casou.

Uso:
    uv run python coverage_test.py [N]   # N = tamanho da amostra (default 200)
"""

import csv
import json
import random
import sys
import time
import unicodedata
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://consultas.anvisa.gov.br"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CSV = Path(
    "dataset/raw_data/anvisa_tables/medicamentos_anvisa_dez_2023.csv"
)
OUT = Path("discovery_out/coverage.json")

JS_FETCH = """
async (url) => {
  try {
    const r = await fetch(url, { headers: { Authorization: 'Guest' } });
    const t = await r.text();
    let b; try { b = JSON.parse(t); } catch (e) { b = null; }
    return { status: r.status, body: b };
  } catch (e) { return { status: -1, body: null }; }
}
"""


def query(page, q):
    """fetch com retry/backoff -> devolve (totalElements, nomeProdutoExemplo)
    ou levanta erro transiente se nunca obtivermos JSON válido."""
    url = f"{BASE}/api/consulta/bulario?count=1&page=1&filter[nomeProduto]={q}"
    for tentativa in range(4):
        r = page.evaluate(JS_FETCH, url)
        body = r["body"]
        if r["status"] == 200 and isinstance(body, dict):
            c = body.get("content") or []
            return body.get("totalElements", 0), (c[0].get("nomeProduto") if c else None)
        time.sleep(0.5 * (tentativa + 1))  # backoff
    return None, None  # transiente persistente


def norm(s: str) -> str:
    """lowercase, sem acentos, espaços colapsados."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


# prefixos de sal/éster que aparecem de forma inconsistente entre CSV e bulário
SAIS = [
    "cloridrato de", "dicloridrato de", "bromidrato de", "sulfato de",
    "maleato de", "acetato de", "fosfato de", "besilato de", "succinato de",
    "valerato de", "tartarato de", "mesilato de", "fumarato de", "nitrato de",
    "citrato de", "cloreto de", "dipropionato de", "propionato de",
    "hemifumarato de", "pamoato de", "sódico", "potássico", "cálcico",
]


def strip_sal(nome: str) -> str:
    s = nome
    for sal in SAIS:
        if s.startswith(sal + " "):
            s = s[len(sal) + 1:]
        if s.endswith(" " + sal):
            s = s[: -(len(sal) + 1)]
    return s.strip()


def candidatos(substancia: str):
    """Estratégias de query em ordem de preferência."""
    base = norm(substancia)
    seen = set()
    cands = []
    def add(estrat, q):
        if q and q not in seen:
            seen.add(q)
            cands.append((estrat, q))
    add("full", base.replace(";", " + "))
    primeiro = norm(substancia.split(";")[0]) if ";" in substancia else base
    add("nucleo", strip_sal(primeiro))  # remove sal -> princípio ativo "puro"
    if ";" in substancia:
        add("primeiro_componente", primeiro)
    return cands


def carregar_substancias():
    subs = {}
    with open(CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            s = row["SUBSTÂNCIA"].strip()
            if s and s not in subs:
                subs[s] = {
                    "classe": row["CLASSE TERAPÊUTICA"],
                    "combo": ";" in s,
                }
    return subs


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    subs = carregar_substancias()
    print(f"Substâncias distintas no CSV: {len(subs)}")
    random.seed(42)
    amostra = random.sample(list(subs.keys()), min(n, len(subs)))
    print(f"Amostra: {len(amostra)} substâncias\n")

    resultados = []
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
        page.goto(f"{BASE}/#/bulario/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)

        # confirma que o canal está aberto
        chk = page.evaluate(
            JS_FETCH, f"{BASE}/api/consulta/bulario?count=1&page=1&filter[nomeProduto]=dipirona"
        )
        if chk["status"] != 200:
            print(f"!! canal bloqueado (status {chk['status']}); abortando")
            browser.close()
            return 1
        print("canal ok (Cloudflare ultrapassado)\n")

        erros_transientes = 0
        for i, sub in enumerate(amostra, 1):
            meta = subs[sub]
            entry = {"substancia": sub, "combo": meta["combo"],
                     "classe": meta["classe"], "total": 0,
                     "estrategia": None, "nomeProdutoExemplo": None,
                     "transiente": False}
            for estrat, q in candidatos(sub):
                tot, exemplo = query(page, q)
                if tot is None:  # nunca obtivemos JSON -> transiente
                    entry["transiente"] = True
                    erros_transientes += 1
                    break
                if tot > 0:
                    entry.update(total=tot, estrategia=estrat,
                                 nomeProdutoExemplo=exemplo)
                    break
                time.sleep(0.25)
            resultados.append(entry)
            flag = "OK " if entry["total"] else ("?? " if entry["transiente"] else "-- ")
            if i % 20 == 0 or entry["total"] or entry["transiente"]:
                print(f"[{i:>3}/{len(amostra)}] {flag}{sub[:42]:42} "
                      f"-> {entry['total']:>4} ({entry['estrategia'] or ('transiente' if entry['transiente'] else 'miss')})")
            time.sleep(0.3)
        print(f"\nerros transientes (descartados das métricas): {erros_transientes}")

        browser.close()

    OUT.write_text(json.dumps(resultados, ensure_ascii=False, indent=2))

    # ---- métricas (transientes excluídos do denominador) ----
    validos = [r for r in resultados if not r.get("transiente")]
    total = len(validos)
    hits = [r for r in validos if r["total"]]
    combos = [r for r in validos if r["combo"]]
    simples = [r for r in validos if not r["combo"]]
    hit_simples = [r for r in simples if r["total"]]
    hit_combos = [r for r in combos if r["total"]]
    por_estrat = {}
    for r in hits:
        por_estrat[r["estrategia"]] = por_estrat.get(r["estrategia"], 0) + 1

    print("\n" + "=" * 60)
    print("COBERTURA — SUBSTÂNCIA (CSV) -> Bulário")
    print("=" * 60)
    print(f"amostra válida       : {total}  (de {len(resultados)} sorteadas)")
    print(f"com >=1 bula (hit)   : {len(hits)}  ({len(hits)/total*100:.1f}%)")
    print(f"  substância simples : {len(hit_simples)}/{len(simples)}"
          f" ({len(hit_simples)/max(len(simples),1)*100:.1f}%)")
    print(f"  combinações        : {len(hit_combos)}/{len(combos)}"
          f" ({len(hit_combos)/max(len(combos),1)*100:.1f}%)")
    print(f"estratégia de match  : {por_estrat}")
    bulas = sum(r["total"] for r in hits)
    print(f"soma de bulas (1a pág, estimativa de volume): {bulas}")
    print(f"\ndetalhe -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
