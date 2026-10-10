# Guardrail AI Moderator

## Objetivo

API serverless na AWS que recebe um texto **em inglês** e censura partes dele (substituindo por `***`), de acordo com o tipo de censura solicitado: informações pessoais, conteúdo inapropriado, ou ambos.

**Por que só inglês:** o `DetectPiiEntities` do Amazon Comprehend só suporta detecção de PII em inglês e espanhol — português não é suportado. Como o objetivo é usar o Comprehend pronto (sem treinar/manter um modelo próprio), o escopo do projeto foi restrito a texto em inglês. Isso também elimina a necessidade de detectar documentos específicos de um país (como CPF), já que os tipos de entidade nativos do Comprehend (nome, e-mail, telefone, endereço, SSN, cartão de crédito, etc.) cobrem bem o caso de uso em inglês.

### Princípios

Em caso de conflito, estes princípios têm prioridade sobre qualquer outra decisão deste documento:

1. **Simples e funcional:** um guardrail que censura PII e conteúdo impróprio. Nada além do necessário para isso.
2. **No ar só quando for usado:** `apply` para mostrar a alguém, `destroy` para remover depois. A stack não fica permanentemente no ar.
3. **Derrubada automática em caso de abuso:** mais de 10 invocações da Lambda principal no mesmo minuto de relógio zeram a concorrência dela, e a API para de processar texto. Requisições barradas antes da Lambda (400, 403, 429 do API Gateway) não contam, porque não geram custo de Comprehend.
4. **Religar só manualmente:** nada religa a stack sozinho. O único caminho de volta é um `apply` disparado manualmente.

## Arquitetura

- **AWS Lambda** — executa a lógica de moderação, cobrança apenas pelo tempo de execução real.
- **API Gateway** — expõe a Lambda como endpoint HTTP, valida o body e aplica throttle/quota por API Key.
- **Amazon Comprehend** — detecção de informações pessoais (PII) no texto.
- **OpenAI Moderation API** — detecção de conteúdo inapropriado.
- **AWS Systems Manager Parameter Store** — guarda de forma segura (`SecureString`) a chave da OpenAI usada pela Lambda.
- **CloudWatch Alarm + SNS + Lambda "kill switch"** — contenção automática de custo: se a Lambda principal passar de **10 invocações em 1 minuto**, a concorrência dela é zerada e a API para de processar texto (ver "Custos").

```
Cliente (curl/Postman) → API Gateway (API Key, schema, throttle/quota) → Lambda → Comprehend / OpenAI Moderation → Texto censurado

CloudWatch Alarm (Invocations > 10/min) → SNS → Lambda kill switch → concorrência da Lambda principal = 0
                                            └→ e-mail de aviso
```

Não há frontend: a demonstração é feita via `curl` ou Postman (ver "Acesso de demonstração").

### Região da AWS

**Decisão: `us-east-1`.** O **Amazon Comprehend não está disponível na região São Paulo (`sa-east-1`)**, então a stack precisa rodar em outra região. Lambda, API Gateway e Parameter Store ficam na mesma região do Comprehend para evitar latência e custo de tráfego entre regiões.

Consequência para a narrativa de LGPD: o texto recebido **sai do Brasil também na etapa de detecção de PII**, não só na de moderação de conteúdo. A ordem fixa Comprehend → OpenAI (ver "Caso 3 — Ambos") não serve para manter o processamento no Brasil — serve para, no **modo 3**, não enviar PII em texto aberto para um **terceiro fora da relação AWS** (OpenAI). No **modo 2** essa proteção não existe: o texto original vai direto para a OpenAI (ver "Caso 2"). Isso deve ficar documentado explicitamente no README, como reconhecimento consciente da limitação em vez de omissão.

**Retenção dos dados pelos provedores:**

- **Comprehend:** ele está na lista de AI services da AWS que podem armazenar e usar o conteúdo processado para melhorar o serviço. Para impedir isso, aplicar uma **AI services opt-out policy** no AWS Organizations (feito no bootstrap, ver "Próximos passos"). Uma conta avulsa pode criar uma organização só com ela mesma, sem custo.
- **OpenAI:** por padrão, a API da OpenAI retém os dados enviados por até 30 dias para monitoramento de abuso, sem usá-los para treino. Isso pesa principalmente no modo 2, em que o texto original com PII vai para a OpenAI. O README deve citar essa retenção.

### Rede — Lambda sem VPC

A Lambda **não é associada a nenhuma VPC**. Nenhum recurso usado (Comprehend, Parameter Store, chamada HTTPS à OpenAI) exige rede privada — Comprehend e SSM são acessados via endpoint público da AWS (SDK padrão, com TLS), e a OpenAI é um endpoint público na internet.

- **Motivo explícito:** colocar uma Lambda dentro de uma VPC só para "seguir o padrão" é o erro de custo mais comum em projetos iniciantes na AWS — para a função ter saída à internet (necessária para chamar a OpenAI) a partir de dentro da VPC, seria preciso um **NAT Gateway**, que custa **~US$ 32/mês fixos só por existir**, além de custo por GB processado — independente de tráfego. Isso destruiria o objetivo de custo zero fora dos períodos de demonstração.
- Sem VPC, a Lambda usa a rede gerenciada da própria AWS para tudo, sem custo de rede adicional.

## Autenticação

Em vez de manter uma lista própria de chaves de acesso, usar **API Gateway API Keys + Usage Plans**:

