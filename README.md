# ChatBulário

Dataset NLP em português extraído das bulas de medicamentos do
[Bulário Eletrônico da ANVISA](https://consultas.anvisa.gov.br), e os componentes de
IA generativa (RAG, busca semântica, chatbot) construídos sobre ele.

O foco é a **bula do paciente** — texto em linguagem acessível, com estrutura de
perguntas e respostas padronizada pela RDC 47/2009.

Este projeto é aberto: código, dataset e modelos serão publicados para pesquisadores,
estudantes e desenvolvedores interessados em GenAI aplicada ao domínio de saúde.

---

## Pipeline

```
DADOS_ABERTOS_MEDICAMENTOS.csv        (~10k registros ativos da ANVISA)
        │
        ▼  collector.py               Playwright headless + API do bulário
dataset/raw_data/.../pdfs/{reg9}/paciente.pdf
        │
        ▼  segment_all.py             extract → split → segment  (grátis, re-rodável)
dataset/work_data/segments.jsonl      1 linha por bula com seções + identificação
        │
        ▼  enrich_all.py              OpenAI Batch API  (1× por bula, sem RPD)
dataset/work_data/meta.jsonl          metadados LLM por registro
        │
        ▼  build_dataset.py           join segments ⋈ meta  (grátis, determinístico)
dataset/work_data/dataset.jsonl       1 linha por (registro × pergunta)
        │
        ▼  [futuro] embeddings + vector store
RAG / chatbot
```

### Etapa 1 — Coleta

A API da ANVISA (`consultas.anvisa.gov.br`) é protegida por Cloudflare — requisições
diretas retornam 403. A solução é um **Chromium headless real** (Playwright) que passa
pelo challenge e executa `fetch()` de dentro da página com `Authorization: Guest`.

A coleta é dirigida por `NUMERO_REGISTRO_PRODUTO` do CSV de dados abertos, usando
`filter[numeroRegistro]` (match exato — o filtro por nome é apenas por prefixo).

Ritmo: 1,5 s + jitter entre requisições, backoff exponencial em 503/429, checkpoint a
cada 100 itens. Retomável: ao reiniciar, pula registros já coletados.

### Etapa 2 — Processamento (3 estágios independentes)

O processamento é separado em três estágios com cadências distintas:

**A — Segmentação** (`segment_all.py`, grátis):

| Módulo | O que faz |
|---|---|
| `process/extract.py` | PyMuPDF → texto limpo; remove cabeçalhos/rodapés; reconecta hifenização de quebra de linha |
| `process/split.py` | Detecta PDFs multi-bula e extrai apenas a primeira |
| `process/segment.py` | Fuzzy matching (rapidfuzz) localiza as 9 perguntas da RDC 47/2009 |

Output: `segments.jsonl` — 1 linha/bula com seções e texto de identificação.
Re-rodável à vontade ao tunar a segmentação. Cobertura: ~87% das bulas com 9/9 seções.

**B — Enriquecimento LLM** (`enrich_all.py`, OpenAI Batch API):

Lê `segments.jsonl`, extrai metadados estruturados da seção de identificação de cada bula
via LLM, e cacheia em `meta.jsonl` (1 linha/registro). Usa a **Batch API da OpenAI**:
sem limite de RPD, até 50.000 requisições por submissão, 50% de desconto, resultado em até 24h.
**O LLM nunca é re-chamado para um registro já cacheado** — iterar a segmentação não re-bilha.

**C — Build** (`build_dataset.py`, grátis):

Join `segments.jsonl ⋈ meta.jsonl` → `dataset.jsonl` no schema flat atual.
Determinístico: sempre reescreve do zero, sempre consistente com a última segmentação.

### Etapa 3 — RAG / chatbot *(futuro)*

Embeddings, vector store e motor de chat sobre o dataset estruturado.

---

## Getting Started

### Pré-requisitos

- [uv](https://docs.astral.sh/uv/) (gerenciador de ambiente Python)
- Python 3.14+
- Chave de API OpenAI (para o estágio B)

### Instalação

```bash
git clone https://github.com/walmeidadf/ChatBulario.git
cd ChatBulario

uv sync                              # cria .venv e instala dependências
uv run playwright install chromium   # baixa o Chromium para coleta
```

### Configuração

```bash
cp .env.example .env
# edite .env e preencha OPENAI_API_KEY
```

### Fonte de dados

Baixe o CSV de dados abertos da ANVISA e coloque em `dataset/raw_data/anvisa_tables/`:

```
https://dados.anvisa.gov.br/dados/DADOS_ABERTOS_MEDICAMENTOS.zip
```

### Coleta

```bash
uv run python collector.py           # coleta todas as bulas (retomável)
uv run python status.py              # acompanha o progresso
```

### Processamento

```bash
# A) Segmentação — grátis, ~5 min para 8k bulas
uv run python segment_all.py

# B) Enriquecimento LLM via Batch API
uv run python enrich_all.py --async          # submete o batch, imprime o batch_id
uv run python enrich_all.py --status <id>   # verifica quando concluiu
uv run python enrich_all.py --retrieve <id> # baixa resultado e grava meta.jsonl

# C) Build do dataset final
uv run python build_dataset.py
```

---

## Estrutura do repositório

```
collector.py            Coleta as bulas do paciente (Playwright + API ANVISA)
import_existing.py      Importa PDFs já baixados para o índice/checkpoint
status.py               Mostra o progresso da coleta
segment_all.py          Estágio A: PDF → segments.jsonl + qc.jsonl (sem LLM)
enrich_all.py           Estágio B: segments.jsonl → meta.jsonl (OpenAI Batch API)
build_dataset.py        Estágio C: join segments ⋈ meta → dataset.jsonl
migrate_meta.py         One-time: extrai meta do dataset.jsonl legado → meta.jsonl
benchmark_providers.py  Compara providers LLM (qualidade, latência, custo)
process_all.py          [deprecated] Pipeline monolítico original

process/
  extract.py            Extração de texto (PyMuPDF)
  split.py              Isolamento da primeira bula em PDFs multi-bula
  segment.py            Segmentação nas 9 seções RDC 47/2009 (fuzzy matching)
  meta_llm.py           Extração de metadados via LLM (OpenAI Batch API / síncrono)
  structure.py          Orquestrador por PDF individual (inspeção manual)

dataset/                Não versionado — gerado localmente (ver Getting Started)
  raw_data/
    anvisa_tables/      CSV fonte da ANVISA
    anvisa_medicine_leaflets/
      pdfs/{reg9}/paciente.pdf
      index.jsonl       Índice de bulas coletadas
      checkpoint.json   Estado da coleta
  work_data/
    segments.jsonl      Artefato A: 1 linha/bula com seções segmentadas
    meta.jsonl          Artefato B: metadados LLM por registro (NUNCA re-gerar)
    dataset.jsonl       Artefato C: output final, 1 linha por registro × pergunta
    qc.jsonl            Métricas de qualidade por bula
```

---

## Schema do dataset

Cada linha do `dataset.jsonl`:

```jsonc
{
  // identificação
  "registro": "100290031",
  "nome_produto": "RENITEC®",

  // metadados do CSV (ANVISA dados abertos)
  "categoria_regulatoria": "Referência",
  "principio_ativo_csv": "MALEATO DE ENALAPRIL",
  "classe_terapeutica": "INIBIDOR DA ECA",

  // metadados extraídos pelo LLM (via Batch API)
  "nome_comercial": "RENITEC®",
  "fabricante": "ORGANON FARMACÊUTICA LTDA.",
  "principio_ativo": "maleato de enalapril",
  "forma_farmaceutica": "comprimidos",
  "via_administracao": "oral",
  "apresentacao": "caixas com 30 comprimidos de 5, 10 ou 20 mg",
  "composicao": "Cada comprimido contém 5 mg de maleato de enalapril...",
  "uso": "adulto",

  // par pergunta/resposta (9 por bula)
  "secao_id": 1,
  "pergunta": "Para que este medicamento é indicado?",
  "resposta": "Seu médico prescreveu RENITEC® para controlar a pressão alta...",
  "fuzzy_score": 100.0
}
```

---

## Stack

| Função | Biblioteca |
|---|---|
| Coleta (bypass Cloudflare) | [Playwright](https://playwright.dev/python/) |
| Extração de PDF | [PyMuPDF](https://github.com/pymupdf/PyMuPDF) |
| Fuzzy matching | [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) |
| LLM (metadados) | [OpenAI SDK](https://github.com/openai/openai-python) + Batch API |
| Ambiente | [uv](https://docs.astral.sh/uv/) |

## Fonte dos dados

- Bulário Eletrônico da ANVISA — `https://consultas.anvisa.gov.br`
- Dados Abertos da ANVISA — `https://dados.anvisa.gov.br/dados/`
