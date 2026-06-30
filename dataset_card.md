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

Dataset de perguntas e respostas em português construído a partir das **bulas do paciente**
de medicamentos registrados no [Bulário Eletrônico da ANVISA](https://consultas.anvisa.gov.br).
Cada exemplo é um par pergunta/resposta correspondente a uma das nove seções padronizadas
pela RDC 47/2009, acompanhado dos metadados do medicamento.

## Resumo

A ANVISA padroniza a bula do paciente em torno de **nove perguntas fixas** (RDC 47/2009),
escritas em linguagem acessível — por exemplo, *"Para que este medicamento é indicado?"* ou
*"Quais os males que este medicamento pode me causar?"*. Este dataset extrai esses pares
pergunta/resposta de milhares de bulas e os disponibiliza num formato **flat**: cada linha é
uma resposta, carregando os metadados da bula (princípio ativo, classe terapêutica, fabricante
etc.) denormalizados para uso direto em instruction-tuning, RAG e busca semântica no domínio
farmacêutico.

## Tarefas suportadas

- **Question answering de domínio fechado** — responder as 9 perguntas da RDC 47/2009 sobre um medicamento
- **Instruction-tuning / geração de texto** — `pergunta` (+ metadados) → `resposta`
- **Recuperação / RAG** — base de conhecimento estruturada para chatbots de informação sobre medicamentos

## Idioma

Português brasileiro (`pt-BR`). Linguagem acessível ao paciente (bula do paciente, não a bula do profissional).

## Estrutura do dataset

### Configuração e splits

Config único `default`, formato flat (1 linha por par pergunta/resposta). Splits **80/10/10
agrupados por medicamento** — todas as perguntas de uma mesma bula caem no mesmo split, evitando
vazamento entre treino e teste. Estratificado por classe terapêutica; classes com menos de 10
medicamentos vão inteiras para o treino.

| Split | Linhas (Q/A) | Bulas |
|---|---|---|
| `train` | 57.460 | 6.614 |
| `validation` | 5.764 | 658 |
| `test` | 5.714 | 658 |
| **Total** | **68.938** | **7.930** |

### Campos

| Campo | Tipo | Origem | Descrição |
|---|---|---|---|
| `registro` | `str` | ANVISA | Número de registro do medicamento (9 dígitos) |
| `nome_produto` | `str` | ANVISA/LLM | Nome comercial do medicamento |
| `categoria_regulatoria` | `str\|null` | CSV ANVISA | Ex: `Referência`, `Genérico`, `Similar` |
| `principio_ativo_csv` | `str\|null` | CSV ANVISA | Princípio ativo (nomenclatura oficial do CSV) |
| `classe_terapeutica` | `str\|null` | CSV ANVISA | Ex: `INIBIDOR DA ECA` |
| `expediente` | `str\|null` | CSV ANVISA | Número do expediente regulatório |
| `nome_comercial` | `str\|null` | LLM | Nome comercial extraído da identificação |
| `fabricante` | `str\|null` | LLM | Empresa fabricante |
| `principio_ativo` | `str\|null` | LLM | Princípio ativo (texto livre da bula) |
| `forma_farmaceutica` | `str\|null` | LLM | Ex: `comprimidos`, `solução oral` |
| `via_administracao` | `str\|null` | LLM | Ex: `oral`, `tópica` |
| `apresentacao` | `str\|null` | LLM | Embalagens e dosagens |
| `composicao` | `str\|null` | LLM | Composição qualitativa/quantitativa |
| `uso` | `str\|null` | LLM | `adulto`, `pediátrico` ou `adulto e pediátrico` |
| `secao_id` | `int` | derivado | Número da seção/pergunta (1–9, RDC 47/2009) |
| `pergunta` | `str` | bula | Texto da pergunta padronizada |
| `resposta` | `str` | bula | Texto da resposta extraído da bula |
| `fuzzy_score` | `float` | derivado | Confiança (0–100) do match da seção via fuzzy matching |

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
  "uso": "adulto",
  "secao_id": 1,
  "pergunta": "Para que este medicamento é indicado?",
  "resposta": "Seu médico prescreveu RENITEC® para controlar a pressão alta...",
  "fuzzy_score": 100.0
}
```

## Estatísticas

| Métrica | Valor |
|---|---|
| Pares pergunta/resposta | 68.938 |
| Medicamentos (bulas) | 7.930 |
| Bulas com as 9 seções completas | ~88% |
| Classes terapêuticas distintas | 397 |
| Tamanho da resposta — mediana | ~720 caracteres |

### Cobertura dos metadados LLM

Os metadados estruturados são extraídos por LLM (`gpt-4o-mini`, via OpenAI Batch API) da seção
de identificação de cada bula. A cobertura varia por campo:

| Campo | Preenchimento |
|---|---|
| `forma_farmaceutica`, `apresentacao` | ~78% |
| `principio_ativo`, `composicao`, `uso` | ~77% |
| `nome_comercial` | ~72% |
| `fabricante` | ~27% |

O campo `fabricante` é esparso porque, na maioria das bulas, o fabricante aparece nos *dizeres
legais* (final do documento), não na seção de identificação processada pelo LLM. Cerca de 20%
das bulas têm todos os metadados LLM nulos (identificação muito curta ou atípica). **Os pares
pergunta/resposta, núcleo do dataset, têm cobertura completa** — os metadados são enriquecimento
opcional.

## Dados originais

As bulas são disponibilizadas publicamente pela ANVISA no
[Bulário Eletrônico](https://consultas.anvisa.gov.br) e o catálogo de medicamentos no
[Dados Abertos da ANVISA](https://dados.anvisa.gov.br/dados/). São documentos públicos.

O código de coleta e processamento está disponível em
[github.com/walmeidadf/ChatBulario](https://github.com/walmeidadf/ChatBulario).

## Como usar

```python
from datasets import load_dataset

