---
name: project-status
description: Estado atual do projeto ChatBulário e próximos passos
metadata: 
  node_type: memory
  type: project
  originSessionId: 0ee6a010-2d25-4b93-8ade-994bfd586e1d
---

Atualizado em 2026-06-23.

## Estado da coleta

- **Universo:** ~10.295 registros ativos no CSV da ANVISA
- **Coletados:** ~5.285 PDFs disponíveis em `dataset/raw_data/anvisa_medicine_leaflets/pdfs/{reg9}/paciente.pdf`
- **Índice:** `index.jsonl` com 7.604 entradas (inclui registros sem PDF disponível na API)
- Coleta estava em andamento (67%+) no fim da sessão anterior; continuando em background

## Estado do processamento

- **Pipeline completo:** implementado e testado em amostras pequenas
- **Não rodado em escala ainda** — usuário vai rodar `process_all.py` após coleta terminar
- Teste `--sem-llm` com 5 bulas: funcionou, 60% com 9/9 seções
- Provider escolhido para o run completo: **OpenAI** (`gpt-4o-mini`, ~$1,07, ~20 min)
  - Alternativa gratuita: Groq (~12h, `--concorrencia 7`)

## Próxima sessão

O usuário vai iniciar a sessão após a coleta terminar e rodar `process_all.py`.
Esperar: ~5.285 bulas × 9 perguntas = ~47.500 linhas no `dataset.jsonl`.

Coisas que podem precisar de atenção na próxima sessão:
- QC do processamento: checar `qc.jsonl` para bulas com < 9/9 seções (esperado ~19%)
- Bulas com `provavel_scan=True` são puladas — decisão futura sobre OCR
- Possível ajuste fino no prompt do `meta_llm.py` dependendo da qualidade observada em escala

## Repositório

- Branch: `main` — sincronizado com `git@github.com:walmeidadf/ChatBulario.git`
- Último commit: `14d32c1` — pipeline completo
- `dataset/` e `archive/` não versionados (gitignore)

Ver [[pipeline-architecture]] e [[llm-providers]].
