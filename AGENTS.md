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

### Pipeline: extração e LLM acoplados intencionalmente

`process_all.py` une extração de texto e metadados LLM em um único script. Não separar:
o texto extraído é insumo imediato do LLM, não tem valor intermediário persistível.
Falhas são tratadas por retomada incremental (`ja_processados()` pula o que já foi gravado).

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

# Processar todas as bulas (retomável — pula já processadas)
uv run python process_all.py

# Processar sem LLM (só segmentação — para testes ou quando sem crédito de API)
uv run python process_all.py --sem-llm

# Reprocessar tudo (inclusive já processados)
uv run python process_all.py --re-run

# Teste rápido com N bulas
uv run python process_all.py --limite 5

# Status da coleta
uv run python status.py

# Benchmark de providers LLM
uv run python benchmark_providers.py --n 10 --providers openai groq
```

---

## Arquivos críticos

| Arquivo | Papel |
|---|---|
| `collector.py` | Coleta via Playwright + API ANVISA (bypass Cloudflare) |
| `process_all.py` | Orquestrador: PDF → dataset.jsonl, escrita incremental |
| `process/meta_llm.py` | Extração de metadados via LLM; retry TPM; falha rápida em RPD |
| `process/segment.py` | Fuzzy matching das 9 seções RDC 47/2009 |
| `dataset/raw_data/.../index.jsonl` | Índice de bulas coletadas (não versionado) |
| `dataset/work_data/dataset.jsonl` | Output principal (não versionado) |
| `dataset/work_data/qc.jsonl` | Métricas de qualidade por bula (não versionado) |

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

- [ ] **Cauda de ~2% (0/9 seções)**: passe pós-processamento só sobre registros 0/9,
  com matcher mais tolerante (perguntas com hífen `- PARA QUE...`, sem numeração, ou
  PDF em tabela com ordem de leitura embaralhada). Não mexer no caminho principal —
  risco de falso positivo nos 93% que já funcionam.
- [ ] **Dedup do `qc.jsonl`** ao final do processamento (keep-last por registro).
- [ ] **Export para Parquet** (`export_parquet.py`) como artefato de publicação.
- [ ] **OCR** para os ~22 PDFs `provavel_scan=True`.

- **RPD OpenAI esgota silenciosamente no código antigo**: o `asyncio.gather` antigo
  deixava o processo travar sem gravar nada. Resolvido com `extract_meta_llm_stream`
  (escrita incremental). Se encontrar código usando `extract_meta_llm_batch` no
  `process_all.py`, é versão antiga.