- A AWS emite e revoga as chaves. Usar uma única chave **gerada pelo API Gateway** (não escolher o valor manualmente) e nunca embutir informação sensível no valor da chave.
- O Usage Plan aplica throttle e quota à chave sem código extra, e reduz a lógica de autenticação dentro da Lambda.
- **API Key não é autenticação de verdade.** A própria documentação da AWS recomenda *não* usar API Keys para autenticar/autorizar acesso (sem expiração automática, sem escopo granular, trafegam em header que pode ser logado). Aqui ela é tratada apenas como **identificador de cliente para throttling/quota** — aceitável para MVP/portfólio, com o risco de custo contido pelo kill switch (ver "Custos"), e não pelo Usage Plan. Se o projeto evoluir para uso real com múltiplos clientes, trocar por Lambda Authorizer (JWT/Cognito) ou IAM.

## Contrato da API

### Request

```json
{
  "texto": "My name is John Smith and my email is john.smith@example.com.",
  "modo": 1
}
```

- `texto`: obrigatório, string de **1 a 1.000 caracteres**. Texto vazio é rejeitado com 400, porque o `DetectPiiEntities` não aceita texto vazio e, pela regra de fail closed, o erro dele viraria 5xx. Texto só com espaços é aceito e devolvido como está, já que não há o que censurar.
- `modo`: obrigatório, **inteiro** (`"1"` ou `1.0` são rejeitados):
  - `1` → censurar apenas informações pessoais
  - `2` → censurar apenas conteúdo inapropriado
  - `3` → censurar informações pessoais e conteúdo inapropriado

A autenticação não vai no corpo do JSON — é feita pelo header `x-api-key` do API Gateway. O header `Content-Type` deve ser `application/json`.

**Só texto em inglês é suportado.** Texto em outro idioma não gera erro: a API responde normalmente, mas a PII não é detectada e passa sem censura.

### Response

Exemplo com `modo: 1` (apenas PII) — a substituição é feita entidade por entidade, preservando o resto do texto:

```json
{
  "texto_censurado": "My name is *** and my email is ***."
}
```

Exemplo com `modo: 3` e conteúdo sinalizado como impróprio pela OpenAI Moderation — pela regra do "Caso 2" (ver "Lógica de moderação"), o texto inteiro é substituído, independente de quantas entidades de PII haviam sido identificadas antes:

```json
{
  "texto": "My name is John Smith. I want to kill you.",
  "modo": 3
}
```

```json
{
  "texto_censurado": "***"
}
```

### Respostas de erro

| Status | Causa |
|---|---|
| `400` | Body inválido: JSON malformado, `Content-Type` diferente de `application/json`, `texto` ausente, vazio ou acima de 1.000 caracteres, `modo` ausente, não inteiro ou diferente de 1, 2 ou 3 |
| `403` | Header `x-api-key` ausente ou com key inválida |
| `429` | Throttle ou quota do Usage Plan excedidos |
| `502` | Falha ou timeout de um serviço de moderação, devolvido pela própria Lambda (fail closed, ver "Tratamento de falhas") |
| outros `5xx` | Gerados pelo API Gateway: timeout da Lambda, erro não tratado ou kill switch ativo (Lambda throttled) |

Corpo de erro, sempre no mesmo formato:

```json
{
  "erro": "mensagem genérica"
}
```

- A Lambda devolve esse formato nos 400 e 502 dela. A mensagem é genérica (por exemplo, `"requisição inválida"`, `"serviço de moderação indisponível"`) e nunca inclui detalhe interno, stack trace ou trecho do texto.
- Os erros gerados pelo API Gateway (400 do validator, 403, 429 e 5xx) usam **Gateway Responses** customizadas, sem custo, para seguir o mesmo formato.

Em nenhum caso de erro o texto é devolvido.

## Lógica de moderação

### Caso 1 — Informações pessoais

- Enviar o texto ao **Amazon Comprehend** (`DetectPiiEntities`).
- Censurar apenas as entidades desta **lista fechada**, e só com `Score` **≥ 0,5**:
  - `NAME`, `EMAIL`, `PHONE`, `ADDRESS`, `SSN`, `CREDIT_DEBIT_NUMBER`, `CREDIT_DEBIT_CVV`, `CREDIT_DEBIT_EXPIRY`, `PIN`, `BANK_ACCOUNT_NUMBER`, `BANK_ROUTING`, `PASSPORT_NUMBER`, `DRIVER_ID`, `IP_ADDRESS`, `PASSWORD`.
  - CVV, validade, PIN, routing e senha entram junto com o número de cartão e de conta, já que deixá-los passar anula a censura do número.
  - `DATE_TIME`, `AGE`, `URL` e as demais entidades são ignoradas.
  - Score mínimo baixo de propósito: num guardrail, censurar demais é melhor do que deixar PII passar.
- **Juntar os trechos sobrepostos ou adjacentes** antes de substituir: ordenar por `BeginOffset` e fundir intervalos que se tocam ou se sobrepõem num só. Sem isso, entidades sobrepostas corrompem o texto na substituição, e entidades coladas geram `******`.
- Substituir cada intervalo resultante por `***`, aplicando as substituições **do fim para o início do texto** (ordenando por `BeginOffset` decrescente), senão os offsets das entidades seguintes se deslocam.
- **Conferir os offsets com caracteres não ASCII:** testar com acento e emoji antes da PII (por exemplo, `"Café ☕ — my name is John Smith"`) para confirmar que os offsets do Comprehend batem com os índices de `str` do Python. Se não baterem, converter os offsets antes de substituir.

### Caso 2 — Conteúdo inapropriado

- Enviar o texto ao endpoint gratuito **OpenAI Moderation API** (modelo `omni-moderation-latest`).
- A resposta indica categorias sinalizadas (ódio, violência, sexual, etc.) mas não a posição exata no texto.
- **Decisão:** se o texto for sinalizado, censurar o texto todo (`***`). Um segundo passo com prompt a um modelo da OpenAI para apontar os trechos exatos foi descartado por enquanto — dobraria o custo por requisição (2 chamadas à OpenAI), adiciona latência, e abre risco de prompt injection (o texto do usuário viraria input de um prompt que decide o que censurar). Fica como possível evolução futura se a precisão de censura parcial for realmente necessária.
- **PII no modo 2:** como não há passo de Comprehend, o **texto original, com PII, vai para a OpenAI**. Quem quiser proteger PII deve usar o modo 3.

