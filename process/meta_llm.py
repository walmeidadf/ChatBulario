"""
Extração de metadados estruturados da seção de identificação da bula.

Usa o SDK OpenAI (compatível com OpenAI, Groq e Cerebras — mesma interface).
O provider é controlado por variáveis de ambiente:

  LLM_PROVIDER=openai    → OpenAI (padrão)  — gpt-4o-mini
  LLM_PROVIDER=groq      → Groq             — llama-3.1-8b-instant
  LLM_PROVIDER=cerebras  → Cerebras         — llama3.1-8b

Variáveis necessárias por provider:
  openai:    OPENAI_API_KEY
  groq:      GROQ_API_KEY
  cerebras:  CEREBRAS_API_KEY

Uso síncrono:
    from meta_llm import extract_meta_llm
    meta = extract_meta_llm(texto_identificacao)

Uso async batch (recomendado para process_all.py):
    from meta_llm import extract_meta_llm_batch
    resultados = asyncio.run(extract_meta_llm_batch(lista_de_textos))
"""

import asyncio
import json
import os
import re
import random
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI, RateLimitError

# carrega .env da raiz do projeto (dois níveis acima de process/)
load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Configuração de provider
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "openai": {
        "base_url": None,  # usa o padrão do SDK
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o-mini",
        "max_tokens": 512,
        # preço $/1M tokens (input / output)
        "price_in": 0.15,
        "price_out": 0.60,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "model": "llama-3.1-8b-instant",
        "max_tokens": 512,
        # free tier: 30 RPM / 6K TPM / 14.4K RPD / 500K TPD
        # binding constraint: TPM → ~7 req/min efetivo (~12h para 5k bulas)
        "price_in": 0.0,
        "price_out": 0.0,
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "api_key_env": "CEREBRAS_API_KEY",
        # gpt-oss-120b é modelo de raciocínio: gasta ~600 tokens internos antes
        # de gerar a resposta visível — max_tokens precisa cobrir ambos.
        "model": "gpt-oss-120b",
        "max_tokens": 3000,
        # free tier: 5 RPM / 30K TPM / 1M TPD
        # binding constraint: TPD (1M) → ~728 req/dia → 7+ dias para 5k bulas
        "price_in": 0.0,
        "price_out": 0.0,
    },
}


def _cfg() -> dict:
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    if provider not in _PROVIDERS:
        raise ValueError(f"LLM_PROVIDER inválido: {provider!r}. Use: {list(_PROVIDERS)}")
    cfg = _PROVIDERS[provider]
    api_key = os.getenv(cfg["api_key_env"])
    if not api_key:
        raise EnvironmentError(
            f"Variável {cfg['api_key_env']} não definida para provider={provider!r}"
        )
    return {**cfg, "api_key": api_key, "provider": provider}


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM = (
    "Você é um extrator de metadados de bulas de medicamentos brasileiros. "
    "Extraia as informações pedidas exatamente como aparecem no texto; "
    "se um campo não estiver presente, use null. "
    "Responda APENAS com JSON válido, sem texto adicional."
)

_PROMPT_TEMPLATE = """\
Extraia as seguintes informações da seção de identificação desta bula:

- nome_comercial: nome do produto/marca
- fabricante: nome do laboratório/fabricante
- principio_ativo: substância(s) ativa(s)
- forma_farmaceutica: forma (comprimido, cápsula, xarope, etc.)
- via_administracao: via (oral, intravenosa, tópica, etc.)
- apresentacao: apresentação comercial (ex: "caixa com 30 comprimidos")
- composicao: composição completa (excipientes incluídos se presentes)
- uso: público-alvo — um dos valores: "adulto", "pediátrico", "adulto e pediátrico"

Texto:
{texto}

Responda com JSON seguindo exatamente este schema:
{{
  "nome_comercial": string | null,
  "fabricante": string | null,
  "principio_ativo": string | null,
  "forma_farmaceutica": string | null,
  "via_administracao": string | null,
  "apresentacao": string | null,
  "composicao": string | null,
  "uso": "adulto" | "pediátrico" | "adulto e pediátrico" | null
}}"""

_CAMPOS = [
    "nome_comercial", "fabricante", "principio_ativo",
    "forma_farmaceutica", "via_administracao", "apresentacao",
    "composicao", "uso",
]

_RE_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


