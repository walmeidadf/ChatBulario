# Próximos Passos — ChatBulário

Documento de continuidade para sessões futuras. Atualizado após a geração do dataset
e do pipeline de publicação no HuggingFace.

---

## Estado atual (junho 2026)

### O que foi feito

- [x] Coleta: 8.258 PDFs de bulas do paciente (Playwright + API ANVISA, bypass Cloudflare)
- [x] `segment_all.py` — segmentação nas 9 seções RDC 47/2009 (8.187 bulas únicas)
- [x] `enrich_all.py` — metadados via OpenAI Batch API (7.930 registros, chunks de 800)
- [x] `build_dataset.py` — join → `dataset.jsonl` (68.938 pares pergunta/resposta)
- [x] `export.py` — splits 80/10/10 agrupados por medicamento + parquet + upload HF
- [x] `dataset_card.md` — card do HuggingFace com frontmatter YAML
- [ ] **Upload efetivo ao HuggingFace** — rodar `uv run python export.py --upload` com `HF_TOKEN`

### Números da v1

| Métrica | Valor |
|---|---|
| Pares pergunta/resposta | 68.938 |
| Medicamentos (bulas) | 7.930 |
| Bulas com 9/9 seções | ~88% |
| Classes terapêuticas | 397 |
| Split train / val / test (bulas) | 6.614 / 658 / 658 |

### Decisões de design (não reverter sem justificativa)

1. **Config único flat** — 1 linha por par pergunta/resposta, com metadados da bula
   denormalizados em cada linha (decisão do usuário; bom para instruction-tuning/RAG)
2. **Split agrupado por `registro`** — as 9 perguntas de uma bula ficam sempre no mesmo
   split, para não vazar respostas do mesmo medicamento entre treino e teste
3. **Estratificação por `classe_terapeutica`** — classes com < 10 bulas vão inteiras para treino
4. **CC BY 4.0 + atribuição à ANVISA** — bulas são documentos públicos

---

## Pendências técnicas / qualidade dos dados

- [ ] **`fabricante` em ~27%** — o LLM extrai da seção de identificação, mas o fabricante
      costuma estar nos *dizeres legais* (final da bula). Para melhorar: incluir
      `dizeres_legais` (já capturado em `segments.jsonl`) no prompt do `meta_llm.py`
- [ ] **~20% das bulas com metadados LLM 100% nulos** — identificação curta/atípica.
      Investigar se vale um segundo passo de extração com mais contexto
- [ ] **~12% das bulas com < 9 seções** — layouts atípicos no fuzzy matching do `segment.py`
- [ ] **22 scans descartados** — sem OCR nesta versão; decisão futura
- [ ] **`segment_all.py` duplica em re-run** — usa append sem dedup; gerou 71 duplicatas
      nesta sessão (corrigidas manualmente). Adicionar dedup automático se for re-rodar

---

## Próximas melhorias

### Dataset
- [ ] Publicar v1 no HuggingFace (`export.py --upload`)
- [ ] Calcular e exportar `n_tokens_resposta` no schema
- [ ] Avaliar config adicional agrupado por bula (9 seções aninhadas) se houver demanda

### Pipeline
- [ ] Reprocessar metadados com `dizeres_legais` no prompt para subir cobertura de `fabricante`
- [ ] Dedup automático no `segment_all.py`

### Etapa 3 — RAG / chatbot (futuro)
- [ ] Embeddings + vector store sobre as respostas
- [ ] Motor de chat com citação da bula de origem (`registro` + `secao_id`)

---

## Fluxo completo (re-rodar)

```bash
uv run python segment_all.py
uv run python enrich_all.py --async          # + --status / --retrieve por batch
uv run python build_dataset.py
uv run python export.py --upload
```
