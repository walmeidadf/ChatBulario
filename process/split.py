"""
Detecta e extrai apenas a primeira bula de PDFs que contêm múltiplas bulas
(diferentes apresentações/concentrações do mesmo medicamento).

Estratégia:
  - Localiza todas as ocorrências do marcador de início de bula
    ("IDENTIFICAÇÃO DO MEDICAMENTO" / "IDENTIFICAÇÃO DO PRODUTO" / "APRESENTAÇÕES").
  - A 1ª ocorrência marca o início do conteúdo real.
  - A 2ª ocorrência (se existir) marca o início da segunda bula → corta ali.
  - Se não houver marcador, devolve o texto inteiro.
"""

import re

# Marcadores de início de bula, em ordem de confiabilidade.
# Usamos o mais específico que aparecer no texto.
_MARCADORES = [
    re.compile(r"IDENTIFICA[ÇC][ÃA]O\s+DO\s+MEDICAMENTO", re.I),
    re.compile(r"IDENTIFICA[ÇC][ÃA]O\s+DO\s+PRODUTO", re.I),
]

# Fallback: "APRESENTAÇÕES" sozinho numa linha — menos específico, usado
# apenas quando os marcadores primários não aparecem.
_MARCADOR_FALLBACK = re.compile(r"^APRESENTA[ÇC][ÕO]ES?\s*$", re.I | re.M)


def _posicoes(pattern: re.Pattern, text: str) -> list[int]:
    return [m.start() for m in pattern.finditer(text)]


def split_primeira_bula(text: str) -> str:
    """Retorna apenas o texto da primeira bula.

    Se o PDF tiver N bulas, o texto entre o 1º e 2º marcador é a 1ª bula.
    Se não houver 2º marcador (PDF de bula única), devolve o texto inteiro.
    """
    for pat in _MARCADORES:
        pos = _posicoes(pat, text)
        if len(pos) >= 2:
            return text[pos[0]:pos[1]].strip()
        if len(pos) == 1:
            return text[pos[0]:].strip()

    # fallback
    pos_fb = _posicoes(_MARCADOR_FALLBACK, text)
    if len(pos_fb) >= 2:
        return text[: pos_fb[1]].strip()

    return text.strip()


def diagnostico(text: str) -> dict:
    """Retorna info de diagnóstico sobre a estrutura multi-bula do texto."""
    resultado = {"marcador": None, "n_bulas_detectadas": 1}
    for pat in _MARCADORES:
        pos = _posicoes(pat, text)
        if pos:
            resultado["marcador"] = pat.pattern
            resultado["n_bulas_detectadas"] = len(pos)
            return resultado
    pos_fb = _posicoes(_MARCADOR_FALLBACK, text)
    if pos_fb:
        resultado["marcador"] = "APRESENTACOES (fallback)"
        resultado["n_bulas_detectadas"] = len(pos_fb)
    return resultado
