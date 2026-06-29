# AGENTS.md — ChatBulário

Guia para agentes de IA (Claude Code, etc.) que trabalham neste repositório.

---

## Visão geral

Pipeline de coleta e processamento de bulas de medicamentos da ANVISA para dataset NLP.
Estágio atual: coleta concluída (~8.800 PDFs), processamento LLM em andamento.

```
collector.py  →  process_all.py  →  dataset.jsonl  →  [futuro] RAG / chatbot
```

---

## Arquitetura de decisões

### Pipeline em 3 artefatos: segmentar (grátis) ≠ enriquecer (pago)

> Decisão revisada em 2026-06-24. A versão anterior (`process_all.py` monolítico)
> acoplava extração+segmentação+LLM num passo só. Isso estava certo para
> *extração↔LLM* (texto é insumo imediato, sem valor intermediário), mas **errado
> para segmentação↔enriquecimento**, que têm cadências opostas:
>
> | | Segmentação | Enriquecimento (LLM) |
> |---|---|---|
> | Custo | grátis | pago + rate-limited (RPD 10K/dia) |
> | Natureza | determinística | externa |
> | Fragilidade | alta (varia por layout de PDF) | baixa |
> | Cadência ideal | iterar N vezes | rodar **1×** por bula |
>
> No design acoplado, melhorar `segment.py` força re-pagar o LLM (`--re-run` re-bilha
> tudo). E o LLM rodava mesmo em bulas 0/9, que geram 0 linhas → chamada desperdiçada.

**Arquitetura-alvo — 3 artefatos, cada estágio com sua cadência:**

```
PDFs ──A──► segments.jsonl ──B──► meta.jsonl ──C──► dataset.jsonl
       (grátis, re-rodável)   (pago, 1×/bula)   (join grátis, rebuildável)
```

- **A — `segment_all.py`** (grátis): extract → split → segment → `segments.jsonl`
  (1 linha/bula: registro, identificacao, secoes[], secoes_encontradas, flags) + `qc.jsonl`.
  Re-rodável à vontade ao tunar `segment.py`. Idempotente por registro.
- **B — `enrich_all.py`** (pago, 1×): lê `segments.jsonl`, **gateia** por
  `secoes_encontradas >= limiar` (default >0 — não enriquece 0/9), chama LLM na
  `identificacao`, cacheia metadados em `meta.jsonl` (1 linha/registro). Só processa
  registros ausentes de `meta.jsonl`. **O LLM nunca é re-chamado para um registro já cacheado.**
- **C — `build_dataset.py`** (grátis): join `segments.jsonl ⋈ meta.jsonl` →
  `dataset.jsonl` (schema atual inalterado). Rebuildável a qualquer momento, sempre
  consistente com a última segmentação + os metadados já pagos.

**Por que `meta.jsonl` separado:** é o único artefato caro/irreproduzível. Isolá-lo
garante que iterar segmentação (A) ou reconstruir o dataset (C) **nunca** re-bilha o LLM.
O fluxo da cauda de 2% vira: melhora `segment.py` → roda A (grátis) → roda B (enriquece
só os recém-recuperados) → roda C (grátis). As bulas 9/9 já em `meta.jsonl` não são re-cobradas.

**Retomada:** A keyed em `segments.jsonl`; B keyed em `meta.jsonl`; C é determinístico
(reescreve do zero). Todos com escrita incremental + flush por item (padrão atual).

### Formato de trabalho: JSONL; publicação: Parquet (futuro)

`dataset.jsonl` é append-only, linha-a-linha, sobrevive a crashes. Parquet seria inviável
para escrita incremental. Quando o dataset estiver completo, exportar com `export_parquet.py`
(a criar) para compressão colunar e compatibilidade com Hugging Face `datasets`.

### Interface única para múltiplos providers LLM

`process/meta_llm.py` usa o SDK OpenAI com `base_url` diferente por provider.
Groq e Cerebras levantam `openai.RateLimitError` para 429 — mesma exceção, funciona.
Cerebras descartado (modelo de raciocínio, gasta tokens internos — TPD inviável).

### Branch única: main

Sem branches de feature. Commits direto na main.

---

## Rate limits e comportamento esperado do LLM

**OpenAI gpt-4o-mini:**
- TPM: 200K — erros 429 transitórios, retry com backoff exponencial (até 32s, 6 tentativas)
- RPD: 10.000 requests/dia — ao atingir, falha rápido (sem retry — não adianta)
- ~8.230 bulas × 1 req = margem justa; retries de TPM podem estourar o RPD

**Groq llama-3.1-8b-instant (free tier):**
- Gargalo: TPM (6K) → ~7 req/min efetivo → usar `--concorrencia 7`
- ETA: ~12h para 8k bulas; gratuito

Se RPD da OpenAI esgotar no meio do run: o `extract_meta_llm_stream` captura a exceção,
grava metadados vazios para as bulas restantes e continua. Rodar novamente com `--re-run`
para preencher os registros com `nome_comercial=null`.

---

## Comandos principais

