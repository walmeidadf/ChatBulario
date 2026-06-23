"""
Extração de texto de bulas do paciente (PyMuPDF).

Remove o ruído repetido de cabeçalho/rodapé das bulas no padrão RDC 47/2009
(ex.: "BULA PARA PACIENTE - RDC 47/2009", número de página, código de versão)
e devolve o texto em ordem de leitura.

Detecta PDFs com pouco texto (possivelmente escaneados) para que a etapa de
QC possa sinalizá-los — o OCR é uma decisão posterior.
"""

import re
from pathlib import Path

import fitz  # PyMuPDF

# Limiar abaixo do qual o PDF é suspeito de ser escaneado (imagem sem texto).
# As bulas de texto têm tipicamente 15k-60k chars; usamos margem folgada.
SCANNED_CHAR_THRESHOLD = 1500

# Linhas de ruído recorrentes (cabeçalho/rodapé). Casadas após strip().
_NOISE_PATTERNS = [
    re.compile(r"^BULA\s+(PARA|DO)\s+PACIENTE.*RDC\s*47/?2009", re.I),
    re.compile(r"^P[ÁA]GINA\s+\d+", re.I),
    re.compile(r"^\d{1,3}$"),                       # número de página solto
    re.compile(r"^[-–—\s]*$"),                      # linha vazia/traços
]


def _is_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    return any(p.search(s) for p in _NOISE_PATTERNS)


def extract_text(pdf_path: str | Path) -> dict:
    """Extrai texto limpo de um PDF de bula.

    Retorna dict com:
        text          — texto limpo, em ordem de leitura
        n_caracteres  — tamanho do texto limpo
        n_paginas     — páginas do PDF
        provavel_scan — True se abaixo do limiar de caracteres
        erro          — mensagem se a abertura falhar (senão None)
    """
    pdf_path = Path(pdf_path)
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # PDF corrompido / ilegível
        return {"text": "", "n_caracteres": 0, "n_paginas": 0,
                "provavel_scan": False, "erro": str(exc)}

    linhas = []
    for page in doc:
        raw = page.get_text("text")
        for line in raw.splitlines():
            if not _is_noise(line):
                linhas.append(line.rstrip())
    n_paginas = doc.page_count
    doc.close()

    text = "\n".join(linhas)
    # reconecta palavras hifenizadas que o PDF quebrou entre linhas
    # ex.: "cirurgião-\ndentista" → "cirurgião-dentista"
    text = re.sub(r"(\w)-\n(\w)", r"\1-\2", text)
    # colapsa quebras de linha múltiplas mantendo parágrafos
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    return {
        "text": text,
        "n_caracteres": len(text),
        "n_paginas": n_paginas,
        "provavel_scan": len(text) < SCANNED_CHAR_THRESHOLD,
        "erro": None,
    }


if __name__ == "__main__":
    import sys
    r = extract_text(sys.argv[1])
    print(f"paginas={r['n_paginas']} chars={r['n_caracteres']} "
          f"scan={r['provavel_scan']} erro={r['erro']}")
    print("-" * 60)
    print(r["text"][:1500])
