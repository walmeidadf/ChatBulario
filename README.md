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
        ▼  process_all.py             extract → split → segment → meta_llm
dataset/work_data/dataset.jsonl       1 linha por (registro × pergunta)
dataset/work_data/qc.jsonl            métricas de qualidade por bula
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

### Etapa 2 — Processamento

Cada PDF passa por quatro transformações em sequência:

| Módulo | O que faz |
|---|---|
| `process/extract.py` | PyMuPDF → texto limpo; remove cabeçalhos/rodapés/páginas; reconecta hifenização de quebra de linha |
| `process/split.py` | Detecta PDFs multi-bula e extrai apenas a primeira |
| `process/segment.py` | Fuzzy matching (rapidfuzz) localiza as 9 perguntas padrão da RDC 47/2009 e extrai cada resposta |
| `process/meta_llm.py` | LLM extrai metadados estruturados da seção de identificação (nome comercial, fabricante, princípio ativo, etc.) |

O output é um JSONL **flat**: uma linha por par (registro × pergunta), com todos os
metadados embutidos em cada linha — sem joins necessários para uso downstream.

### Etapa 3 — RAG / chatbot *(futuro)*

Embeddings, vector store e motor de chat sobre o dataset estruturado.

---

## Getting Started

### Pré-requisitos

- [uv](https://docs.astral.sh/uv/) (gerenciador de ambiente Python)
- Python 3.14+
- Chave de API: OpenAI **ou** Groq (ver seção [LLM Provider](#llm-provider))

### Instalação

```bash
git clone https://github.com/<seu-usuario>/ChatBulario.git
cd ChatBulario

uv sync                              # cria .venv e instala dependências
uv run playwright install chromium   # baixa o Chromium para coleta
```

### Configuração

```bash
cp .env.example .env
# edite .env e preencha OPENAI_API_KEY ou GROQ_API_KEY
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
# teste rápido (5 bulas, sem LLM)
uv run python process_all.py --limite 5 --sem-llm

# dataset completo
uv run python process_all.py
```

---

## LLM Provider

A extração de metadados estruturados usa um LLM configurável via `.env`:

| Provider | Modelo | Custo | ETA (5k bulas) | Gargalo |
|---|---|---|---|---|
| `openai` | `gpt-4o-mini` | ~$1,07 | ~20 min | TPM (200K) |
| `groq` | `llama-3.1-8b-instant` | gratuito | ~12 h | TPM (6K) |

Para usar Groq com a concorrência ajustada ao rate limit real:

```bash
# .env
LLM_PROVIDER=groq

uv run python process_all.py --concorrencia 7
```

Para comparar providers em uma amostra pequena:

```bash
uv run python benchmark_providers.py --n 10 --providers openai groq
```

---

## Estrutura do repositório

```
collector.py            Coleta as bulas do paciente (Playwright + API ANVISA)
import_existing.py      Importa PDFs já baixados para o índice/checkpoint
status.py               Mostra o progresso da coleta
process_all.py          Pipeline de processamento: PDF → dataset.jsonl
benchmark_providers.py  Compara providers LLM (qualidade, latência, custo)

process/
  extract.py            Extração de texto (PyMuPDF)
  split.py              Isolamento da primeira bula em PDFs multi-bula
  segment.py            Segmentação nas 9 seções RDC 47/2009 (fuzzy matching)
  meta_llm.py           Extração de metadados via LLM (OpenAI / Groq)
  structure.py          Orquestrador: une todas as etapas por PDF

dataset/                Não versionado — gerado localmente (ver Getting Started)
  raw_data/
    anvisa_tables/      CSV fonte da ANVISA
    anvisa_medicine_leaflets/
      pdfs/{reg9}/paciente.pdf
      index.jsonl       Índice de bulas coletadas
      checkpoint.json   Estado da coleta
  work_data/
    dataset.jsonl       Output principal (1 linha por registro × pergunta)
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

  // metadados extraídos pelo LLM
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
| LLM (metadados) | [OpenAI SDK](https://github.com/openai/openai-python) (OpenAI / Groq) |
| Ambiente | [uv](https://docs.astral.sh/uv/) |

## Fonte dos dados

- Bulário Eletrônico da ANVISA — `https://consultas.anvisa.gov.br`
- Dados Abertos da ANVISA — `https://dados.anvisa.gov.br/dados/`
