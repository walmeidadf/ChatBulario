---
name: pipeline-architecture
description: Arquitetura do pipeline de processamento de PDFs do ChatBulário
metadata: 
  node_type: memory
  type: project
  originSessionId: 0ee6a010-2d25-4b93-8ade-994bfd586e1d
---

Pipeline construído e commitado em 2026-06-23. Todas as etapas estão funcionais.

## Fluxo

```
PDF  →  extract  →  split_primeira_bula  →  segment  →  meta_llm  →  dataset.jsonl
```

## Módulos (`process/`)

| Arquivo | Função | Decisões relevantes |
|---|---|---|
| `extract.py` | PyMuPDF → texto limpo | Remove cabeçalho/rodapé RDC 47/2009; reconecta hifenização de quebra de linha com regex `(\w)-\n(\w)` |
| `split.py` | Isola 1ª bula em PDFs multi-bula | Ancora em "IDENTIFICAÇÃO DO MEDICAMENTO"; alguns PDFs têm 10-20+ bulas concatenadas |
| `segment.py` | Localiza as 9 seções RDC 47/2009 | Fuzzy matching (rapidfuzz, limiar=80); `_coleta_pergunta()` acumula até 3 linhas para achar `?` (pergunta 9 sempre quebra em 2 linhas); cobertura de 9/9 seções: ~81% das bulas |
| `meta_llm.py` | Extrai metadados estruturados da seção de identificação | Usa OpenAI SDK com suporte a múltiplos providers via `LLM_PROVIDER` env var; 8 campos: nome_comercial, fabricante, principio_ativo, forma_farmaceutica, via_administracao, apresentacao, composicao, uso |
| `structure.py` | Orquestrador por PDF individual | Útil para inspeção manual; `process_all.py` é o caminho para produção |

## Script principal: `process_all.py`

Flags:
- `--limite N` — processa só N primeiros (para testes)
- `--sem-llm` — pula extração LLM (mais rápido)
- `--re-run` — reprocessa registros já no output
- `--concorrencia N` — workers async paralelos para LLM (padrão 20)

Outputs em `dataset/work_data/`:
- `dataset.jsonl` — **1 linha por (registro × pergunta)**; todos os metadados embutidos em cada linha (flat, sem joins)
- `qc.jsonl` — 1 linha por bula com métricas de qualidade

## Schema do dataset.jsonl

```json
{
  "registro": "100290031",
  "nome_produto": "RENITEC®",
  "categoria_regulatoria": "Referência",
  "principio_ativo_csv": "MALEATO DE ENALAPRIL",
  "classe_terapeutica": "INIBIDOR DA ECA",
  "nome_comercial": "RENITEC®",
  "fabricante": "ORGANON FARMACÊUTICA LTDA.",
  "principio_ativo": "maleato de enalapril",
  "forma_farmaceutica": "comprimidos",
  "via_administracao": "oral",
  "apresentacao": "caixas com 30 comprimidos...",
  "composicao": "Cada comprimido contém...",
  "uso": "adulto",
  "secao_id": 1,
  "pergunta": "Para que este medicamento é indicado?",
  "resposta": "...",
  "fuzzy_score": 100.0
}
```

## Qualidade do texto extraído

Boa. O único artefato real era hifenização de quebra de linha (ex: `cirurgião-\ndentista`), corrigido deterministicamente em `extract.py`. "Linhas curtas" detectadas são bullets legítimos — não problema. LLM para limpeza de texto foi avaliado e **não vale** (texto já está pronto para NLP).

Ver [[anvisa-bulario-api]] para coleta, [[llm-providers]] para escolha de provider.