def _parse_resposta(texto: str) -> dict:
    m = _RE_JSON_BLOCK.search(texto)
    raw = m.group(1) if m else texto.strip()
    try:
        dados = json.loads(raw)
    except json.JSONDecodeError:
        return {c: None for c in _CAMPOS}
    return {c: dados.get(c) for c in _CAMPOS}


def _build_messages(identificacao: str) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _PROMPT_TEMPLATE.format(texto=identificacao[:2000])},
    ]


# ---------------------------------------------------------------------------
# Interface síncrona
# ---------------------------------------------------------------------------

def extract_meta_llm(identificacao: str) -> dict:
    """Extrai metadados de uma seção de identificação (síncrono)."""
    if not identificacao or not identificacao.strip():
        return {c: None for c in _CAMPOS}

    cfg = _cfg()
    kwargs = {"api_key": cfg["api_key"]}
    if cfg["base_url"]:
        kwargs["base_url"] = cfg["base_url"]

    client = OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=cfg["model"],
        messages=_build_messages(identificacao),
        max_tokens=cfg["max_tokens"],
        temperature=0,
    )
    return _parse_resposta(resp.choices[0].message.content or "")


# ---------------------------------------------------------------------------
# Interface assíncrona batch
# ---------------------------------------------------------------------------

async def _extract_one(
    client: AsyncOpenAI,
    model: str,
    max_tokens: int,
    identificacao: str,
    sem: asyncio.Semaphore,
) -> dict:
    if not identificacao or not identificacao.strip():
        return {c: None for c in _CAMPOS}
    for attempt in range(6):
        try:
            async with sem:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=_build_messages(identificacao),
                    max_tokens=max_tokens,
                    temperature=0,
                )
            return _parse_resposta(resp.choices[0].message.content or "")
        except RateLimitError as exc:
            msg = str(exc).lower()
            if "per day" in msg or "rpd" in msg:
                raise  # limite diário — retry não resolve
            if attempt == 5:
                raise
            wait = (2 ** attempt) + random.random()
            await asyncio.sleep(wait)


async def extract_meta_llm_batch(
    textos: list[str],
    concorrencia: int = 20,
) -> list[dict]:
    """Processa lista de identificações em paralelo. Retorna na mesma ordem."""
    cfg = _cfg()
    kwargs = {"api_key": cfg["api_key"]}
    if cfg["base_url"]:
        kwargs["base_url"] = cfg["base_url"]

    client = AsyncOpenAI(**kwargs)
    sem = asyncio.Semaphore(concorrencia)
    tasks = [_extract_one(client, cfg["model"], cfg["max_tokens"], t, sem) for t in textos]
    return await asyncio.gather(*tasks)


async def extract_meta_llm_stream(
    textos: list[str],
    concorrencia: int = 20,
):
    """Igual a batch, mas é um async generator que produz (indice, meta)
    conforme cada extração termina (ordem de conclusão, não de entrada).

    Permite ao chamador gravar resultados em streaming. Falha persistente de
    uma única chamada (após esgotar os retries) é capturada e devolvida como
    metadados vazios — nunca derruba o lote inteiro.
    """
    cfg = _cfg()
    kwargs = {"api_key": cfg["api_key"]}
    if cfg["base_url"]:
        kwargs["base_url"] = cfg["base_url"]

    client = AsyncOpenAI(**kwargs)
    sem = asyncio.Semaphore(concorrencia)

    async def _one(i: int, t: str) -> tuple[int, dict]:
        try:
            return i, await _extract_one(client, cfg["model"], cfg["max_tokens"], t, sem)
        except Exception as exc:  # noqa: BLE001 — resiliência: 1 falha não mata o lote
            print(f"  ! LLM falhou (idx={i}): {exc}", flush=True)
            return i, {c: None for c in _CAMPOS}

    tasks = [asyncio.create_task(_one(i, t)) for i, t in enumerate(textos)]
    for fut in asyncio.as_completed(tasks):
        yield await fut


# ---------------------------------------------------------------------------
# CLI rápido para teste
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    texto = sys.stdin.read() if not sys.stdin.isatty() else (
        sys.argv[1] if len(sys.argv) > 1 else ""
    )
    if not texto:
        print("Uso: echo '<identificacao>' | uv run python process/meta_llm.py")
        print(f"Provider ativo: {os.getenv('LLM_PROVIDER', 'openai')}")
        sys.exit(1)
    resultado = extract_meta_llm(texto)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
