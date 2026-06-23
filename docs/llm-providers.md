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

| Provider | Modelo | Qualidade | Latência | Custo (5k bulas) | ETA | Gargalo |
|---|---|---|---|---|---|---|
| `openai` | gpt-4o-mini | 7/8 campos | 5,3s/req | ~$1,07 | ~20 min | TPM (200K) |
| `groq` | llama-3.1-8b-instant | 7/8 campos | 0,55s/req | $0,00 (free) | ~12 h | TPM (6K) |
| `cerebras` | gpt-oss-120b | **inviável** | — | $0,00 (free) | ~6 dias | TPD (1M) |

**Cerebras descartado:** `gpt-oss-120b` é modelo de raciocínio — gasta ~633 tokens internos antes de gerar a resposta visível. Com `max_tokens=512` retorna tudo null; com `max_tokens=3000` funciona, mas TPD de 1M tokens limita a ~728 bulas/dia. Cerebras removido do `.env.example`.

**OpenAI e Groq têm a mesma qualidade** (7/8 campos preenchidos — o campo "fabricante" frequentemente não aparece na seção de identificação). A diferença é custo vs. tempo.

## Rate limits relevantes

**Groq llama-3.1-8b-instant (free):**
- 30 RPM / 6K TPM / 14.4K RPD / 500K TPD
- Gargalo real é TPM: com ~821 tokens/req → ~7 req/min efetivo
- Para `process_all.py`: usar `--concorrencia 7`

**OpenAI gpt-4o-mini:**
- 500 RPM / 200K TPM (tier 1)
- Sem gargalo prático para este volume

## Implementação

`process/meta_llm.py` usa OpenAI SDK (compatível com OpenAI e Groq via mesma interface).
Funções: `extract_meta_llm()` (síncrono) e `extract_meta_llm_batch()` (async com semáforo).
`max_tokens` por provider definido em `_PROVIDERS` dict (Cerebras precisa de 3000 por causa do reasoning).

Ver [[pipeline-architecture]].
