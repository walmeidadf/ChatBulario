---
name: project-status
description: Estado atual do projeto ChatBulário e próximos passos
metadata:
  node_type: memory
  type: project
  originSessionId: 0ee6a010-2d25-4b93-8ade-994bfd586e1d
---

Atualizado em 2026-06-24.

## Estado da coleta

- **Universo:** ~10.295 registros ativos no CSV da ANVISA
- **Coletados:** ~8.800 PDFs (index.jsonl: 10.420 entradas; PDFs disponíveis: 8.258)
- **Coleta concluída.** 1.602 registros confirmados sem bula na ANVISA (após re-tentativa).
- 589 bulas recuperadas na re-tentativa de 2026-06-24 (eram falsos negativos).

## Estado do processamento

Pipeline em 3 estágios implementado e commitado em 2026-06-24.

| Estágio | Script | Status |
|---|---|---|
| A — Segmentação | `segment_all.py` | Pendente (a rodar) |
| B — Enriquecimento LLM | `enrich_all.py` (Batch API) | Pendente |
| C — Build | `build_dataset.py` | Pendente |
| Migração legado | `migrate_meta.py` | Pendente — rodar PRIMEIRO |

**LLM já pago:** 3.597 registros têm metadados LLM no `dataset.jsonl` legado.
`migrate_meta.py` extrai esses dados para `meta.jsonl` antes de rodar o pipeline novo.

**Sequência de execução para a próxima sessão:**
```bash
uv run python migrate_meta.py        # preserva LLM já pago → meta.jsonl
uv run python segment_all.py         # ~5 min, grátis
uv run python enrich_all.py --async  # submete Batch API, retorna batch_id
# ... aguarda até 24h ...
uv run python enrich_all.py --retrieve <batch_id>
uv run python build_dataset.py
```

## Custo acumulado OpenAI

- ~$0,88 até 2026-06-24 (runs com API síncrona, parcial)
- Batch API: ~$0,70 estimado para o lote completo (~4.600 bulas novas) com 50% de desconto

## Repositório

- Branch única: `main` — `git@github.com:walmeidadf/ChatBulario.git`
- `dataset/` não versionado (gitignore)

Ver [[pipeline-architecture]] e [[llm-providers]].