### Caso 3 — Ambos

- **Ordem fixa: Comprehend (Caso 1) sempre primeiro, depois OpenAI Moderation (Caso 2).**
- A OpenAI Moderation recebe o texto **já processado pelo Comprehend** (PII substituída por `***`), nunca o original — é esse encadeamento, e não só a ordem das chamadas, que evita enviar PII de terceiros à OpenAI (ver "Região da AWS" para o alcance dessa proteção em relação à LGPD). A proteção vale para o que o Caso 1 censura: entidades fora da lista, abaixo do score mínimo ou não detectadas (por exemplo, em texto que não está em inglês) seguem para a OpenAI.
- **Combinação final:** se a OpenAI sinalizar o texto (já com PII redigida) como impróprio, vale a regra do Caso 2 — a resposta final é `"***"` inteiro, substituindo inclusive as marcações de PII que já haviam sido feitas (ver exemplo em "Contrato da API"). Se a OpenAI não sinalizar nada, a resposta final é o texto com PII redigida pelo Comprehend, sem alteração adicional.

### Tratamento de falhas

- Se Comprehend ou OpenAI Moderation falharem/timeout, a resposta deve ser um erro (`502`, ver "Respostas de erro") — **nunca** devolver o texto sem a censura correspondente (fail closed). Um serviço de moderação que falha e libera texto sem censurar é pior do que um serviço que fica indisponível.
- **Timeouts:**
  - Comprehend e SSM (boto3): `connect_timeout=1`, `read_timeout=3`, `retries={"max_attempts": 2, "mode": "standard"}`.
  - OpenAI (`urllib`, sem dependências externas): `timeout=3`, no máximo 1 retry.
  - O parâmetro do SSM é lido uma vez por container e cacheado em variável de módulo, fora do handler (ver "Segurança"). Assim, o SSM só entra no tempo da primeira invocação de cada container.
  - Lambda: timeout de **15 s**. Se ela estourar, o API Gateway responde 5xx sem devolver texto, o que continua sendo fail closed — por isso não é preciso checar o tempo restante no código. A soma dos piores casos com retries (cerca de 22 s com SSM, ou 14 s sem) pode passar de 15 s; nesse caso a requisição vira 5xx, o que é aceitável.

## Segurança

- Chave da OpenAI armazenada como `SecureString` no Parameter Store, lida pela Lambda via IAM role (não hardcoded). Parameter Store *standard tier* não tem custo por chamada; a leitura é feita uma vez por container e cacheada em variável de módulo (fora do handler), para não pagar a latência do SSM em toda invocação.
- **Chave da OpenAI fora do state:** o `aws_ssm_parameter` grava o valor com `value_wo` + `value_wo_version` (argumento *write-only*, Terraform ≥ 1.11 e um provider AWS que já suporte o atributo). A chave nunca vai para o `.tfstate`. Para rotacionar, basta trocar o GitHub Secret e incrementar `value_wo_version`.
- **Key da OpenAI com dano limitado:** usar um **projeto dedicado** na OpenAI, com:
  - key restrita com a menor permissão disponível;
  - allowlist de modelos liberando só `omni-moderation-latest`, se o painel oferecer;
  - limite de gasto mensal baixo.

  Testar a Moderation com o limite ativo antes do primeiro uso, porque o limite pode bloquear até a Moderation gratuita.
- **IAM least privilege:**
  - Role da Lambda principal: `comprehend:DetectPiiEntities` e `ssm:GetParameter` limitado ao ARN exato do parâmetro da chave da OpenAI (nunca `ssm:GetParameter*` genérico ou `comprehend:*`).
  - Role da Lambda kill switch: `lambda:PutFunctionConcurrency` no ARN da função principal.
  - As duas roles também têm `logs:CreateLogStream` e `logs:PutLogEvents`, restritos ao ARN do log group da própria função. Sem isso, nenhuma Lambda grava log. `logs:CreateLogGroup` não entra, porque o log group é criado pelo Terraform.
  - Não é preciso `kms:Decrypt`: a chave gerenciada `alias/aws/ssm` já permite a descriptografia via SSM para quem tem `ssm:GetParameter`.
- Validar `texto` e `modo` antes de chamar serviços externos.
  - Fazer essa validação também via **Request Validator do API Gateway** (schema JSON), sem custo adicional — rejeita requisição malformada antes de invocar a Lambda, reduzindo custo e superfície de ataque. A Lambda repete a validação como defesa em profundidade.
  - Schema: `texto` com `"type": "string"`, `minLength: 1` e `maxLength: 1000`; `modo` com `"type": "integer"` e `"enum": [1, 2, 3]`; os dois em `required`.
  - Na Lambda, conferir o tipo com `type(modo) is int`, e não só `modo in (1, 2, 3)`: em Python, `True in (1, 2, 3)` é verdadeiro.
  - **`Content-Type`:** o Request Validator escolhe o modelo pelo `Content-Type` da requisição. Confirmar na implementação se um `Content-Type` diferente de `application/json` passa pelo validator sem validar o body. Se passar, a Lambda rejeita com 400 (ela confere o header), mas essa requisição já foi uma invocação e conta para o kill switch. Esse desvio é aceito, desde que fique documentado.
  - **Limite de tamanho: `maxLength` de 1.000 caracteres**, no Request Validator e na Lambda. O `DetectPiiEntities` aceita até 100 KB de UTF-8 por chamada síncrona e a OpenAI Moderation tem limite próprio — ambos muito acima disso, então a Lambda nunca paga uma chamada externa só para receber erro de tamanho. O valor é definido pelo custo: 1.000 caracteres = no máximo 10 unidades do Comprehend por chamada (ver "Custos").