ds = load_dataset("walmeidadf/ChatBulario")

# Só as respostas para "Para que este medicamento é indicado?" (seção 1)
indicacoes = ds["train"].filter(lambda x: x["secao_id"] == 1)

# Todas as perguntas de um medicamento específico
renitec = ds["train"].filter(lambda x: x["registro"] == "100290031")
```

## Considerações de uso

- **Não é aconselhamento médico.** As respostas reproduzem o texto das bulas para fins de
  pesquisa em PLN. Não devem ser usadas como fonte clínica sem validação.
- A extração das seções usa fuzzy matching; consulte `fuzzy_score` para filtrar respostas de
  baixa confiança. ~12% das bulas têm menos de 9 seções detectadas (layouts atípicos).
- Bulas escaneadas (sem texto extraível) foram descartadas — não há OCR nesta versão.
- Os metadados LLM podem conter erros de extração e têm cobertura parcial (ver acima).

## Licença

- **Texto das bulas**: redigido pelas empresas fabricantes e publicado pela ANVISA no
  Bulário Eletrônico. As bulas não são atos oficiais (art. 8º da Lei 9.610/98) — o direito
  autoral sobre o texto permanece com os respectivos fabricantes. O texto é reproduzido aqui
  para fins de pesquisa em PLN, a partir de fonte de acesso público.
- **Dataset compilado** (segmentação, estrutura, metadados): [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- **Código**: MIT

## Citação

```bibtex
@dataset{chatbulario-2026,
  title     = {ChatBulário: Dataset de perguntas e respostas a partir das bulas
               de medicamentos da ANVISA},
  author    = {Almeida, Wesley},
  year      = {2026},
  publisher = {Hugging Face},
  url       = {https://huggingface.co/datasets/walmeidadf/ChatBulario},
  note      = {Dados originais: Bulário Eletrônico da ANVISA
               (consultas.anvisa.gov.br)}
}
```
