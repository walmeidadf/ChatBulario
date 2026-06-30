# AGENTS.md — ChatBulário

Guia para agentes de IA (Claude Code, etc.) que trabalham neste repositório.

---

## Visão geral

Pipeline de coleta e processamento de bulas de medicamentos da ANVISA para dataset NLP.
Estágio atual: pipeline completo. 7.930 bulas processadas → 68.938 pares pergunta/resposta
em `dataset.jsonl`; export para parquet/HuggingFace pronto (`export.py`).

```
collector.py → segment_all.py → enrich_all.py → build_dataset.py → export.py → HuggingFace
                  (grátis)        (Batch API)       (grátis)        (splits + upload)
```

---

## Arquitetura de decisões

### Pipeline em 3 artefatos: segmentar (grátis) ≠ enriquecer (pago)

Segmentação e enriquecimento têm cadências opostas e não devem estar acoplados:

| | Segmentação | Enriquecimento (LLM) |
|---|---|---|
| Custo | grátis | pago |
| Natureza | determinística | externa |
| Fragilidade | alta — varia por layout de PDF | baixa |
| Cadência ideal | iterar N vezes | rodar **1×** por bula |

No design acoplado (`process_all.py`), melhorar `segment.py` forçava re-pagar o LLM.
O LLM também rodava em bulas 0/9 — chamadas desperdiçadas sem nenhuma linha de output.

**Arquitetura em 3 artefatos:**

```
PDFs ──A──► segments.jsonl ──B──► meta.jsonl ──C──► dataset.jsonl
       (grátis, re-rodável)   (pago, 1×/bula)   (join grátis, rebuildável)
```

- **A — `segment_all.py`**: extract → split → segment → `segments.jsonl` + `qc.jsonl`.
  Re-rodável. Idempotente por registro (pula o que já está em `segments.jsonl`).
- **B — `enrich_all.py`**: lê `segments.jsonl`, gateia por `secoes_encontradas >= 1`,
  chama OpenAI Batch API, cacheia em `meta.jsonl`. Registros já em `meta.jsonl` são pulados.
  **O LLM nunca é re-chamado para um registro já cacheado.**
- **C — `build_dataset.py`**: join `segments.jsonl ⋈ meta.jsonl` → `dataset.jsonl`.
  Determinístico — reescreve do zero; sempre consistente com a última segmentação.

`meta.jsonl` é o único artefato caro/irreproduzível. Isolá-lo garante que iterar
segmentação (A) ou reconstruir o dataset (C) **nunca** re-bilha o LLM.

### Por que Batch API (não API síncrona)

A API síncrona da OpenAI tem RPD de 10.000 requests/dia. Com ~8.230 bulas + retries
por TPM, o limite é atingido antes de terminar. A Batch API não tem RPD — aceita até
50.000 requisições por submissão, processa em até 24h com 50% de desconto.

### Formato: JSONL de trabalho; Parquet como publicação (futuro)

`segments.jsonl` e `meta.jsonl` são append-only por registro — sobrevivem a crashes.
`dataset.jsonl` é derivado (sempre reconstruído por C). Quando o dataset estiver
completo, exportar para Parquet (`export_parquet.py`, a criar) para compatibilidade
com Hugging Face `datasets` e compressão colunar.

### Interface única para múltiplos providers LLM

`process/meta_llm.py` usa o SDK OpenAI com `base_url` configurável por provider.
Groq e Cerebras levantam `openai.RateLimitError` para 429 — mesma exceção.
Cerebras descartado (modelo de raciocínio, TPD inviável). Groq: qualidade inferior
ao OpenAI nos metadados — não usar para enriquecimento.
A Batch API só está disponível com `LLM_PROVIDER=openai`.

### Branch única: main

Sem branches de feature. Commits direto na main.

---

## Comandos principais