- O Comprehend pode errar (falso negativo/positivo); o README não deve apresentar o serviço como garantia de anonimização.
- **Logs:** nunca logar `texto` ou `texto_censurado` em texto puro no CloudWatch (podem conter PII). Logar apenas metadados: modo, tamanho do texto, latência, status da resposta.
  - Definir **retenção explícita** (ex.: 14 dias) via Terraform nos log groups das duas Lambdas (principal e kill switch). Por padrão o CloudWatch Logs mantém os logs indefinidamente ("never expire"). Não há access log do API Gateway.
  - O log group é criado pelo Terraform **antes** da função (`logging_config` apontando para ele, ou `depends_on`). Se a Lambda rodar antes, ela cria o log group sozinha, sem retenção, e o `apply` seguinte falha com `ResourceAlreadyExistsException`. O mesmo acontece se sobrar um log group de um `destroy` anterior: apagá-lo pelo Console antes do `apply`.
- Manter Parameter Store (não migrar para Secrets Manager) — Secrets Manager cobra por secret armazenado (~$0,40/mês) e por chamada; não traz benefício necessário aqui (a rotação manual via `value_wo_version` basta), então o Parameter Store `SecureString` é a opção correta tanto por segurança quanto por custo.
  - Usar a **chave gerenciada pela AWS (`alias/aws/ssm`)** para a criptografia do `SecureString`, não uma KMS key própria (customer-managed key) — uma CMK custa ~US$ 1/mês só por existir, e não há necessidade de controle de rotação/política própria sobre a chave neste projeto.

## Custos e proteção contra gastos inesperados

Como este é um projeto de portfólio (sem tráfego real esperado), o foco é evitar qualquer custo fixo desnecessário e conter a exposição a abuso/flood, já que cada requisição nos modos 1 e 3 custa uma chamada ao Comprehend:

- **Sem NAT Gateway** (ver "Rede — Lambda sem VPC"): todo o resto da stack é pay-per-use ou gratuito em repouso.
- **Comprehend cobrado desde a primeira chamada:** a conta tem mais de 12 meses, então o free tier do Comprehend não se aplica (ver premissas em "Estimativa de custo"). Tratar como custo real ao dimensionar a quota do Usage Plan e o alerta do Budget.
- **Reserved concurrency de 3** na Lambda principal, **se a quota da conta permitir**: impõe um teto de **paralelismo** mesmo em caso de abuso ou bug em loop, sem custo adicional. Ela limita paralelismo, *não* o volume total de requisições — sozinha não é um teto de custo. A AWS só permite reservar até o valor de *Unreserved account concurrency* menos 100; se o limite de concorrência da conta for baixo (contas novas podem ter 10), o `apply` falha. **Conferir em Service Quotas → Lambda → "Concurrent executions" antes do primeiro deploy.** Se a quota não permitir, omitir a reserved concurrency: o throttle e o kill switch já limitam o custo. Nos dois casos, o teste do kill switch confirma que o `apply` restaura a função.
- **AWS Budgets:** configurar um budget com alerta (ex.: em $5 ou $10) para ser avisado por e-mail antes de qualquer gasto relevante. Os dois primeiros budgets são gratuitos. **Budgets só avisa, não interrompe nada** e é avaliado poucas vezes por dia — por isso não substitui o kill switch abaixo.
- **AWS Cost Anomaly Detection** (gratuito): complementa o Budgets, reagindo mais rápido a padrões fora do normal. Ativar junto com o Budget, no bootstrap via Console, antes do primeiro deploy.
- **Usage Plan:** um único plano e uma única key, com:
  - throttle **rate 5 req/s, burst 10** — propositalmente **acima** do limite do kill switch. Se o throttle ficasse abaixo de 10 req/min, as requisições seriam barradas antes de chegar à Lambda e o kill switch nunca dispararia;
  - **quota de 1.000 requisições/dia**, para cobrir abuso lento abaixo de 10/min.

  **A documentação da AWS é explícita: throttle e quota do Usage Plan são *best-effort*, não limites rígidos, e não devem ser usados como controle de custo.** Tratar como camada de redução de abuso, não como garantia.
- **Kill switch automático (obrigatório), calibrado para mais de 10 invocações/min:**
  - Alarme do CloudWatch em `Invocations` da Lambda principal: `Sum > 10` (`GreaterThanThreshold`, threshold 10), período de 60 s, 1 de 1 datapoint.
  - **O período segue o minuto do relógio, não uma janela móvel.** "Mais de 10 em 1 minuto" significa mais de 10 entre `hh:mm:00` e `hh:mm:59`. Um uso que se divide entre dois minutos (por exemplo, 10 + 10 em 40 s) não dispara. No pior caso, cerca de 20 invocações em 60 s passam sem disparar o alarme. É aceito: o custo disso é desprezível, e a quota diária cobre o abuso que fica abaixo do gatilho.
  - `treat_missing_data = notBreaching`. Depois do kill, as invocações caem a zero e o alarme volta sozinho para `OK`. Assim, se a stack for religada e o abuso continuar, ele dispara de novo.
  - A reação leva cerca de 1–3 minutos.
  - Ação: SNS → Lambda kill switch → `PutFunctionConcurrency` com `0` (throttle total da função). A permissão é só `lambda:PutFunctionConcurrency` no ARN da função principal. O mesmo tópico SNS tem uma assinatura de e-mail. Como o tópico é recriado a cada `apply` depois de um `destroy`, a assinatura nasce pendente: **confirmar o e-mail da AWS depois de cada `apply` que recria a stack**, senão o aviso não chega. A Lambda do kill switch funciona mesmo sem a confirmação.
  - O endereço de e-mail não fica hardcoded no `.tf`, porque o repositório é público. Ele entra por variável, vinda de uma GitHub Actions Secret (`ALERT_EMAIL`).
  - **Alarme de falha do próprio kill switch:** um segundo alarme, em `Errors` da Lambda kill switch (`Sum > 0`, período de 60 s), publica no mesmo tópico SNS. Assim, se a Lambda kill switch falhar e a concorrência não for zerada, chega um e-mail. A Lambda kill switch só escreve no log e chama `PutFunctionConcurrency`, então ela não reage a esse alarme. Os dois alarmes cabem nos 10 gratuitos do CloudWatch.
  - **Religar = rodar o workflow `apply` manualmente.** O Terraform detecta a concorrência em 0 e volta ao que o `.tf` define: 3, ou sem reserva se a reserved concurrency tiver sido omitida. Não existe outro caminho de religar.
  - O `apply` sozinho mantém a mesma API key. Se o kill veio de uso da key por terceiros, religar com `destroy` + `apply`, que gera uma key nova.
  - Se o abuso persistir mesmo com a Lambda zerada (as requisições ainda custam API Gateway), rodar o `destroy`.
