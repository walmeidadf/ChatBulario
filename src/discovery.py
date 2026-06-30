"""
Discovery técnico da API do Bulário Eletrônico da ANVISA (projeto ChatBulário).

Estratégia (equivalente Python ao plano original com Puppeteer):
  - Playwright + Chromium headless real -> passa pelo Cloudflare.
  - fetch() executado DENTRO do contexto da página (mesma origin, cookies do
    challenge já resolvidos) com header Authorization: "Guest".
  - PyMuPDF (fitz) para extrair texto dos PDFs e detectar seções.

Uso:
    uv run python discovery.py
"""

import base64
import json
import re
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF
from playwright.sync_api import sync_playwright

BASE = "https://consultas.anvisa.gov.br"
BULARIO_PAGE = f"{BASE}/#/bulario/"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MEDICAMENTOS = ["dipirona", "paracetamol", "amoxicilina", "losartana", "metformina"]

# Seções típicas de bula (paciente + profissional). Casamos por regex tolerante
# a acentos/variações. Chave = rótulo canônico; valor = regex de busca.
SECOES = {
    "INDICACOES": r"PARA\s+QUE\s+ESTE\s+MEDICAMENTO|IND[IÍ]CA[ÇC][ÕO]ES",
    "CONTRAINDICACOES": r"N[ÃA]O\s+DEVO\s+USAR|CONTRA[\s-]?INDICA[ÇC][ÕO]ES",
    "PRECAUCOES": r"O\s+QUE\s+DEVO\s+SABER\s+ANTES|ADVERT[EÊ]NCIAS?\s+E\s+PRECAU[ÇC][ÕO]ES",
    "POSOLOGIA": r"COMO\s+DEVO\s+USAR|POSOLOGIA|MODO\s+DE\s+USAR",
    "REACOES_ADVERSAS": r"MALES\s+QUE\s+ESTE\s+MEDICAMENTO\s+PODE|REA[ÇC][ÕO]ES\s+ADVERSAS",
    "SUPERDOSE": r"QUANTIDADE\s+MAIOR|SUPERDOSE",
    "ARMAZENAGEM": r"ONDE,?\s+COMO\s+E\s+POR\s+QUANTO\s+TEMPO|CUIDADOS\s+DE\s+ARMAZENAMENTO",
    "COMPOSICAO": r"COMPOSI[ÇC][ÃA]O",
}

OUT_DIR = Path("discovery_out")
PDF_DIR = OUT_DIR / "pdfs"
PDF_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers executados no browser
# ---------------------------------------------------------------------------

# JS: fetch JSON com header Authorization: Guest, retornando {status, body}
JS_FETCH_JSON = """
async (url) => {
  try {
    const r = await fetch(url, { headers: { Authorization: 'Guest' } });
    const text = await r.text();
    let body;
    try { body = JSON.parse(text); } catch (e) { body = text; }
    return { ok: r.ok, status: r.status, body };
  } catch (e) {
    return { ok: false, status: -1, body: String(e) };
  }
}
"""

# JS: baixa binário (PDF) e devolve em base64
JS_FETCH_PDF = """
async (url) => {
  try {
    const r = await fetch(url, { headers: { Authorization: 'Guest' } });
    if (!r.ok) return { ok: false, status: r.status, b64: null };
    const buf = await r.arrayBuffer();
    let binary = '';
    const bytes = new Uint8Array(buf);
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return { ok: true, status: r.status, b64: btoa(binary) };
  } catch (e) {
    return { ok: false, status: -1, b64: null, err: String(e) };
  }
}
"""


def fetch_json(page, path):
    return page.evaluate(JS_FETCH_JSON, BASE + path)


def fetch_pdf_bytes(page, path):
    res = page.evaluate(JS_FETCH_PDF, BASE + path)
    if res.get("ok") and res.get("b64"):
        return base64.b64decode(res["b64"])
    return None


# ---------------------------------------------------------------------------
# Análise de PDF
# ---------------------------------------------------------------------------

