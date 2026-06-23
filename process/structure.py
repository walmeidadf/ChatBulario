"""
Orquestra a estruturação de uma bula do paciente:
  PDF -> extract -> split_primeira_bula -> segment -> meta_llm -> JSON enriquecido.

Os metadados vêm de duas fontes:
  - index.jsonl (coleta): categoria regulatória, princípio ativo, classe terapêutica
  - LLM (Haiku): nome comercial, fabricante, forma farmacêutica, via, apresentação, etc.
"""

from pathlib import Path

from extract import extract_text
from meta_llm import extract_meta_llm
from segment import segment
from split import split_primeira_bula


def estruturar(
    pdf_path: str | Path,
    registro: str,
    metadados: dict | None = None,
    usar_llm: bool = True,
) -> dict:
    """Processa um PDF de bula do paciente e devolve o documento estruturado.

    metadados: dict opcional vindo do index.jsonl (categoria, princípio ativo,
               classe terapêutica, nome do produto).
    usar_llm:  se False, pula a extração LLM de metadados (útil em testes).
    """
    metadados = metadados or {}
    ext = extract_text(pdf_path)

    if ext["erro"]:
        return {
            "registro": registro,
            "qc": {"erro_extracao": ext["erro"], "completa": False,
                   "secoes_encontradas": 0},
        }

    texto = split_primeira_bula(ext["text"])
    seg = segment(texto)

    encontradas = {s["id"] for s in seg["secoes"]}
    faltantes = [n for n in range(1, 10) if n not in encontradas]

    meta_llm = extract_meta_llm(seg["identificacao"]) if usar_llm else {}

    return {
        "registro": registro,
        "nome_produto": (
            meta_llm.get("nome_comercial")
            or metadados.get("nomeProdutoBulario")
            or metadados.get("nomeProdutoCSV")
        ),
        "metadados": {
            # do index.jsonl (coleta)
            "categoria_regulatoria": metadados.get("categoriaRegulatoria"),
            "principio_ativo_csv": metadados.get("principioAtivo"),
            "classe_terapeutica": metadados.get("classeTerapeutica"),
            "expediente": metadados.get("expediente"),
            # do LLM
            "nome_comercial": meta_llm.get("nome_comercial"),
            "fabricante": meta_llm.get("fabricante"),
            "principio_ativo": meta_llm.get("principio_ativo"),
            "forma_farmaceutica": meta_llm.get("forma_farmaceutica"),
            "via_administracao": meta_llm.get("via_administracao"),
            "apresentacao": meta_llm.get("apresentacao"),
            "composicao": meta_llm.get("composicao"),
            "uso": meta_llm.get("uso"),
        },
        "identificacao": seg["identificacao"],
        "secoes": seg["secoes"],
        "dizeres_legais": seg["dizeres_legais"],
        "qc": {
            "secoes_encontradas": seg["secoes_encontradas"],
            "secoes_faltantes": faltantes,
            "completa": seg["secoes_encontradas"] == 9,
            "n_caracteres": ext["n_caracteres"],
            "n_paginas": ext["n_paginas"],
            "provavel_scan": ext["provavel_scan"],
            "erro_extracao": None,
        },
    }


if __name__ == "__main__":
    import json
    import sys
    reg = Path(sys.argv[1]).parent.name
    usar_llm = "--no-llm" not in sys.argv
    doc = estruturar(sys.argv[1], reg, usar_llm=usar_llm)
    print(json.dumps(doc, ensure_ascii=False, indent=2)[:3000])
