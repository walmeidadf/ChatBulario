---
name: pipeline-architecture
description: Arquitetura do pipeline de processamento de PDFs do ChatBulário
metadata:
  node_type: memory
  type: project
  originSessionId: 0ee6a010-2d25-4b93-8ade-994bfd586e1d
---

Atualizado em 2026-06-24. Pipeline refatorado para 3 estágios independentes.

## Fluxo

```
PDF  →  (A) segment_all  →  segments.jsonl
                                  │
                             (B) enrich_all  →  meta.jsonl
                                                     │
                                              (C) build_dataset  →  dataset.jsonl
```

## Estágio A — `segment_all.py` (grátis, re-rodável)

| Módulo | Função | Decisões relevantes |
|---|---|---|
| `extract.py` | PyMuPDF → texto limpo | Remove cabeçalho/rodapé RDC 47/2009; reconecta hifenização de quebra de linha com regex `(\w)-\n(\w)` |
| `split.py` | Isola 1ª bula em PDFs multi-bula | Âncora em "IDENTIFICAÇÃO DO MEDICAMENTO"; alguns PDFs têm 10-20+ bulas concatenadas |
| `segment.py` | Localiza as 9 seções RDC 47/2009 | Fuzzy matching (rapidfuzz, limiar=80); aceita número isolado na linha (`1.` sozinho) + acumula até 4 linhas para achar `?`; cobertura: ~87% com 9/9, ~2% com 0/9 (layouts atípicos) |

Output: `segments.jsonl` (1 linha/bula) + `qc.jsonl`.
Idempotente por registro — re-rodar pula o que já está em `segments.jsonl`.

Schema de `segments.jsonl`:
```json
{
  "registro": "100290031",
  "nomeProduto": "RENITEC®",
  "categoriaRegulatoria": "Referência",
  "principioAtivo": "MALEATO DE ENALAPRIL",
  "classeTerapeutica": "INIBIDOR DA ECA",
  "expediente": "...",
  "identificacao": "IDENTIFICAÇÃO DO MEDICAMENTO\n...",
  "secoes": [{"id": 1, "pergunta": "Para que...", "resposta": "...", "score": 100.0}],
  "secoes_encontradas": 9,
  "dizeres_legais": "...",
  "n_caracteres": 12000,
  "n_paginas": 6,
  "provavel_scan": false,
  "erro": null
}
```

## Estágio B — `enrich_all.py` (OpenAI Batch API, 1× por bula)

Lê `segments.jsonl`, filtra `secoes_encontradas >= 1`, e chama o LLM para extrair
metadados estruturados da seção de identificação de cada bula.

Usa a **OpenAI Batch API** (não a API síncrona):
- Sem limite de RPD — a API síncrona tem 10.000 req/dia, insuficiente para 8k+ bulas
- Até 50.000 requisições por submissão, resultado em até 24h, 50% de desconto
- Fluxo: `--async` submete e retorna imediatamente; `--retrieve <id>` baixa quando pronto
- **Limite de tokens enfileirados:** 2M tokens por organização para `gpt-4o-mini`. Com ~2.400 tokens/bula, o máximo seguro por batch é ~800 requisições. O script divide automaticamente em chunks de 800 e salva todos os batch IDs em `dataset/work_data/batch_ids.txt`.

Registros já em `meta.jsonl` são pulados — o LLM nunca é re-chamado para o que
já foi pago. Iterar a segmentação (estágio A) não gera nova cobrança.

Schema de `meta.jsonl`:
```json
{
  "registro": "100290031",
  "nome_comercial": "RENITEC®",
  "fabricante": "ORGANON FARMACÊUTICA LTDA.",
  "principio_ativo": "maleato de enalapril",
  "forma_farmaceutica": "comprimidos",
  "via_administracao": "oral",
  "apresentacao": "caixas com 30 comprimidos...",
  "composicao": "Cada comprimido contém...",
  "uso": "adulto"
}
```

## Estágio C — `build_dataset.py` (grátis, determinístico)

Join `segments.jsonl ⋈ meta.jsonl` → `dataset.jsonl` no schema flat.
Sempre reescreve do zero — `dataset.jsonl` é derivado, nunca editar à mão.
Para registros sem meta (não enriquecidos), metadados LLM ficam nulos mas as seções
segmentadas são incluídas.

Schema de `dataset.jsonl` (1 linha por registro × pergunta):
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

## Resiliência

- **A** é idempotente por registro — re-rodar não duplica.
- **B** pula registros já em `meta.jsonl` — run interrompido é retomável sem re-bilhagem.
- **C** é determinístico — reescreve do zero, sempre consistente.

## Pipeline legado (`process_all.py`) — deprecated

O `process_all.py` original acoplava extração + segmentação + LLM num único script.
Foi substituído pelo pipeline em 3 estágios. Mantido para referência.
Problemas que levaram à substituição:
- Melhorar `segment.py` forçava `--re-run` (re-pagava todo o LLM)
- API síncrona da OpenAI tem RPD de 10.000 req/dia — insuficiente para 8k+ bulas
- LLM rodava em bulas 0/9 (chamadas desperdiçadas sem output)

## Qualidade do texto extraído

Boa. O único artefato real era hifenização de quebra de linha (ex: `cirurgião-\ndentista`),
corrigido deterministicamente em `extract.py`. LLM para limpeza de texto foi avaliado
e não vale — texto já está pronto para NLP.

Ver [[anvisa-bulario-api]] para coleta, [[llm-providers]] para escolha de provider.