- **Pior caso de custo (documentar no README):**
  - **Flood:** até o kill switch disparar (cerca de 3 min no throttle de 5 req/s ≈ 900 requisições × no máximo 10 unidades do Comprehend), menos de **US$ 1**.
  - **Abuso lento abaixo do gatilho:** limitado pela quota diária (1.000 requisições × até 10 unidades do Comprehend) a cerca de **US$ 1/dia**, com o Budget e o Cost Anomaly Detection avisando. Como a quota é best-effort, esse limite também é.
- **AWS WAF foi avaliado e descartado por enquanto:** tem custo fixo mensal (~$5-6 de Web ACL + $1/regra + $0,60 por milhão de requisições) mesmo sem tráfego, o que não se justifica neste estágio. **Essa decisão só se sustenta com o kill switch acima** — Usage Plan + Budget sozinhos não bastam, já que a AWS não os considera limites rígidos. Reavaliar (WAF com rate-based rule) se o projeto sair do portfólio e for para produção com tráfego real.
- **Infraestrutura como código com `destroy` fácil:** como o objetivo é demonstração (não uso contínuo), definir a stack via Terraform permite rodar `terraform destroy` quando o projeto não estiver sendo mostrado a ninguém, garantindo custo zero absoluto fora dos períodos de demonstração, e `terraform apply` para recriar tudo em minutos. Como nada roda localmente (ver "Execução via CI/CD"), tanto `apply` quanto `destroy` são disparados manualmente como workflows do GitHub Actions.

### Estimativa de custo por volume de uso

Estimativa **conservadora** para a stack no ar (região `us-east-1`, valores em US$, mês = 30 dias). Preços conferidos nas páginas oficiais da AWS em 24/09/2026 — **reconferir antes de fixar quotas e alertas**.

**Premissas por requisição:**

| Componente | Preço | Custo por requisição |
|---|---|---|
| Comprehend `DetectPiiEntities` (modos 1 e 3) | US$ 0,0001 por unidade de 100 caracteres, **mínimo 3 unidades** por chamada | texto ≤ 300 caracteres: **US$ 0,0003**; texto de 1.000 caracteres (10 unidades, o máximo aceito): **US$ 0,0010** |
| API Gateway (REST) | US$ 3,50 por milhão de requisições | US$ 0,0000035 |
| Lambda (256 MB, ~300 ms, arm64) | US$ 0,20 por milhão de requisições + duração por GB-s (~US$ 0,000001 por chamada) | ~US$ 0,0000012 |
| CloudWatch Logs (só metadados, ~1 KB por chamada) | US$ 0,50 por GB ingerido | ~US$ 0,0000005 |
| OpenAI Moderation (modos 2 e 3) | gratuita | US$ 0 |

- **Sem free tier:** a conta tem mais de 12 meses, então o free tier de 12 meses do Comprehend (50 mil unidades/mês de `DetectPiiEntities`) e do API Gateway não se aplica. O free tier permanente da Lambda (1 milhão de requisições e 400 mil GB-s por mês) e o de 5 GB do CloudWatch Logs cobririam todos os volumes abaixo, mas foram ignorados por conservadorismo — o custo real da Lambda e dos logs tende a zero.
- **Custo fixo mensal (independe do tráfego): ~US$ 0.** Os dois alarmes do kill switch cabem nos 10 alarmes gratuitos do CloudWatch; Parameter Store *standard*, a chave `alias/aws/ssm` e o tópico SNS não cobram por existir; os dois primeiros Budgets são gratuitos; o bucket S3 do state soma centavos por mês.
- **O Comprehend domina o custo** (~98% da requisição nos modos 1 e 3). O modo 2 (só OpenAI Moderation) não chama o Comprehend e custa uma fração disso.

**Custo estimado por cenário de uso (custo/mês):**

| Requisições/dia | Modo 2 (só OpenAI, sem Comprehend) | Modos 1/3, texto ≤ 300 car. (demo típica) | Modos 1/3, texto de 1.000 car. (máximo aceito) |
|---:|---:|---:|---:|
| 0 | US$ 0,00 | US$ 0,00 | US$ 0,00 |
| 1 | US$ 0,0002 | US$ 0,01 | US$ 0,03 |
| 5 | US$ 0,0008 | US$ 0,05 | US$ 0,15 |
| 10 | US$ 0,002 | US$ 0,09 | US$ 0,30 |
| 100 | US$ 0,02 | US$ 0,92 | US$ 3,02 |
| 500 | US$ 0,08 | US$ 4,58 | US$ 15,08 |
| 1.000 (quota diária) | US$ 0,16 | US$ 9,16 | US$ 30,16 |

