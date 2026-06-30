# ChatBulário

Dataset de perguntas e respostas em português extraído das bulas do paciente de medicamentos
registrados no [Bulário Eletrônico da ANVISA](https://consultas.anvisa.gov.br), junto com o
pipeline completo de coleta e processamento.

O foco é a **bula do paciente** — texto em linguagem acessível ao cidadão, com estrutura de
perguntas e respostas padronizada pela RDC 47/2009. A norma obriga que cada medicamento
registrado responda às mesmas nove perguntas em ordem fixa, o que cria um alinhamento natural
entre pergunta e resposta aproveitado para geração de pares supervisionados de PLN.

**Dataset no HuggingFace:** [walmeidadf/ChatBulario](https://huggingface.co/datasets/walmeidadf/ChatBulario)
— 68.938 pares pergunta/resposta de 7.930 bulas, com metadados de medicamento denormalizados.

```python
from datasets import load_dataset
ds = load_dataset("walmeidadf/ChatBulario")
```

---

## Pipeline

```
DADOS_ABERTOS_MEDICAMENTOS.csv        (~10k registros ativos da ANVISA)
        │
        ▼  collector.py               Playwright headless + API do bulário
dataset/raw_data/.../pdfs/{reg9}/paciente.pdf   (8.258 PDFs coletados)
        │
        ▼  segment_all.py             extract → split → segment  (grátis, re-rodável)
dataset/work_data/segments.jsonl      1 linha por bula, 9 seções + identificação
        │
        ▼  enrich_all.py              OpenAI Batch API  (1× por bula, cacheado)
dataset/work_data/meta.jsonl          metadados estruturados por registro
        │
        ▼  build_dataset.py           join segments ⋈ meta  (grátis, determinístico)
dataset/work_data/dataset.jsonl       1 linha por (registro × pergunta)
        │
        ▼  export.py                  splits + parquet + upload HuggingFace
dataset/work_data/hf_dataset/*.parquet → walmeidadf/ChatBulario
        │
        ▼  [futuro] embeddings + vector store
RAG / chatbot
```

---

## Etapa 1 — Coleta

### Por que Playwright?

A API do Bulário Eletrônico (`consultas.anvisa.gov.br`) é protegida por Cloudflare. Requisições
diretas com `requests` ou `httpx` retornam 403 — o Cloudflare bloqueia clientes sem fingerprint
de navegador real. A solução é um **Chromium headless real** via Playwright que:

1. Navega para o domínio uma única vez, resolvendo o challenge de forma transparente
2. Injeta e executa `fetch()` de dentro da página já autenticada com `Authorization: Guest`
3. Retorna o JSON da API diretamente, sem parsing de HTML

### Seleção dos registros

A coleta é dirigida pelo campo `NUMERO_REGISTRO_PRODUTO` do CSV de Dados Abertos da ANVISA
(`dados.anvisa.gov.br`), que lista todos os medicamentos com registro ativo. O endpoint do
Bulário usa o parâmetro `filter[numeroRegistro]` para match exato por número de 9 dígitos —
o filtro por nome é apenas por prefixo e não serve para coleta sistemática.

### Robustez e retomada

- Ritmo de 1,5 s + jitter aleatório entre requisições para não saturar a API
- Backoff exponencial em respostas 429 (rate limit) e 503 (servidor sobrecarregado)
- Checkpoint salvo a cada 100 itens — a coleta é totalmente retomável: ao reiniciar, pula
  registros já baixados

**Resultado:** 8.258 PDFs de bulas do paciente coletados de um universo de ~10.420 registros
ativos (diferença explica-se por registros sem bula publicada ou URLs inválidas na API).

---

## Etapa 2 — Processamento

O processamento é dividido em quatro estágios independentes com cadências distintas, evitando
que melhorias em estágios baratos (segmentação) forcem re-execução de estágios caros (LLM).

### A — Extração de texto: `process/extract.py`

Cada PDF é processado com **PyMuPDF** para extração da camada de texto. Bulas escaneadas
(PDFs de imagem sem texto extraível) são detectadas por heurística no número de caracteres
por página e descartadas — 22 bulas, sem OCR nesta versão.

Artefatos do PDF corrigidos deterministicamente antes da segmentação:

- **Hifenização de quebra de linha**: `cirurgião-\ndentista` → `cirurgião-dentista`
  (padrão `(\w)-\n(\w)` com regex)
- **Cabeçalhos e rodapés**: blocos repetidos exigidos pela RDC 47/2009 em toda página (nome
  do produto, número de registro, versão) removidos por regex para não poluir as seções

### B — Isolamento da primeira bula: `process/split.py`

Alguns PDFs agrupam bulas de múltiplas apresentações ou dosagens do mesmo produto. O script
detecta essa situação por contagem de âncoras `IDENTIFICAÇÃO DO MEDICAMENTO` e extrai apenas
a primeira bula, evitando duplicação de conteúdo no dataset.

### C — Segmentação em seções: `process/segment.py`

A RDC 47/2009 exige que cada bula do paciente contenha as mesmas nove perguntas em ordem
fixa. A localização dessas seções usa **fuzzy matching** (`rapidfuzz`, limiar de similaridade
80/100) sobre os títulos padronizados, tolerando variações comuns encontradas nas bulas:

- Numeração isolada em linha separada (`1.` sozinho + até 4 linhas de acumulação para achar `?`)
- Abreviações e grafias alternativas nos títulos
- Variações tipográficas (maiúsculas, pontuação, espaçamento)

O score de cada match (0–100) é preservado no campo `fuzzy_score` do dataset — valores abaixo
de 80 indicam match aproximado e possível imprecisão nos limites da seção.

**Resultados da segmentação (8.258 PDFs):**

| Resultado | Quantidade |
|---|---|
| Segmentadas com sucesso | 8.235 |
| Scans (descartadas) | 22 |
| Erro de arquivo corrompido | 1 |
| **Com 9/9 seções completas** | **7.068 (86%)** |

### D — Enriquecimento por LLM: `enrich_all.py`

A seção de identificação de cada bula (cabeçalho com nome comercial, fabricante, composição,
forma farmacêutica, via de administração, apresentações, uso) é enviada ao `gpt-4o-mini` para
extração de metadados estruturados em JSON.

**Estratégia de execução:** a [OpenAI Batch API](https://platform.openai.com/docs/guides/batch)
foi escolhida em vez da API síncrona por dois motivos práticos:

- A API síncrona tem limite de **10.000 requisições/dia** — insuficiente para 8k+ bulas em
  tempo razoável
- A Batch API não tem esse limite, oferece **50% de desconto** e retorna em até 24h

Para respeitar o limite de **2 milhões de tokens enfileirados por organização** do `gpt-4o-mini`,
as requisições são divididas automaticamente em chunks de 800 (~500k tokens cada). Os IDs dos
batches são salvos em `batch_ids.txt` para acompanhamento e retomada independente de cada chunk.

**Custo total do enriquecimento:** ~US$ 1,07 para 7.930 bulas.

O resultado por registro é cacheado em `meta.jsonl` — o LLM **nunca é re-chamado** para um
registro já enriquecido. Iterar a segmentação não gera nova cobrança.

### E — Build do dataset final: `build_dataset.py`

Join `segments.jsonl ⋈ meta.jsonl` no schema flat do dataset. Determinístico: sempre reescreve
`dataset.jsonl` do zero a partir dos dois artefatos, garantindo consistência com a última
segmentação. Para registros sem meta LLM, os campos de metadados ficam nulos mas os pares
pergunta/resposta são incluídos.

### F — Export e publicação: `export.py`

Converte `dataset.jsonl` em parquet com splits **80/10/10**:

- **Agrupado por medicamento**: todas as perguntas de uma bula ficam no mesmo split, eliminando
  vazamento entre treino e teste
- **Estratificado por `classe_terapeutica`**: preserva a distribuição de áreas terapêuticas
  em cada split; classes com menos de 10 medicamentos vão inteiras para o treino

Faz upload direto ao HuggingFace Hub via `huggingface_hub`, incluindo o `dataset_card.md`
como `README.md` do repositório.

---

## Getting Started

### Pré-requisitos

- [uv](https://docs.astral.sh/uv/) — gerenciador de ambiente Python
- Python 3.14+
- Chave de API OpenAI (para o estágio de enriquecimento LLM)
- Token HuggingFace com permissão de escrita (para publicação)

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
# edite .env e preencha:
# OPENAI_API_KEY  — chave OpenAI com escopos Files (Write) e Batches (Write)
# HF_TOKEN        — token HuggingFace com permissão de escrita
# HF_REPO_ID      — ex: walmeidadf/ChatBulario
```

### Fonte de dados

Baixe o CSV de dados abertos da ANVISA e coloque em `dataset/raw_data/anvisa_tables/`:

```
https://dados.anvisa.gov.br/dados/DADOS_ABERTOS_MEDICAMENTOS.zip
```

### Executando o pipeline completo

```bash
# 1. Coleta (~8k PDFs, retomável)
uv run python collector.py
uv run python status.py              # acompanha progresso

# 2A. Segmentação — grátis, ~5 min para 8k bulas
uv run python segment_all.py

# 2B. Enriquecimento LLM via Batch API (~US$ 1 para 8k bulas)
uv run python enrich_all.py --async          # submete batches, retorna imediatamente
uv run python enrich_all.py --status <id>   # verifica conclusão (até 24h)
uv run python enrich_all.py --retrieve <id> # baixa resultado e grava meta.jsonl

# 2C. Build do dataset final
uv run python build_dataset.py

# 2D. Export + publicação no HuggingFace
uv run python export.py              # gera parquets localmente
uv run python export.py --upload     # gera e publica (requer HF_TOKEN no .env)
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
export.py               Estágio D: dataset.jsonl → parquet + upload HuggingFace
dataset_card.md         Dataset card do HuggingFace (publicado como README.md no HF)
migrate_meta.py         One-time: extrai meta do dataset.jsonl legado → meta.jsonl
benchmark_providers.py  Compara providers LLM (qualidade, latência, custo)
process_all.py          [deprecated] Pipeline monolítico original

process/
  extract.py            Extração de texto e limpeza de artefatos (PyMuPDF)
  split.py              Isolamento da primeira bula em PDFs multi-bula
  segment.py            Segmentação nas 9 seções RDC 47/2009 (fuzzy matching)
  meta_llm.py           Extração de metadados via LLM (prompt + parsing)
  structure.py          Orquestrador por PDF individual (inspeção manual)

docs/
  pipeline-architecture.md   Decisões de arquitetura do pipeline
  proximos-passos.md         Continuidade: pendências, melhorias planejadas
  anvisa-bulario-api.md      Detalhes da API e bypass Cloudflare
  llm-providers.md           Benchmark de providers LLM (OpenAI vs Groq vs Cerebras)

dataset/                Não versionado — gerado localmente
  raw_data/
    anvisa_tables/           CSV fonte da ANVISA
    anvisa_medicine_leaflets/
      pdfs/{reg9}/paciente.pdf   PDFs coletados
      index.jsonl              Índice de bulas coletadas
      checkpoint.json          Estado da coleta (para retomada)
  work_data/
    segments.jsonl       Artefato A: 1 linha/bula com seções segmentadas
    meta.jsonl           Artefato B: metadados LLM (NUNCA apagar — evita re-cobrança)
    qc.jsonl             Métricas de qualidade por bula (cobertura de seções)
    dataset.jsonl        Artefato C: output final, 1 linha por registro × pergunta
    hf_dataset/          Artefato D: train/validation/test.parquet para o HuggingFace
    batch_ids.txt        IDs dos batches OpenAI submetidos (para acompanhamento)
```

---

## Schema do dataset

Cada linha do `dataset.jsonl` / parquet:

```jsonc
{
  // identificação
  "registro": "100290031",            // número de registro ANVISA (9 dígitos)
  "nome_produto": "RENITEC®",

  // metadados do CSV de Dados Abertos (ANVISA)
  "categoria_regulatoria": "Referência",
  "principio_ativo_csv": "MALEATO DE ENALAPRIL",
  "classe_terapeutica": "INIBIDOR DA ECA",
  "expediente": "...",

  // metadados extraídos pelo LLM da seção de identificação
  "nome_comercial": "RENITEC®",
  "fabricante": "ORGANON FARMACÊUTICA LTDA.",
  "principio_ativo": "maleato de enalapril",
  "forma_farmaceutica": "comprimidos",
  "via_administracao": "oral",
  "apresentacao": "caixas com 30 comprimidos de 5, 10 ou 20 mg",
  "composicao": "Cada comprimido contém maleato de enalapril equivalente a 5 mg...",
  "uso": "adulto",

  // par pergunta/resposta (9 linhas por bula, uma por seção)
  "secao_id": 1,
  "pergunta": "Para que este medicamento é indicado?",
  "resposta": "Seu médico prescreveu RENITEC® para controlar a pressão alta...",
  "fuzzy_score": 100.0  // confiança do match da seção (0–100)
}
```

---

## Decisões de arquitetura

O pipeline separa segmentação (grátis, re-rodável) de enriquecimento LLM (pago, 1× por bula)
em artefatos independentes. A motivação: no design original (`process_all.py`), qualquer
ajuste no `segment.py` forçava re-pagar o LLM para todas as bulas. Com os três artefatos
intermediários (`segments.jsonl`, `meta.jsonl`, `dataset.jsonl`), é possível:

- Iterar a segmentação sem custo adicional
- Re-enriquecer só os registros novos (o LLM pula o que já está em `meta.jsonl`)
- Rebuild instantâneo do `dataset.jsonl` a qualquer momento

Detalhes em [`docs/pipeline-architecture.md`](docs/pipeline-architecture.md).

---

## Stack

| Função | Biblioteca |
|---|---|
| Coleta (bypass Cloudflare) | [Playwright](https://playwright.dev/python/) |
| Extração de PDF | [PyMuPDF](https://github.com/pymupdf/PyMuPDF) |
| Fuzzy matching | [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) |
| LLM (metadados) | [OpenAI SDK](https://github.com/openai/openai-python) + Batch API |
| Export | [pandas](https://pandas.pydata.org/) + [pyarrow](https://arrow.apache.org/docs/python/) |
| Publicação | [huggingface_hub](https://huggingface.co/docs/huggingface_hub/) |
| Ambiente | [uv](https://docs.astral.sh/uv/) |

---

## Licença

- **Código**: MIT
- **Dataset compilado** (segmentação, estrutura, metadados): CC BY 4.0
- **Texto das bulas**: pertence aos respectivos fabricantes, reproduzido de fonte pública
  para fins de pesquisa. Veja a [seção de licença do dataset card](dataset_card.md#licença-e-direitos)
  para a discussão completa.

## Fonte dos dados

- Bulário Eletrônico da ANVISA — `https://consultas.anvisa.gov.br`
- Dados Abertos da ANVISA — `https://dados.anvisa.gov.br/dados/`
