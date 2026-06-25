---
name: llm-providers
description: Benchmark de providers LLM para extração de metadados de bulas (ChatBulário)
metadata: 
  node_type: memory
  type: project
  originSessionId: 0ee6a010-2d25-4b93-8ade-994bfd586e1d
---

Benchmark rodado em 2026-06-23 com 10 bulas. Provider configurado via `LLM_PROVIDER` no `.env`.
Código em `benchmark_providers.py`, resultado em `dataset/work_data/benchmark_providers.json`.

## Resultados

| Provider | Modelo | Qualidade | Latência | Custo (8k bulas) | ETA | Gargalo |
|---|---|---|---|---|---|---|
| `openai` | gpt-4o-mini | 7/8 campos | 5,3s/req | ~$1,40 | ~20 min | TPM (200K) e RPD (10K) |
| `groq` | llama-3.1-8b-instant | 7/8 campos | 0,55s/req | $0,00 (free) | ~12 h | TPM (6K) |
| `cerebras` | gpt-oss-120b | **inviável** | — | $0,00 (free) | ~6 dias | TPD (1M) |

**Cerebras descartado:** modelo de raciocínio — gasta ~633 tokens internos antes da resposta.
Com `max_tokens=3000` funciona, mas TPD de 1M limita a ~728 bulas/dia. Removido do `.env.example`.

**OpenAI e Groq têm a mesma qualidade** (7/8 campos — "fabricante" frequentemente ausente
na seção de identificação). A diferença é custo vs. tempo.

## Rate limits relevantes

**OpenAI gpt-4o-mini:**
- 500 RPM / 200K TPM / **10.000 RPD** (tier 1)
- TPM é o gargalo habitual: retries com backoff exponencial (1s→32s, 6 tentativas)
- **RPD é o risco real para 8k bulas:** retries de TPM consomem requests extras.
  O código distingue os dois: TPM → retry; RPD → falha rápida (sem retry inútil).
  Run interrompido por RPD é retomável — output gravado incrementalmente.

**Groq llama-3.1-8b-instant (free):**
- 30 RPM / 6K TPM / 14.4K RPD / 500K TPD
- Gargalo real: TPM → ~7 req/min efetivo → usar `--concorrencia 7`
- Para 8k bulas: ETA ~12h (seguro para RPD de 14.4K)

## Implementação

`process/meta_llm.py` usa OpenAI SDK com `base_url` configurável por provider.
Funções principais:
- `extract_meta_llm()` — síncrono, para testes via CLI
- `extract_meta_llm_stream()` — async generator, usado por `process_all.py`;
  produz `(índice, meta)` conforme cada chamada termina; falha individual → metadados
  vazios, nunca derruba o lote

Ver [[pipeline-architecture]].