**Como usar esta tabela:**
- Uso legítimo de demonstração (dezenas de chamadas por dia) custa **centavos por mês**. O risco financeiro do projeto não está no uso normal, e sim no abuso — coberto em "Pior caso de custo": o kill switch limita um flood a menos de US$ 1, e a quota limita o abuso lento a cerca de US$ 1/dia (a última linha da tabela).
- **Calibrar o Budget:** um Budget de US$ 5/mês dispara por volta de 550 requisições/dia sustentadas com texto curto, ou de 170/dia com texto de 1.000 caracteres. Como esses volumes já seriam muito acima do esperado para uma demo, qualquer alerta neles indica uso anormal.
- **Kill switch:** o limite é fixo em **mais de 10 invocações por minuto**. Uma demo feita à mão não chega perto disso; qualquer coisa acima é tratada como abuso.
- Se o projeto sair do portfólio com tráfego real, refazer a conta: acima de ~milhares de requisições/dia o Comprehend continua dominando.

## Execução via CI/CD (GitHub Actions) — tudo roda na nuvem

O objetivo é que nada do sistema — aplicação ou infraestrutura — dependa de rodar/manter algo na máquina local; tudo vive na AWS e o deploy (`apply`/`destroy`) é feito via workflow do **GitHub Actions**, o que elimina o problema clássico de credenciais da AWS armazenadas/vazadas em uma máquina de desenvolvedor.

**Exceção pontual — bootstrap inicial:** antes de o primeiro workflow existir, alguém precisa criar manualmente o IAM OIDC Identity Provider, a IAM Role de deploy, o bucket S3 do state, o Budget, o Cost Anomaly Detection e a AI services opt-out policy (ver passo 2 de "Próximos passos") — é um problema do tipo "ovo e galinha", já que o CI ainda não tem como se autenticar sozinho. Esse passo único é feito **via Console da AWS** (não via AWS CLI local com credenciais salvas), para não contradizer o objetivo de não manter credenciais de longa duração numa máquina local. Depois desse bootstrap, nenhum comando de deploy roda fora do GitHub Actions. Como esses recursos não são do Terraform, o `destroy` não os remove.

- **Workflows:**
  - `apply`: **só manual** (`workflow_dispatch`). Nunca dispara em merge na `main`, para que nenhum deploy religue a stack depois de um kill. Disparar o `apply` manualmente já é a aprovação.
  - `destroy`: manual (`workflow_dispatch`), usado para zerar custo entre demonstrações. Ele também recebe os secrets `OPENAI_API_KEY` e `ALERT_EMAIL`, porque o `terraform destroy` exige valor para toda variável obrigatória.
  - `test`: em pull request e em push na `main` (para cobrir commits feitos direto na `main`). Roda os testes unitários, `terraform fmt -check` e `terraform validate` (com `terraform init -backend=false`). Sem credenciais AWS (não pede `id-token: write` nem assume a role).
- **Hardening dos workflows** (a role de deploy tem permissões amplas, e o repositório é público):
  - actions de terceiros (`aws-actions/configure-aws-credentials`, `hashicorp/setup-terraform`, `actions/checkout`, etc.) **fixadas por SHA de commit**, não por tag, que pode ser movida;
  - `permissions:` explícitas e mínimas em cada workflow: `contents: read` em todos, e `id-token: write` só no `apply` e no `destroy`;
  - nunca usar `pull_request_target`: PRs de fork rodam o `test` sem secrets nem credenciais.
- **Autenticação via OIDC, sem access keys estáticas:** configurar um **IAM OIDC Identity Provider** para `token.actions.githubusercontent.com` e **uma única IAM Role de deploy**, assumida via `aws-actions/configure-aws-credentials` com `id-token: write`. Não existe nenhum `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` salvo em secret do GitHub — remove o risco de uma chave de longa duração vazar do repositório ou dos logs do workflow.
  - **Trust policy:** `sub = repo:<owner>/<repo>:ref:refs/heads/main`, nunca um wildcard como `repo:<owner>/<repo>:*`. Um `workflow_dispatch` disparado a partir de outra branch recebe outro `sub` e não assume a role. Os jobs não usam `environment:`, senão o `sub` passa a ter o formato `environment:<nome>` e deixa de casar com a trust policy.
- **Permissões da role de deploy:** essa role é diferente e mais ampla que as roles de execução das Lambdas. Escopar por serviço/prefixo de recurso sempre que possível, evitando `*:*`:
  - permissões por serviço: Lambda, API Gateway, SSM, CloudWatch (logs e alarme), SNS, IAM e o bucket de state;
  - **todos os recursos nomeados com o prefixo `guardrails-*`** (funções Lambda, log groups, tópico SNS, alarmes, parâmetro SSM e roles), para que as permissões da role de deploy possam ser escopadas por ARN em cada serviço que aceita restrição por recurso. O API Gateway não aceita esse tipo de escopo por nome e fica sem prefixo no ARN;
  - ações de IAM sobre roles e políticas (criar, ler, alterar, anexar, desanexar e apagar, porque o `destroy` também precisa delas) restritas ao prefixo `guardrails-*`, e `iam:PassRole` restrito ao mesmo prefixo, com `iam:PassedToService = lambda.amazonaws.com`.
  - **A própria role de deploy fica fora do prefixo `guardrails-*`**, e as permissões dela ficam numa **policy inline**, não numa managed policy `guardrails-*`. Se ela estivesse dentro do prefixo, conseguiria alterar as próprias permissões com as ações de IAM acima.
  - **Risco residual aceito:** o prefixo só restringe o **nome** das roles, não as permissões dadas a elas. A role de deploy ainda consegue criar uma `guardrails-*` com permissões amplas. A mitigação real é o `sub` restrito à `main`: só código que chegou à `main` (ou seja, do único desenvolvedor) assume a role. É um risco aceito conscientemente no lugar de uma permissions boundary, e o README deve dizer isso.