def analisar_pdf(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texto = "\n".join(p.get_text() for p in doc)
    doc.close()
    upper = texto.upper()
    secoes = {nome: bool(re.search(rx, upper)) for nome, rx in SECOES.items()}
    return {
        "totalCaracteres": len(texto),
        "totalPaginas": doc.page_count if not doc.is_closed else None,
        "secoes": secoes,
        "secoesEncontradas": sum(secoes.values()),
    }


def pega(d, *chaves):
    """Retorna o primeiro valor não-nulo entre as chaves candidatas."""
    for k in chaves:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------

def main():
    resultados = []
    primeiro_dump_feito = False

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(user_agent=UA, locale="pt-BR")
        # mascara navigator.webdriver
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = ctx.new_page()

        print(f"-> Navegando para {BULARIO_PAGE} (aguardando Cloudflare/Angular)...")
        page.goto(BULARIO_PAGE, wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)  # margem para challenge do Cloudflare + boot do Angular

        # sanity-check: o fetch de dentro da página passa pelo Cloudflare?
        teste = fetch_json(page, "/api/consulta/tipoCategoriaRegulatoria")
        print(f"   sanity tipoCategoriaRegulatoria -> status {teste['status']}")
        if teste["status"] == 403:
            print("!! Cloudflare ainda bloqueando. Aumentando espera e tentando de novo...")
            time.sleep(8)
            teste = fetch_json(page, "/api/consulta/tipoCategoriaRegulatoria")
            print(f"   retry -> status {teste['status']}")

        for med in MEDICAMENTOS:
            print(f"\n=== {med} ===")
            entrada = {"medicamento": med}

            busca = fetch_json(
                page, f"/api/consulta/bulario?count=10&page=1&filter[nomeProduto]={med}"
            )
            if busca["status"] != 200 or not isinstance(busca["body"], dict):
                print(f"   busca falhou (status {busca['status']})")
                entrada["erro"] = f"busca status {busca['status']}"
                resultados.append(entrada)
                continue

            body = busca["body"]
            # dump da estrutura crua do primeiro medicamento p/ inspeção de campos
            if not primeiro_dump_feito:
                (OUT_DIR / "_raw_busca_dump.json").write_text(
                    json.dumps(body, ensure_ascii=False, indent=2)
                )
                print("   (estrutura crua salva em discovery_out/_raw_busca_dump.json)")
                primeiro_dump_feito = True

            content = pega(body, "content", "data") or []
            total = pega(body, "totalElements", "total", "totalRegistros") or len(content)
            entrada["totalEncontrados"] = total
            print(f"   encontrados: {total}")

            if not content:
                resultados.append(entrada)
                continue

            r0 = content[0]
            id_paciente = pega(
                r0, "idBulaPacienteProtegido", "idBulaPaciente"
            )
            id_profissional = pega(
                r0, "idBulaProfissionalProtegido", "idBulaProfissional"
            )
            entrada["primeiroResultado"] = {
                "nomeProduto": pega(r0, "nomeProduto", "nomeComercial", "nome"),
                "expediente": pega(r0, "numeroExpediente", "expediente"),
                "idBulaPaciente": id_paciente,
                "idBulaProfissional": id_profissional,
            }
            print(f"   produto: {entrada['primeiroResultado']['nomeProduto']}")
            print(f"   idBulaPaciente={id_paciente} idBulaProfissional={id_profissional}")

            for rotulo, id_bula in (
                ("textoBulaPaciente", id_paciente),
                ("textoBulaProfissional", id_profissional),
            ):
                if not id_bula:
                    print(f"   {rotulo}: sem id")
                    continue
                path = f"/api/consulta/medicamentos/arquivo/bula/parecer/{id_bula}/?Authorization="
                pdf = fetch_pdf_bytes(page, path)
                if not pdf:
                    print(f"   {rotulo}: download falhou")
                    entrada[rotulo] = {"erro": "download falhou"}
                    continue
                fname = PDF_DIR / f"{med}_{rotulo.replace('textoBula','').lower()}.pdf"
                fname.write_bytes(pdf)
                analise = analisar_pdf(pdf)
                analise["arquivo"] = str(fname)
                entrada[rotulo] = analise
                print(
                    f"   {rotulo}: {len(pdf)} bytes, "
                    f"{analise['totalCaracteres']} chars, "
                    f"{analise['secoesEncontradas']}/{len(SECOES)} seções"
                )

            entrada["parParPacienteProfissional"] = bool(id_paciente and id_profissional)
            resultados.append(entrada)

        browser.close()

    (OUT_DIR / "results.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=2)
    )

    # ---- resumo no terminal ----
    print("\n" + "=" * 60)
    print("RESUMO DO DISCOVERY")
    print("=" * 60)
    for e in resultados:
        med = e["medicamento"]
        total = e.get("totalEncontrados", "?")
        par = "SIM" if e.get("parParPacienteProfissional") else "NAO"
        pac = e.get("textoBulaPaciente", {})
        prof = e.get("textoBulaProfissional", {})
        print(
            f"- {med:12} | encontrados={total!s:>4} | par pac/prof={par} | "
            f"pac={pac.get('secoesEncontradas','-')}sec "
            f"prof={prof.get('secoesEncontradas','-')}sec"
        )
    print(f"\nresults.json -> {OUT_DIR/'results.json'}")
    print(f"PDFs -> {PDF_DIR}/")


if __name__ == "__main__":
    sys.exit(main())