```bash
# Coleta (retomável)
uv run python collector.py
uv run python status.py              # acompanha progresso

# --- Pipeline em 3 estágios ---

# A) Segmentação — grátis, re-rodável, ~5 min para 8k bulas
uv run python segment_all.py

# B) Enriquecimento LLM via Batch API
uv run python enrich_all.py --async          # submete o batch, imprime o batch_id
uv run python enrich_all.py --status <id>   # verifica status
uv run python enrich_all.py --retrieve <id> # baixa resultado → meta.jsonl

# C) Build do dataset final
uv run python build_dataset.py

# --- Migração one-time (já rodado; manter para referência) ---
# Extrai meta do dataset.jsonl legado → meta.jsonl (preserva LLM já pago)
uv run python migrate_meta.py

# --- Pipeline legado (deprecated) ---
uv run python process_all.py
uv run python process_all.py --limite 5 --sem-llm

# Benchmark de providers LLM
uv run python benchmark_providers.py --n 10 --providers openai groq
```

---

## Arquivos críticos

| Arquivo | Papel |
|---|---|
| `collector.py` | Coleta via Playwright + API ANVISA (bypass Cloudflare) |
| `segment_all.py` | Estágio A: PDF → segments.jsonl + qc.jsonl (sem LLM) |
| `enrich_all.py` | Estágio B: segments.jsonl → meta.jsonl via OpenAI Batch API |
| `build_dataset.py` | Estágio C: join segments ⋈ meta → dataset.jsonl |
| `migrate_meta.py` | One-time: extrai meta do dataset.jsonl legado → meta.jsonl |
| `process/segment.py` | Fuzzy matching das 9 seções RDC 47/2009 |
| `process/meta_llm.py` | Extração LLM; retry TPM; falha rápida em RPD; Batch API via enrich_all.py |
| `dataset/work_data/segments.jsonl` | **Artefato A** — 1 linha/bula com seções (não versionado) |
| `dataset/work_data/meta.jsonl` | **Artefato B** — metadados LLM; NUNCA re-gerar sem necessidade |
| `dataset/work_data/dataset.jsonl` | **Artefato C** — derivado, 1 linha/registro×pergunta (não versionado) |
| `dataset/work_data/qc.jsonl` | Métricas de qualidade por bula (não versionado) |
| `dataset/raw_data/.../index.jsonl` | Índice de bulas coletadas (não versionado) |
| `process_all.py` | **Deprecated** — monolítico, sofre com RPD; mantido para referência |

---

## Armadilhas conhecidas

- **`sem_bula` no index pode ser falso negativo**: a API da ANVISA retorna lista vazia
  em falhas transitórias (Cloudflare, timeout), indistinguível de "sem bula cadastrada".
  Para re-tentar: remover entradas `sem_bula` do `index.jsonl` e do `checkpoint.json`.
  Feito em 2026-06-24: removeu 2.212 entradas, recuperou 589 bulas reais.

- **PDFs multi-bula**: alguns PDFs têm 10–20+ bulas concatenadas. `process/split.py`
  extrai só a primeira. Âncora: "IDENTIFICAÇÃO DO MEDICAMENTO".

- **PDFs scan (`provavel_scan=True`)**: ~22 arquivos são imagens sem texto extraível.
  Pulados em `segment_all.py`. Decisão futura: OCR.

- **Layout do número de pergunta varia**: muitas bulas têm o número numa linha isolada
  (`1.`) e a pergunta na seguinte. `segment.py` lida com isso (regex aceita número
  isolado, acumula até 4 linhas). Cobertura: 87% com 9/9, ~2% com 0/9 (layouts atípicos).

- **Cauda de 0/9**: ~2% das bulas não segmentam (perguntas com hífen `- PARA QUE...`,
  sem numeração, ou PDF em tabela). `enrich_all.py` gateia em `secoes_encontradas >= 1`
  — essas bulas não são enriquecidas nem aparecem no dataset final. Tratar futuramente
  com matcher mais tolerante sem tocar no caminho dos 93%.

---

## Roadmap

- [x] Pipeline em 3 artefatos (`segment_all`, `enrich_all` com Batch API, `build_dataset`)
- [ ] Rodar `migrate_meta.py` + `segment_all.py` + `enrich_all.py` + `build_dataset.py`
- [ ] QC do dataset final: checar `qc.jsonl`, validar % de 9/9, inspecionar 0/9
- [ ] Cauda de ~2% (0/9 seções): matcher mais tolerante em passe separado
- [ ] Export para Parquet (`export_parquet.py`) como artefato de publicação
- [ ] OCR para os ~22 PDFs `provavel_scan=True`
- [ ] RAG / chatbot sobre o dataset