- **State do Terraform remoto (obrigatório, já que o CI não tem disco persistente):** backend em **S3** (bucket `guardrails-tfstate-moderator`) com **versionamento habilitado** no bucket (permite reverter um state corrompido) e **locking nativo do S3** (`use_lockfile = true`) para evitar concorrência entre execuções — dispensa criar uma tabela DynamoDB só para lock. A chave da OpenAI **não** vai para o state (`value_wo`, ver "Segurança"), mas o state ainda guarda dados sensíveis, como o valor da API key gerada pelo API Gateway. Por isso o bucket **deve** ter: acesso público bloqueado (Block Public Access nas 4 opções), criptografia padrão SSE-S3/KMS, versionamento, e uma bucket policy que restrinja o acesso à role de deploy e ao usuário admin usado no Console. Sem essa exceção, quem usa o Console fica sem acesso ao state, por exemplo para investigar um `destroy` que falhou. Custo: armazenamento de um arquivo de poucos KB, irrelevante.
- **Segredo da OpenAI:** o valor da API key da OpenAI é passado ao Terraform a partir de uma **GitHub Actions Secret** (`OPENAI_API_KEY`), nunca commitado em `.tfvars`, numa variável `sensitive` (e `ephemeral`), e gravado no Parameter Store como `SecureString` via `value_wo` pelo próprio `apply`. O valor não aparece nos logs do workflow nem no state.

## Boas práticas de repositório (código público)

Como o código vai para um repositório público, alguns cuidados evitam vazamento de segredo e ruído no histórico do Git:

- **`.gitignore`** cobrindo `*.tfstate`, `*.tfstate.backup`, `.terraform/`, `*.tfvars` (exceto um `terraform.tfvars.example` com valores fictícios, versionado como referência).
- **`.terraform.lock.hcl` versionado** (não entra no `.gitignore`): fixa as versões e os hashes dos providers usados pelo CI.
- **Versões fixadas:** `required_version` do Terraform (≥ 1.11, por causa do `value_wo`) e versão do provider AWS em `required_providers`.
- **Nenhum valor de segredo hardcoded** nos arquivos versionados (incluindo a API key do API Gateway, que nunca vai para o README).
- **Nenhum dado pessoal hardcoded:** o e-mail de alerta entra por secret (ver "Custos").
- **Nenhum Account ID ou ARN real hardcoded** nos arquivos `.tf` — usar variáveis e data sources como `aws_caller_identity`. O motivo aqui é **portabilidade**, não sigilo: o Account ID não é segredo, e ele aparece nos logs do Actions de qualquer forma.
- **Secret scanning e push protection** do GitHub ativados (gratuitos em repositório público): bloqueiam o push de um commit com uma chave reconhecível, como a da OpenAI ou uma access key da AWS.
- **Estrutura do repositório:**

  ```
  src/moderador/        Lambda principal (Python, sem dependências externas)
  src/kill_switch/      Lambda kill switch
  tests/                testes unitários (pytest)
  infra/                Terraform
  .github/workflows/    apply, destroy, test
  ```

### Licença

**Decisão: [PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/).** Qualquer pessoa pode ler, estudar, modificar e redistribuir o código para fins **não comerciais**. O uso com fins lucrativos não é permitido.

- Foi escolhida no lugar de **CC BY-NC 4.0** porque a própria Creative Commons não recomenda suas licenças para software. A PolyForm foi escrita para código.
- **Não é uma licença open source** pela definição da OSI, que exige permitir uso comercial. Por isso o README e este documento falam em **código público** (*source-available*), e não em "open source".
- O arquivo `LICENSE` na raiz recebe o texto oficial copiado do site da PolyForm, sem alterações, seguido da linha `Required Notice: Copyright 2026 <nome do titular>`.
- Pelos Termos de Serviço do GitHub, qualquer repositório público pode ser visualizado e ter fork **dentro do GitHub**. A licença não impede isso, só o uso comercial.

## Acesso de demonstração

- A demonstração é feita na hora, com a stack recém-criada pelo `apply`: `curl` ou Postman.
- A API key **não é publicada** no README nem em nenhum arquivo. Pegar o valor no Console (API Gateway → API Keys → Show) e repassar diretamente à pessoa.
- O README traz exemplos de `curl` dos três modos com `<API_KEY>` e `<URL>` como placeholders, e avisa que a API só fica no ar durante demonstrações — o repositório é a definição da infraestrutura, não um ambiente permanentemente no ar.
- A key muda a cada `destroy` + `apply`, já que o recurso é recriado. Um `apply` sobre a stack existente mantém a mesma key. Nos dois casos nada quebra, porque nenhum arquivo depende do valor dela.
- O throttle, a quota e o kill switch valem também durante a demo: mandar mais de 10 requisições em 1 minuto derruba a API até o próximo `apply`.

## Ameaças e mitigações

Resumo das principais ameaças consideradas e como o design responde a cada uma — checklist de segurança e base para a seção equivalente do README:

