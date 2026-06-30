---
language:
  - pt
license: cc-by-4.0
task_categories:
  - question-answering
  - text-generation
task_ids:
  - closed-domain-qa
pretty_name: ChatBulário
size_categories:
  - 10K<n<100K
tags:
  - medical
  - pharmaceutical
  - portuguese
  - brazil
  - question-answering
  - drug-leaflets
  - anvisa
  - rdc-47-2009
configs:
  - config_name: default
    data_files:
      - split: train
        path: data/train.parquet
      - split: validation
        path: data/validation.parquet
      - split: test
        path: data/test.parquet
---

# ChatBulário

Dataset de perguntas e respostas em **português brasileiro** construído a partir das bulas do
paciente de medicamentos registrados no Bulário Eletrônico da ANVISA. Cada exemplo corresponde
a uma das nove seções padronizadas pela RDC 47/2009 — textos escritos em linguagem acessível ao
paciente, acompanhados de metadados do medicamento.

> **Este dataset não é produzido nem endossado pela ANVISA.** É uma compilação independente
> de documentos públicos disponibilizados pela agência.

Código-fonte e pipeline de construção:
[github.com/walmeidadf/ChatBulario](https://github.com/walmeidadf/ChatBulario)

---

## Motivação

A bula do paciente brasileira tem uma estrutura única: a **RDC 47/2009** obriga que todos os
medicamentos registrados apresentem suas informações em torno de **nove perguntas fixas**,
escritas na segunda pessoa e em linguagem não-técnica. Essa uniformidade cria um alinhamento
natural entre pergunta e resposta — raro em textos médicos — que o ChatBulário explora para gerar
pares supervisionados de alta qualidade.

O dataset é adequado para:

- **Question answering de domínio fechado** — dada uma pergunta da RDC 47/2009 e metadados de
  um medicamento, recuperar ou gerar a resposta correta
- **Instruction-tuning** — fine-tuning de LLMs para o domínio farmacêutico em português
- **RAG / busca semântica** — base de conhecimento estruturada para chatbots de informação
  sobre medicamentos

---

## Fonte dos dados

As bulas são disponibilizadas publicamente pela ANVISA em dois pontos:

| Fonte | URL | Uso neste dataset |
|---|---|---|
| Bulário Eletrônico | `consultas.anvisa.gov.br` | Download dos PDFs das bulas do paciente |
| Dados Abertos ANVISA | `dados.anvisa.gov.br/dados/DADOS_ABERTOS_MEDICAMENTOS.zip` | Catálogo com metadados de ~10k medicamentos ativos |

A coleta abrangeu o universo de medicamentos com número de registro ativo no CSV de Dados
Abertos, resultando em **8.258 PDFs** de bulas do paciente baixados.

---

## Como foi construído

### 1. Coleta

A API do Bulário (`consultas.anvisa.gov.br`) é protegida por Cloudflare. A solução utilizada foi
um **Chromium headless real** via Playwright, que resolve o challenge de forma transparente e
executa `fetch()` de dentro da página com `Authorization: Guest`. A coleta é dirigida pelo campo
`NUMERO_REGISTRO_PRODUTO` do CSV, com ritmo de 1,5 s + jitter entre requisições e backoff
exponencial em erros 429/503.

### 2. Extração de texto (PDF → texto)

Cada PDF foi processado com **PyMuPDF** para extração de texto. Artefatos corrigidos
deterministicamente:
- Cabeçalhos e rodapés padrão RDC 47/2009 removidos por regex
- Hifenização de quebra de linha reconstituída (`cirurgião-\ndentista` → `cirurgião-dentista`)
- PDFs multi-bula (alguns registros agrupam bulas de diferentes apresentações): isolada apenas a
  primeira bula por split em âncora textual
- Bulas escaneadas (sem camada de texto) descartadas — **22 bulas**, sem OCR nesta versão

### 3. Segmentação nas 9 seções (fuzzy matching)

A localização das seções usa **fuzzy matching** (biblioteca `rapidfuzz`, limiar 80) sobre os
títulos padronizados da RDC 47/2009. O algoritmo tolera variações tipográficas comuns nas bulas:
numeração isolada em linha separada, abreviações e grafias alternativas.

**Cobertura:** 88% das bulas com as 9 seções completas; 12% com seções parciais (layouts
atípicos) — incluídas no dataset com as seções detectadas.

### 4. Enriquecimento por LLM

A seção de identificação de cada bula (cabeçalho com nome, fabricante, composição, forma
farmacêutica etc.) foi enviada ao `gpt-4o-mini` via **OpenAI Batch API** para extração de
metadados estruturados. A Batch API foi escolhida por não ter limite de requisições por dia e
oferecer 50% de desconto sobre a API síncrona.

| Campo extraído | Cobertura |
|---|---|
| `forma_farmaceutica`, `apresentacao` | ~78% |
| `principio_ativo`, `composicao`, `uso` | ~77% |
| `nome_comercial` | ~72% |
| `fabricante` | ~27% |

O `fabricante` tem cobertura baixa porque aparece predominantemente nos *dizeres legais* (final
da bula), não na seção de identificação processada. Cerca de 20% das bulas têm metadados LLM
inteiramente nulos (identificação muito curta ou atípica). **Os pares pergunta/resposta — o
núcleo do dataset — têm cobertura completa.**

---

## Estrutura do dataset

### Configuração e splits

Config único `default`, formato flat — 1 linha por par pergunta/resposta, com os metadados da
bula denormalizados em cada linha (sem necessidade de join). Splits **80/10/10 agrupados por
medicamento**: todas as perguntas de uma mesma bula caem no mesmo split, eliminando vazamento
entre treino e teste. Estratificado por `classe_terapeutica`; classes com menos de 10
medicamentos vão inteiras para o treino.

| Split | Pares Q/A | Medicamentos |
|---|---|---|
| `train` | 57.460 | 6.614 |
| `validation` | 5.764 | 658 |
| `test` | 5.714 | 658 |
| **Total** | **68.938** | **7.930** |

### As 9 seções da RDC 47/2009

| `secao_id` | Pergunta |
|---|---|
| 1 | Para que este medicamento é indicado? |
| 2 | Como este medicamento funciona? |
| 3 | Quando não devo usar este medicamento? |
| 4 | O que devo saber antes de usar este medicamento? |
| 5 | Onde, como e por quanto tempo posso guardar este medicamento? |
| 6 | Como devo usar este medicamento? |
| 7 | O que devo fazer quando eu me esquecer de usar este medicamento? |
| 8 | Quais os males que este medicamento pode me causar? |
| 9 | O que fazer se alguém usar uma quantidade maior do que a indicada? |

### Campos

| Campo | Tipo | Origem | Descrição |
|---|---|---|---|
| `registro` | `str` | ANVISA | Número de registro do medicamento (9 dígitos) |
| `nome_produto` | `str\|null` | ANVISA/LLM | Nome comercial do medicamento |
| `categoria_regulatoria` | `str\|null` | CSV ANVISA | `Referência`, `Genérico`, `Similar`, etc. |
| `principio_ativo_csv` | `str\|null` | CSV ANVISA | Princípio ativo (nomenclatura oficial do CSV) |
| `classe_terapeutica` | `str\|null` | CSV ANVISA | Ex: `INIBIDOR DA ECA`, `ANTIDEPRESSIVOS` |
| `expediente` | `str\|null` | CSV ANVISA | Número do expediente regulatório |
| `nome_comercial` | `str\|null` | LLM | Nome comercial extraído da bula |
| `fabricante` | `str\|null` | LLM | Empresa fabricante (~27% cobertura — ver acima) |
| `principio_ativo` | `str\|null` | LLM | Princípio ativo (texto livre da bula) |
| `forma_farmaceutica` | `str\|null` | LLM | Ex: `comprimidos`, `solução oral` |
| `via_administracao` | `str\|null` | LLM | Ex: `oral`, `tópica`, `intravenosa` |
| `apresentacao` | `str\|null` | LLM | Embalagens e dosagens disponíveis |
| `composicao` | `str\|null` | LLM | Composição qualitativa/quantitativa |
| `uso` | `str\|null` | LLM | `adulto`, `pediátrico` ou `adulto e pediátrico` |
| `secao_id` | `int` | segmentação | Número da seção/pergunta (1–9) |
| `pergunta` | `str` | bula | Texto da pergunta padronizada |
| `resposta` | `str` | bula | Texto da resposta extraído da bula |
| `fuzzy_score` | `float` | segmentação | Confiança do match da seção (0–100); 100 = match exato |

### Exemplo

```python
{
  "registro": "100290031",
  "nome_produto": "RENITEC®",
  "categoria_regulatoria": "Referência",
  "principio_ativo_csv": "MALEATO DE ENALAPRIL",
  "classe_terapeutica": "INIBIDOR DA ECA",
  "nome_comercial": "RENITEC®",
  "fabricante": "ORGANON FARMACÊUTICA LTDA.",
  "principio_ativo": "maleato de enalapril",
  "forma_farmaceutica": "comprimidos",
  "via_administracao": "oral",
  "apresentacao": "caixas com 30 comprimidos de 5, 10 ou 20 mg",
  "composicao": "Cada comprimido contém maleato de enalapril equivalente a 5 mg...",
  "uso": "adulto",
  "secao_id": 1,
  "pergunta": "Para que este medicamento é indicado?",
  "resposta": "Seu médico prescreveu RENITEC® para controlar a pressão alta...",
  "fuzzy_score": 100.0
}
```

---

## Como usar

```python
from datasets import load_dataset

ds = load_dataset("walmeidadf/ChatBulario")

# Todas as indicações (seção 1) do split de treino
indicacoes = ds["train"].filter(lambda x: x["secao_id"] == 1)

# Todas as perguntas de um medicamento específico
renitec = ds["train"].filter(lambda x: x["registro"] == "100290031")

# Só registros com fuzzy_score alto (match exato de seção)
alta_qualidade = ds["train"].filter(lambda x: x["fuzzy_score"] >= 90)

# Antidepressivos — todas as seções
antidepressivos = ds["train"].filter(
    lambda x: x["classe_terapeutica"] == "ANTIDEPRESSIVOS"
)
```

---

## Considerações de uso

- **Não é aconselhamento médico.** As respostas reproduzem o texto das bulas para fins de
  pesquisa em PLN. Não devem ser usadas como fonte clínica sem validação profissional.
- Consulte `fuzzy_score` para filtrar respostas de baixa confiança: scores abaixo de 80
  indicam que a seção foi localizada por match aproximado e pode haver imprecisão nos limites.
- ~12% das bulas têm menos de 9 seções detectadas (layouts atípicos) — incluídas com as
  seções disponíveis.
- 22 bulas escaneadas foram descartadas por falta de camada de texto; não há OCR nesta versão.
- Os metadados extraídos por LLM podem conter erros de extração e têm cobertura parcial (ver
  tabela de campos acima). Para uso que depende desses campos, validação adicional é recomendada.

---

## Estatísticas

| Métrica | Valor |
|---|---|
| Pares pergunta/resposta | 68.938 |
| Medicamentos únicos | 7.930 |
| Bulas com as 9 seções completas | ~88% |
| Classes terapêuticas distintas | 397 |
| Top classes (por volume) | Antidepressivos (332), Antineoplásicos (288), Antibióticos (279) |
| Tamanho da resposta — mediana | ~720 caracteres |
| Tamanho da resposta — máximo | ~96.000 caracteres |

---

## Licença e direitos

### Texto das bulas

As bulas do paciente são redigidas pelas **empresas farmacêuticas** titulares do registro e
publicadas pela ANVISA no Bulário Eletrônico como condição de registro (Lei 6.360/1976 e RDC
47/2009). Diferentemente de atos normativos ou decisões judiciais, bulas **não são excluídas**
da proteção autoral pelo art. 8º, IV da Lei 9.610/1998 — o direito autoral sobre o texto
pertence aos respectivos fabricantes.

O texto das bulas é reproduzido neste dataset para fins de **pesquisa em processamento de
linguagem natural**, a partir de fonte de acesso público disponibilizada pela ANVISA. O uso
aqui se enquadra na exceção de pesquisa científica (art. 46, II da Lei 9.610/1998).
Usos comerciais do texto das bulas podem requerer autorização dos titulares.

### Dataset compilado

A estrutura, segmentação, metadados extraídos e o trabalho de compilação deste dataset são
disponibilizados sob **[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)** — uso livre
com atribuição.

### Código

O código de coleta e processamento está disponível em
[github.com/walmeidadf/ChatBulario](https://github.com/walmeidadf/ChatBulario) sob licença **MIT**.

---

## Citação

```bibtex
@dataset{chatbulario-2026,
  title     = {ChatBulário: Dataset de perguntas e respostas a partir das bulas
               de medicamentos da ANVISA},
  author    = {Almeida, Wesley},
  year      = {2026},
  publisher = {Hugging Face},
  url       = {https://huggingface.co/datasets/walmeidadf/ChatBulario},
  note      = {Código: github.com/walmeidadf/ChatBulario.
               Dados originais: Bulário Eletrônico da ANVISA
               (consultas.anvisa.gov.br)}
}
```
