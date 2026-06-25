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
- Coleta concluída. 1.602 registros confirmados sem bula na ANVISA (após re-tentativa).
- 589 bulas recuperadas na re-tentativa (eram falsos negativos por falha transitória).

## Estado do processamento

- **Pipeline:** implementado com escrita incremental e progresso visual (sessão 2026-06-24)
- **Run em andamento:** `process_all.py` rodando em 2026-06-24 (~8.230 bulas)
- Provider: **OpenAI** (`gpt-4o-mini`); custo acumulado até agora: ~$0,88
- Risco: RPD 10.000/dia pode esgotar com retries — novo código falha rápido em RPD e
  grava o que foi feito; rodar novamente retoma de onde parou.

## Próxima sessão

Verificar output do run atual:
- Quantas bulas processadas (`wc -l dataset/work_data/dataset.jsonl`)
- QC: checar `qc.jsonl` para bulas com < 9/9 seções (esperado ~19%)
- Registros com `nome_comercial=null` → RPD esgotou no meio; rodar `--re-run`
- Bulas `provavel_scan=True` (~22) puladas — decisão futura sobre OCR

## Repositório

- Branch única: `main` — `git@github.com:walmeidadf/ChatBulario.git`
- `dataset/` não versionado (gitignore)

Ver [[pipeline-architecture]] e [[llm-providers]].