```bash
# Coleta (retomável)
uv run python collector.py

# Status da coleta
uv run python status.py

# --- Pipeline em 3 estágios (caminho atual) ---

# A) Segmentação — grátis, re-rodável, ~5 min para 8k bulas
uv run python segment_all.py

# B) Enriquecimento LLM via Batch API — submete e retorna imediatamente
uv run python enrich_all.py --async
# Verificar status do batch (retorna o output_file_id quando concluído)
uv run python enrich_all.py --status <batch_id>
# Baixar e processar resultado (rodar quando status=completed)
uv run python enrich_all.py --retrieve <batch_id>

# C) Build do dataset final — determinístico, reescreve do zero
uv run python build_dataset.py

# --- Migração (one-time, apenas uma vez) ---
# Preserva LLM já pago no dataset.jsonl → meta.jsonl antes de migrar
uv run python migrate_meta.py

# --- Pipeline legado (deprecated, mantido para referência) ---
uv run python process_all.py          # monolítico, sofre com RPD OpenAI
uv run python process_all.py --sem-llm
uv run python process_all.py --re-run
uv run python process_all.py --limite 5

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
| `migrate_meta.py` | One-time: extrai meta do dataset.jsonl existente → meta.jsonl |
| `process/meta_llm.py` | Extração de metadados LLM; retry TPM; falha rápida em RPD |
| `process/segment.py` | Fuzzy matching das 9 seções RDC 47/2009 |
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
  Para re-tentar: remover entradas `sem_bula` do `index.jsonl` e do `checkpoint.json`
  (ver script ad-hoc usado em 2026-06-24 — removeu 2.212 entradas, recuperou 589 bulas).

- **PDFs multi-bula**: alguns PDFs têm 10–20+ bulas concatenadas. `process/split.py`
  extrai só a primeira. Âncora: "IDENTIFICAÇÃO DO MEDICAMENTO".

- **PDFs scan (`provavel_scan=True`)**: ~22 arquivos são imagens sem texto extraível.
  Pulados silenciosamente. Decisão futura: OCR.

- **Layout de número de pergunta varia entre bulas**: muitas bulas têm o número
  numa linha isolada (`1.` numa linha, a pergunta na seguinte). `segment.py` lida
  com isso (regex aceita número isolado + acumula até 4 linhas). Numa amostra
  aleatória de 400 bulas novas: 87% com 9/9, 2% com 0/9.

- **`qc.jsonl` acumula duplicatas para bulas 0/9**: a retomada (`ja_processados`)
  se baseia no `dataset.jsonl`; bulas que segmentam para 0 seções não geram linha
  lá, então re-escrevem no `qc.jsonl` a cada run. Fazer dedup ao final
  (manter última entrada por registro). `dataset.jsonl` é a fonte de verdade e fica limpo.

## Roadmap

- [x] **Refatorar para pipeline em 3 artefatos** — `segment_all.py`, `enrich_all.py`
  (Batch API), `build_dataset.py` implementados. Ver sequência de execução abaixo.
- [ ] **Cauda de ~2% (0/9 seções)**: matcher mais tolerante para registros 0/9
  (perguntas com hífen `- PARA QUE...`, sem numeração, ou PDF em tabela com ordem
  embaralhada). Vira trivial com a pipeline em estágios: tuna `segment.py` → roda A → B → C.
  Não mexer no caminho dos 93% que já funcionam — risco de falso positivo.
- [ ] **Dedup do `qc.jsonl`** (keep-last por registro). Resolvido naturalmente em A
  (idempotente por registro) quando a refatoração entrar.
- [ ] **Export para Parquet** (`export_parquet.py`) como artefato de publicação.
- [ ] **OCR** para os ~22 PDFs `provavel_scan=True`.

## Plano de migração: monolito → 3 estágios

**Sequência (não bloqueia o run atual — fazer DEPOIS que `process_all.py` terminar):**

1. **Preservar o trabalho de LLM já pago.** Gerar `meta.jsonl` a partir do
   `dataset.jsonl` existente: dedup por `registro`, extraindo os 8 campos de metadados +
   `nome_produto`. Script único (`migrate_meta.py`, descartável). **Zero re-bilhagem.**

2. **`segment_all.py`** (estágio A) — extrair de `process_all.py` os passos 1–2
   (extract → split → segment) escrevendo `segments.jsonl` + `qc.jsonl`. Sem LLM.
   Schema de `segments.jsonl`: `{registro, identificacao, secoes:[{id,pergunta,resposta,score}],
   secoes_encontradas, n_caracteres, n_paginas}`.

3. **`enrich_all.py`** (estágio B) — ler `segments.jsonl`, filtrar
   `secoes_encontradas > 0`, chamar `extract_meta_llm_stream` na `identificacao`,
   escrever `meta.jsonl` (1 linha/registro). Pular registros já em `meta.jsonl`.
   Reaproveita o retry TPM/RPD e a escrita incremental atuais.

4. **`build_dataset.py`** (estágio C) — join `segments.jsonl ⋈ meta.jsonl` por
   `registro`, emitindo o `dataset.jsonl` no schema atual (1 linha/`registro×pergunta`).
   Determinístico, reescreve do zero. Para registros sem meta (gate não passou ou ainda
   não enriquecido), usar fallback de `nome_produto` do índice e metadados nulos.

5. **Aposentar `process_all.py`** ou transformá-lo em wrapper `A→B→C` para o caminho
   feliz. Manter `--limite`, `--re-run`, `--concorrencia`, `--sem-llm` (= só A+C).

**Riscos a vigiar:**
- Consistência: `dataset.jsonl` é sempre derivado — nunca editar à mão; sempre via C.
- `meta.jsonl` é o artefato precioso: incluir no backup; nunca regenerar sem necessidade.
- Idempotência de A: chave por `registro`; re-rodar não deve duplicar (corrige o wart do qc).

- **RPD OpenAI esgota silenciosamente no código antigo**: o `asyncio.gather` antigo
  deixava o processo travar sem gravar nada. Resolvido com `extract_meta_llm_stream`
  (escrita incremental). Se encontrar código usando `extract_meta_llm_batch` no
  `process_all.py`, é versão antiga.
