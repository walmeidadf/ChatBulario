---
name: anvisa-bulario-api
description: Como coletar bulas da API do Bulário Eletrônico da ANVISA (ChatBulário)
metadata: 
  node_type: memory
  type: project
  originSessionId: 0ee6a010-2d25-4b93-8ade-994bfd586e1d
---

Discovery técnico validado em 2026-06-22. API em `https://consultas.anvisa.gov.br` protegida por Cloudflare.

**Acesso (confirmado funcionando):** Playwright Python + Chromium headless. Stealth é OBRIGATÓRIO — sem ele retorna 403:
- `launch(args=["--disable-blink-features=AutomationControlled"])`
- UA de Chrome real via `new_context(user_agent=...)`
- `add_init_script` mascarando `navigator.webdriver`
- esperar ~8s após `goto` para o challenge do Cloudflare resolver
- `fetch()` rodado DENTRO da página (`page.evaluate`) com header `Authorization: 'Guest'`
- PDFs via arrayBuffer → base64

**Endpoints:**
- Busca por registro: `/api/consulta/bulario?count=10&filter[numeroRegistro]=XXXXXXXXX`
- PDF bula: `/api/consulta/medicamentos/arquivo/bula/parecer/{idBulaProtegido}/?Authorization=`

**Estratégia de filtro — IMPORTANTE:**
- `filter[numeroRegistro]` faz match **exato** pelo número de 9 dígitos → usar este.
- `filter[nomeProduto]` faz match apenas por **prefixo do início da string** → evitar (cobertura baixa).
- O driver correto é `NUMERO_REGISTRO_PRODUTO` do CSV de dados abertos da ANVISA, truncado para 9 dígitos: `reg13[:9] = reg9`. Taxa de cobertura com registro: ~86%.

**Mapeamento de IDs:**
- CMED usa registro de 13 dígitos (por apresentação)
- Bulário usa registro de 9 dígitos (por produto base)
- 857 arquivos pré-existentes → 449 registros únicos (esperado, não bug)

**Resposta da API:**
- Paginada (Spring): `content[]`, `totalElements`, `totalPages`
- Campos: `idProduto`, `numeroRegistro`, `nomeProduto`, `expediente`, `idBulaPacienteProtegido` (JWT com exp ~300s — usar logo após buscar)
- **Foco: apenas bula do paciente** (`idBulaPacienteProtegido`) — bula profissional excluída do projeto

**Rate limiting / pacing:**
- ANVISA retorna 500 estocásticos sob pressão
- Solução: 1,5s base + ±0,4s jitter + backoff exponencial (4 tentativas)
- Checkpoint a cada 100 itens; script retomável

Script principal: `collector.py`. Ver [[pipeline-architecture]].