| Ameaça | Mitigação |
|---|---|
| API key vazada | A key não é publicada; a stack só fica no ar durante demos; throttle, quota e kill switch se aplicam. Para invalidar uma key vazada, `destroy` + `apply` |
| Abuso/flood gerando custo inesperado | **Kill switch** em mais de 10 invocações/min + religamento só por `apply` manual + throttle/quota do Usage Plan (best-effort) + Budgets (só avisa) + Cost Anomaly Detection |
| Kill switch falhando em silêncio | Alarme em `Errors` da Lambda kill switch, com aviso por e-mail |
| Escalonamento de privilégio via role de deploy (que cria roles IAM) | `sub` do OIDC restrito à `main` (mitigação principal) + ações de IAM e `iam:PassRole` restritos ao prefixo `guardrails-*`. Risco residual aceito e documentado: a role ainda pode criar uma `guardrails-*` com permissões amplas |
| Chave da OpenAI legível no `.tfstate` | `value_wo`: a chave não vai para o state |
| Vazamento da chave da OpenAI | `SecureString` no Parameter Store, nunca hardcoded; acesso restrito ao ARN exato do parâmetro; projeto dedicado na OpenAI, key restrita e limite de gasto |
| Escalonamento de privilégios via IAM das Lambdas | Least privilege: `comprehend:DetectPiiEntities` e `ssm:GetParameter` no ARN exato na principal, e só `lambda:PutFunctionConcurrency` no ARN da principal na do kill switch; nunca wildcard |
| Vazamento de PII via logs | Apenas metadados (modo, tamanho, latência, status), nunca `texto`/`texto_censurado` |
| Envio de PII em texto aberto para terceiro externo (OpenAI) | No modo 3, a OpenAI recebe o texto já com PII substituída pelo Comprehend. **Exceção:** no modo 2, o texto original vai para a OpenAI, que o retém por até 30 dias (documentado no README) |
| Texto enviado ao Comprehend usado pela AWS para melhorar o serviço | AI services opt-out policy no AWS Organizations |
| Action de terceiro comprometida no CI (com acesso à role de deploy) | Actions fixadas por SHA de commit, `permissions:` mínimas, sem `pull_request_target` |
| Prompt injection via texto do usuário | Nenhum modelo generativo decide o que censurar |
| Falha de um serviço de moderação liberando conteúdo não censurado | Fail-closed: erro/timeout retorna 5xx |
| Requisição malformada consumindo invocação da Lambda | Request Validator do API Gateway (tipo e valor de `modo`, tamanho mínimo e máximo de `texto`). Desvio possível por `Content-Type` diferente, rejeitado pela Lambda (ver "Segurança") |
| Custo fixo por hora sem tráfego (ex.: NAT Gateway) | Lambda sem VPC |
| Credenciais AWS de longa duração vazadas | Deploy via GitHub Actions com **OIDC** (sem access keys estáticas); bootstrap único via Console AWS |
| Segredo vazado via `.tfstate` ou `.tfvars` commitado no repositório público | `.gitignore` cobrindo state e `.tfvars`; state remoto em S3 privado; secret scanning com push protection |

## Próximos passos

1. Adicionar ao repositório o `.gitignore`, o `LICENSE` (PolyForm Noncommercial 1.0.0), a estrutura de pastas (ver "Boas práticas de repositório") e o esqueleto do Terraform, com versões fixadas e **sem VPC** para a Lambda. Ativar secret scanning e push protection nas configurações do repositório.
2. Bootstrap **via Console da AWS** (uma única vez — ver "Execução via CI/CD"): IAM OIDC Identity Provider do GitHub Actions, role de deploy (nome fora do prefixo `guardrails-*`, `sub` na `main`, permissões escopadas a `guardrails-*`), bucket S3 privado do state, Budget, Cost Anomaly Detection e AI services opt-out policy no AWS Organizations (criando a organização, se a conta for avulsa). Conferir em Service Quotas o limite de concorrência da conta. Na OpenAI: projeto dedicado, key restrita e limite de gasto, testando a Moderation com o limite ativo. No GitHub: secrets `OPENAI_API_KEY` e `ALERT_EMAIL`.
3. Workflows do GitHub Actions: `apply` e `destroy` manuais (`workflow_dispatch`), `test` em pull request e em push na `main`. Actions fixadas por SHA e `permissions:` mínimas.
4. Terraform:
   - Lambda Python 3.13, arm64, 256 MB, empacotada com `archive_file`, com HTTP via `urllib` (sem dependências), IAM restrito (incluindo logs), timeout de 15 s, log group com retenção criado antes da função e reserved concurrency de 3 se a quota permitir;
   - API Gateway REST, com Request Validator (`minLength` 1, `maxLength` 1.000, `modo` inteiro), Gateway Responses no formato `{"erro": ...}`, uma API key gerada pelo API Gateway e um Usage Plan (rate 5, burst 10, quota 1.000/dia);
   - parâmetro SSM `SecureString` com `value_wo`, a partir do GitHub Secret `OPENAI_API_KEY`.
5. Kill switch: alarme (mais de 10/min, `notBreaching`), alarme em `Errors` da Lambda kill switch, SNS com assinatura de e-mail (`ALERT_EMAIL`), Lambda com `PutFunctionConcurrency` e log group com retenção.
6. Implementar Comprehend (lista de entidades e score 0,5), OpenAI Moderation, fusão de trechos sobrepostos ou adjacentes, substituição por `***` com offset decrescente, validação (incluindo `Content-Type` e `type(modo) is int`), timeouts, fail closed com 502 e logs só com metadados.
7. Testes unitários com mocks: substituição, entidades sobrepostas e adjacentes, combinação do modo 3, fail closed, validação (texto vazio, `modo` como `"1"`, `1.0` e `true`).
8. Confirmar a assinatura de e-mail do SNS. Depois, teste real:
   - os três modos;
   - offsets com acento e emoji antes da PII;
   - `Content-Type` diferente de `application/json`, para saber se o Request Validator barra ou se a requisição chega à Lambda;
   - **kill switch**: logo depois da virada de um minuto, mandar cerca de 25 requisições **em sequência** (uma depois da outra, não em paralelo), para que mais de 10 caiam no mesmo minuto de relógio. Em paralelo, o burst de 10 e a reserved concurrency de 3 barram parte delas antes da invocação, e o `Sum` pode não passar de 10. Depois, confirmar a concorrência em 0 e o e-mail recebido, rodar o `apply` e confirmar que a stack voltou.
9. README: arquitetura, limitação de região/LGPD, retenção de dados pela AWS e pela OpenAI, só inglês, exceção do modo 2, pior caso de custo, risco residual da role de deploy, ameaças e mitigações, licença não comercial, `curl` com placeholders e aviso de que a API só fica no ar durante demos.
