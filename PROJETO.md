# Guardrail AI Moderator

## Objetivo

API serverless na AWS que recebe um texto e censura partes dele (substituindo por `***`), de acordo com o tipo de censura solicitado: informações pessoais, conteúdo inapropriado, ou ambos.

## Arquitetura

- **AWS Lambda** — executa a lógica de moderação, cobrança apenas pelo tempo de execução real.
- **API Gateway** — expõe a Lambda como endpoint HTTP e cuida da autenticação via API Keys.
- **Amazon Comprehend** — detecção de informações pessoais (PII) no texto.
- **OpenAI Moderation API** — detecção de conteúdo inapropriado.
- **AWS Systems Manager Parameter Store** — guarda de forma segura (`SecureString`) a chave da OpenAI usada pela Lambda.

```
Cliente → API Gateway (valida API Key) → Lambda → Comprehend / OpenAI Moderation → Texto censurado
```

## Autenticação

Em vez de manter uma lista própria de chaves de acesso, usar **API Gateway API Keys + Usage Plans**:

- A AWS emite e revoga as chaves.
- Usage Plans permitem aplicar rate limit/throttling por cliente sem código extra.
- Reduz a superfície de lógica de autenticação dentro da Lambda.

## Contrato da API

### Request

```json
{
  "texto": "Meu nome é João e meu CPF é 123.456.789-00. Isso é uma droga.",
  "modo": 3
}
```

- `modo`:
  - `1` → censurar apenas informações pessoais
  - `2` → censurar apenas conteúdo inapropriado
  - `3` → censurar informações pessoais e conteúdo inapropriado

A autenticação não vai no corpo do JSON — é feita pelo header `x-api-key` do API Gateway.

### Response

```json
{
  "texto_censurado": "Meu nome é *** e meu CPF é ***. Isso é uma ***."
}
```

## Lógica de moderação

### Caso 1 — Informações pessoais

- Enviar o texto ao **Amazon Comprehend** (`DetectPiiEntities`).
- Para cada entidade PII retornada (nome, CPF, e-mail, telefone, endereço, etc.), substituir o trecho correspondente por `***`.

### Caso 2 — Conteúdo inapropriado

- Enviar o texto ao endpoint gratuito **OpenAI Moderation API**.
- A resposta indica categorias sinalizadas (ódio, violência, sexual, etc.) mas não a posição exata no texto.
- **Decisão:** se o texto for sinalizado, censurar o texto todo (`***`). Um segundo passo com prompt a um modelo da OpenAI para apontar os trechos exatos foi descartado por enquanto — dobraria o custo por requisição (2 chamadas à OpenAI), adiciona latência, e abre risco de prompt injection (o texto do usuário viraria input de um prompt que decide o que censurar). Fica como possível evolução futura se a precisão de censura parcial for realmente necessária.

### Caso 3 — Ambos

- **Ordem fixa: Comprehend (Caso 1) sempre primeiro, depois OpenAI Moderation (Caso 2).** Isso evita enviar PII (nome, CPF, etc.) para um serviço de terceiro (OpenAI, fora do Brasil) sem necessidade — relevante para LGPD, já que o texto recebido pode conter dados pessoais de terceiros, não só do próprio usuário da API.

### Tratamento de falhas

- Se Comprehend ou OpenAI Moderation falharem/timeout, a resposta deve ser um erro (5xx) — **nunca** devolver o texto sem a censura correspondente (fail closed). Um serviço de moderação que falha e libera texto sem censurar é pior do que um serviço que fica indisponível.

## Segurança

- Chave da OpenAI armazenada como `SecureString` no Parameter Store, lida pela Lambda via IAM role (não hardcoded). Parameter Store *standard tier* não tem custo por chamada, então buscar o parâmetro na invocação é aceitável — mas vale cachear em memória fora do handler (variável de módulo) para reduzir latência em invocações subsequentes do mesmo container.
- Autenticação de clientes via API Gateway API Keys + Usage Plan.
  - API Keys sozinhas são uma autenticação relativamente simples (sem expiração automática, sem escopo granular). Suficiente para MVP/portfólio; se o projeto evoluir para uso real com múltiplos clientes, considerar reforçar com um Lambda Authorizer (JWT/Cognito) — não necessário agora.
- **IAM least privilege:** a role de execução da Lambda deve ter permissões restritas ao mínimo necessário — `comprehend:DetectPiiEntities` e `ssm:GetParameter` limitado ao ARN exato do parâmetro da chave da OpenAI (nunca `ssm:GetParameter*` genérico ou `comprehend:*`).
- Validar `modo` (aceitar somente 1, 2 ou 3) e tamanho máximo do texto recebido antes de chamar serviços externos.
- **Logs:** nunca logar `texto` ou `texto_censurado` em texto puro no CloudWatch (podem conter PII). Logar apenas metadados: modo, tamanho do texto, latência, status da resposta.
- Manter Parameter Store (não migrar para Secrets Manager) — Secrets Manager cobra por secret armazenado (~$0,40/mês) e por chamada; não traz benefício necessário aqui (a chave da OpenAI não precisa de rotação automática por enquanto), então o Parameter Store `SecureString` é a opção correta tanto por segurança quanto por custo.

## Custos e proteção contra gastos inesperados

Como este é um projeto de portfólio (sem tráfego real esperado), o foco é evitar qualquer custo fixo desnecessário e conter a exposição a abuso/flood, já que cada requisição custa uma chamada à OpenAI e possivelmente ao Comprehend:

- **Reserved concurrency** na Lambda: limitar a concorrência máxima (ex.: 5–10 execuções simultâneas) para impor um teto duro de custo mesmo em caso de abuso ou bug em loop. Sem custo adicional — apenas reserva parte do limite de concorrência da conta.
- **AWS Budgets:** configurar um budget com alerta (ex.: em $5 ou $10) para ser avisado por e-mail antes de qualquer gasto relevante. Os dois primeiros budgets são gratuitos.
- **Usage Plan** com throttle (rate) e quota (requisições/dia ou /mês) definidos explicitamente — não deixar sem limite.
- **AWS WAF foi avaliado e descartado por enquanto:** tem custo fixo mensal (~$5-6 de Web ACL + $1/regra + $0,60 por milhão de requisições) mesmo sem tráfego, o que não se justifica neste estágio. O Usage Plan + Budget alerts cobrem a proteção contra abuso/custo excessivo sem custo fixo. Reavaliar se o projeto sair do portfólio e for para produção com tráfego real.

## Próximos passos

1. (Opcional, recomendado) Definir a infraestrutura como código (SAM, CDK ou Terraform) em vez de criar recursos manualmente pelo console — facilita reproduzir o ambiente e revisar mudanças de IAM/API Gateway.
2. Criar a Lambda (runtime, permissões IAM restritas para Comprehend e Parameter Store, reserved concurrency).
3. Configurar API Gateway com API Keys, Usage Plan (throttle/quota) e CORS (se aplicável).
4. Configurar AWS Budgets com alerta de custo.
5. Implementar detecção de PII (Comprehend).
6. Implementar detecção de conteúdo inapropriado (OpenAI Moderation).
7. Implementar a substituição por `***` e montar a resposta, com fail-closed em caso de erro.
8. Testar os três modos com casos reais.
9. Configurar logs (sem PII) e alarmes básicos de monitoramento (CloudWatch: erros, throttles, duração).
