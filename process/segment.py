"""
Segmentação da bula do paciente nas 9 seções padrão (RDC 47/2009).

Estratégia de âncora:
  - As 9 perguntas aparecem numeradas (1..9) e terminam em "?".
  - A âncora confiável é a LINHA iniciada por "N." / "N)" cujo conteúdo
    bate (fuzzy) com a pergunta canônica N.
  - O texto entre a âncora N e a âncora N+1 é a resposta N.
  - O bloco antes da pergunta 1 é a IDENTIFICAÇÃO DO MEDICAMENTO.
  - O bloco após a pergunta 9 (frequentemente "DIZERES LEGAIS") é capturado
    em `dizeres_legais`.

Não usa LLM: o template é fixo, então o parsing é determinístico.
"""

import re
import unicodedata

from rapidfuzz import fuzz

# Perguntas canônicas (ordem oficial). Índice 0 = pergunta 1.
PERGUNTAS_CANONICAS = [
    "Para que este medicamento é indicado?",
    "Como este medicamento funciona?",
    "Quando não devo usar este medicamento?",
    "O que devo saber antes de usar este medicamento?",
    "Onde, como e por quanto tempo posso guardar este medicamento?",
    "Como devo usar este medicamento?",
    "O que devo fazer quando eu me esquecer de usar este medicamento?",
    "Quais os males que este medicamento pode me causar?",
    "O que fazer se alguém usar uma quantidade maior do que a indicada deste medicamento?",
]

# limiar de similaridade fuzzy para aceitar uma linha como pergunta N
FUZZY_MIN = 80

# início de linha numerada: "N. ..." / "N) ..." / "N- ..."
# NÃO exige "?" na mesma linha (perguntas longas quebram em 2-3 linhas).
# O conteúdo após o número é OPCIONAL (.*): em muitas bulas o PyMuPDF extrai o
# número numa linha isolada ("1.") e a pergunta na(s) linha(s) seguinte(s).
_RE_NUM_START = re.compile(r"^\s*(\d{1,2})\s*[\.\)\-]\s*(.*)$")
# nº máximo de linhas acumuladas para formar uma pergunta multi-linha.
# 4 cobre o pior caso: número isolado + pergunta 9 quebrada em 2 linhas.
_MAX_LINHAS_PERGUNTA = 4
# fim da bula: dizeres legais
_RE_DIZERES = re.compile(r"DIZERES\s+LEGAIS", re.I)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


_CANON_NORM = [_norm(p) for p in PERGUNTAS_CANONICAS]


def _coleta_pergunta(linhas, i, primeiro_conteudo):
    """A partir da linha i (início numerado), acumula linhas até achar "?".

    Retorna (texto_da_pergunta_ate_interrogacao, indice_linha_final) ou
    (None, i) se nenhuma "?" surgir dentro de _MAX_LINHAS_PERGUNTA.
    """
    cand = primeiro_conteudo
    fim = i
    j = 1
    while "?" not in cand and j < _MAX_LINHAS_PERGUNTA and i + j < len(linhas):
        cand += " " + linhas[i + j].strip()
        fim = i + j
        j += 1
    if "?" not in cand:
        return None, i
    # trunca no primeiro "?" (descarta texto da resposta que veio junto)
    return cand[: cand.index("?") + 1], fim


def segment(text: str) -> dict:
    """Segmenta o texto de uma bula do paciente.

    Retorna dict:
        identificacao  — texto antes da pergunta 1
        secoes         — lista de {id, pergunta (canônica), resposta, score}
        dizeres_legais — texto após a pergunta 9 (se houver)
        secoes_encontradas — quantas das 9 foram localizadas
    """
    linhas = text.splitlines()

    # 1) localiza as âncoras. ancoras[num] = (linha_ini, linha_fim, score)
    #    linha_fim = última linha da pergunta (a resposta começa depois dela).
    ancoras = {}
    for i, line in enumerate(linhas):
        m = _RE_NUM_START.match(line.strip())
        if not m:
            continue
        num = int(m.group(1))
        if not (1 <= num <= 9):
            continue
        pergunta, linha_fim = _coleta_pergunta(linhas, i, m.group(2))
        if pergunta is None:
            continue
        score = fuzz.ratio(_norm(pergunta), _CANON_NORM[num - 1])
        if score < FUZZY_MIN:
            continue
        # mantém a âncora de maior score (evita falsos positivos no sumário)
        if num not in ancoras or score > ancoras[num][2]:
            ancoras[num] = (i, linha_fim, score)

    if not ancoras:
        return {"identificacao": text.strip(), "secoes": [],
                "dizeres_legais": None, "secoes_encontradas": 0}

    # 2) ordena âncoras por posição no texto
    ordem = sorted(ancoras.items(), key=lambda kv: kv[1][0])  # por linha_ini

    # identificação = antes da 1a âncora
    primeira_linha = ordem[0][1][0]
    identificacao = "\n".join(linhas[:primeira_linha]).strip()

    # 3) fatia respostas: da linha após o FIM da pergunta até o início da próxima
    secoes = []
    dizeres_legais = None
    for idx, (num, (linha_ini, linha_fim, score)) in enumerate(ordem):
        if idx + 1 < len(ordem):
            prox_ini = ordem[idx + 1][1][0]
        else:
            prox_ini = len(linhas)
        resposta = "\n".join(linhas[linha_fim + 1:prox_ini]).strip()

        # na última seção, separa os "DIZERES LEGAIS" da resposta
        if idx + 1 == len(ordem):
            mz = _RE_DIZERES.search(resposta)
            if mz:
                dizeres_legais = resposta[mz.start():].strip()
                resposta = resposta[:mz.start()].strip()

        secoes.append({
            "id": num,
            "pergunta": PERGUNTAS_CANONICAS[num - 1],
            "resposta": resposta,
            "score": round(score, 1),
        })

    return {
        "identificacao": identificacao,
        "secoes": secoes,
        "dizeres_legais": dizeres_legais,
        "secoes_encontradas": len(secoes),
    }


if __name__ == "__main__":
    import sys
    from extract import extract_text
    r = extract_text(sys.argv[1])
    seg = segment(r["text"])
    print(f"seções encontradas: {seg['secoes_encontradas']}/9")
    print(f"identificação ({len(seg['identificacao'])} chars)")
    for s in seg["secoes"]:
        print(f"  [{s['id']}] score={s['score']} "
              f"resp={len(s['resposta'])} chars | {s['pergunta'][:50]}")
    print(f"dizeres_legais: {'sim' if seg['dizeres_legais'] else 'não'}")
