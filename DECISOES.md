# Mudanças a aplicar no PROJETO.md

## Objetivo que guia as mudanças

Em caso de conflito, estes objetivos têm prioridade sobre o que o `PROJETO.md` já planeja:

1. **Simples e funcional:** um guardrail que censura PII e conteúdo impróprio. Nada além do necessário para isso.
2. **No ar só quando for usado:** `apply` para mostrar a alguém, `destroy` para remover depois. A stack não fica permanentemente no ar.
3. **Derrubada automática em caso de abuso:** mais de 10 invocações da Lambda por minuto zeram a concorrência dela, e a API para de processar texto. Requisições barradas antes da Lambda (400, 403, 429) não contam, porque não geram custo de Comprehend.
4. **Religar só manualmente:** nada religa a stack sozinho. O único caminho de volta é um `apply` disparado manualmente.

---

## Arquitetura

- Atualizar a descrição do kill switch: o gatilho passa a ser "mais de 10 invocações em 1 minuto" (ver "Custos"), e não um limite genérico de volume.
- Remover qualquer menção ao frontend (S3, CloudFront, OAC). A demonstração é feita via `curl`/Postman.

## Contrato da API

- Deixar explícito que **só texto em inglês é suportado**. Texto em outro idioma resulta em PII não detectada, sem erro.
- Listar as respostas de erro: `400` (body inválido), `403` (key ausente ou inválida), `429` (throttle/quota), `5xx` (falha ou timeout de um serviço de moderação, que é fail closed, ou kill switch ativo, com a Lambda throttled).

## Lógica de moderação

- **Caso 1:** definir a lista fechada de entidades censuradas e o score mínimo:
  - Entidades: `NAME`, `EMAIL`, `PHONE`, `ADDRESS`, `SSN`, `CREDIT_DEBIT_NUMBER`, `CREDIT_DEBIT_CVV`, `CREDIT_DEBIT_EXPIRY`, `PIN`, `BANK_ACCOUNT_NUMBER`, `BANK_ROUTING`, `PASSPORT_NUMBER`, `DRIVER_ID`, `IP_ADDRESS`, `PASSWORD`.
  - CVV, validade, PIN, routing e senha entram junto com o número de cartão e de conta, já que deixá-los passar anula a censura do número.
  - Ignorar `DATE_TIME`, `AGE`, `URL` e as demais entidades.
  - Score mínimo: **0,5**. Num guardrail, censurar demais é melhor do que deixar PII passar.
- **Caso 2:** documentar que, no **modo 2**, o texto original (com PII) vai para a OpenAI. Quem quiser proteger PII usa o modo 3.
- **Tratamento de falhas:** adicionar os timeouts:
  - Comprehend e SSM (boto3): `connect_timeout=1`, `read_timeout=3`, `retries={"max_attempts": 2, "mode": "standard"}`.
  - OpenAI (`urllib`): `timeout=3`, no máximo 1 retry.
  - Ler o parâmetro do SSM uma vez por container (cache em variável de módulo, fora do handler), como o PROJETO.md já recomenda. Assim, o SSM só entra no tempo da primeira invocação.
  - Lambda: timeout de 15 s. Se ela estourar, o API Gateway responde 5xx sem devolver texto, o que continua sendo fail closed. Por isso não é preciso checar o tempo restante no código. A soma dos piores casos com retries (cerca de 22 s com SSM, ou 14 s sem) pode passar de 15 s. Nesse caso a requisição vira 5xx, o que é aceitável.

## Segurança

- **Chave da OpenAI fora do state:** gravar o parâmetro com `value_wo` + `value_wo_version` no `aws_ssm_parameter` (Terraform ≥ 1.11). A chave nunca vai para o `.tfstate`; para rotacionar, basta incrementar `value_wo_version`. Atualizar o item "State do Terraform remoto" em "Execução via CI/CD" e a linha correspondente em "Ameaças e mitigações", que hoje dizem que a chave aparece em texto puro no state.
- **Key da OpenAI com dano limitado:** usar um projeto dedicado na OpenAI, com:
  - key restrita com a menor permissão disponível;
  - allowlist de modelos liberando só `omni-moderation-latest`, se o painel oferecer;
  - limite de gasto mensal baixo.
  Testar a Moderation com o limite ativo antes do primeiro uso, porque o limite pode bloquear até a Moderation gratuita.
- **Logs:** a retenção explícita vale só para os log groups das duas Lambdas (principal e kill switch). Remover o log do API Gateway (ver "Descartado").
- **Limite de tamanho:** fixar `maxLength` em **1.000 caracteres** no Request Validator e na Lambda.

## Custos e proteção contra gastos inesperados

- **Kill switch, calibrado para mais de 10 req/min:**
  - Alarme do CloudWatch em `Invocations` da Lambda principal: `Sum > 10` (`GreaterThanThreshold`, threshold 10), período de 60 s, 1 de 1 datapoint.
  - `treat_missing_data = notBreaching`. Depois do kill, as invocações caem a zero e o alarme volta sozinho para `OK`. Assim, se a stack for religada e o abuso continuar, ele dispara de novo.
  - A reação leva cerca de 1–3 minutos.
  - Ação: SNS → Lambda kill switch → `PutFunctionConcurrency` com `0`. A permissão é só `lambda:PutFunctionConcurrency` no ARN da função principal. Manter a assinatura de e-mail no mesmo tópico SNS. Como o tópico é recriado a cada `apply` depois de um `destroy`, a assinatura nasce pendente: confirmar o e-mail da AWS depois de cada `apply` que recria a stack, senão o aviso não chega. A Lambda do kill switch funciona mesmo sem a confirmação.
  - **Religar = rodar o workflow `apply` manualmente.** O Terraform detecta a concorrência em 0 e volta ao que o `.tf` define: 3, ou sem reserva se a reserved concurrency tiver sido omitida (ver abaixo). Não existe outro caminho de religar.
  - O `apply` sozinho mantém a mesma API key. Se o kill veio de uso da key por terceiros, religar com `destroy` + `apply`, que gera uma key nova.
  - Se o abuso persistir mesmo com a Lambda zerada (as requisições ainda custam API Gateway), rodar o `destroy`.
- **Usage Plan:** um único plano e uma única key:
  - throttle rate 5, burst 10, **acima** do limite do kill switch. Se o throttle ficasse abaixo de 10 req/min, as requisições seriam barradas antes de chegar à Lambda e o kill switch nunca dispararia;
  - quota de 1.000 requisições/dia, para cobrir abuso lento abaixo de 10/min (best-effort).
- **Reserved concurrency:** manter 3 se a quota da conta permitir (conferir em Service Quotas). Se não permitir, omitir: o throttle e o kill switch já limitam o custo. Nos dois casos, o teste do kill switch confirma que o `apply` restaura a função.
- **Pior caso de custo:** reescrever com base no kill switch de 1 minuto:
  - flood: até o kill disparar (cerca de 3 min no throttle de 5 req/s ≈ 900 requisições), menos de **US$ 1**;
  - abuso lento abaixo do gatilho: limitado pela quota diária (1.000 × até 10 unidades do Comprehend) a cerca de **US$ 1/dia**, com o Budget avisando.
  - Remover o cenário de ~US$ 400/dia, que pressupunha uma key pública e nenhum kill switch.
- **Estimativa de custo:**
  - unificar a duração da Lambda em ~300 ms e recalcular a linha dela;
  - remover S3 do frontend, CloudFront e CloudTrail do custo fixo, e trocar "os 2 alarmes do kill switch" por 1 alarme;
  - trocar a coluna e a linha de premissa de "texto de 2.000 caracteres" (20 unidades) por 1.000 caracteres (10 unidades), que passa a ser o máximo aceito, e recalcular a coluna;
  - trocar o "calibrar o kill switch" pelo valor fixo de 10/min.
- **Infraestrutura com `destroy` fácil:** manter como está. Ela já descreve o objetivo 2.

## Execução via CI/CD (GitHub Actions)

- **Workflows (substitui o fluxo atual):**
  - `apply`: **só manual** (`workflow_dispatch`). Nunca dispara em merge na `main`, para que nenhum deploy religue a stack depois de um kill.
  - `destroy`: manual (`workflow_dispatch`).
  - `test`: em pull request, roda os testes unitários. Sem credenciais AWS.
- Remover o `plan` em PR, a role de `plan` e o GitHub Environment com required reviewer. Disparar o `apply` manualmente já é a aprovação.
- **Uma única role OIDC** de deploy:
  - trust policy com `sub = repo:<owner>/<repo>:ref:refs/heads/main`;
  - permissões por serviço: Lambda, API Gateway, SSM, CloudWatch (logs e alarme), SNS, IAM e o bucket de state. Tirar S3 do frontend e CloudFront da lista atual;
  - ações de IAM sobre roles e políticas (criar, ler, alterar, anexar, desanexar e apagar, porque o `destroy` também precisa delas) restritas ao prefixo `guardrails-*`, e `iam:PassRole` restrito ao mesmo prefixo, com `iam:PassedToService = lambda.amazonaws.com`.
  - O prefixo só restringe o **nome** das roles, não as permissões dadas a elas. A role de deploy ainda consegue criar uma `guardrails-*` com permissões amplas. A mitigação real é o `sub` restrito à `main`: só código que chegou à `main` (ou seja, do único desenvolvedor) assume a role. É um risco aceito conscientemente no lugar da permissions boundary, e o README deve dizer isso.
- **Bootstrap via Console:** OIDC provider, role de deploy, bucket de state, Budget e Cost Anomaly Detection. Remover a permissions boundary.
- **State remoto em S3:** manter (o CI não tem disco persistente), com versionamento, Block Public Access, criptografia e `use_lockfile = true`. Na bucket policy, restringir o acesso à role de deploy e ao usuário admin usado no Console. Sem essa exceção, quem usa o Console fica sem acesso ao state, por exemplo para investigar um `destroy` que falhou.
- **Segredo da OpenAI:** continua vindo do GitHub Secret `OPENAI_API_KEY`, agora gravado via `value_wo`.

## Boas práticas de repositório

- Ajustar o item "Nenhum Account ID, ARN real ou valor de segredo hardcoded": o motivo de não hardcodar Account ID e ARNs é **portabilidade**, não sigilo. O Account ID não é segredo, e ele aparece nos logs do Actions de qualquer forma.

## Acesso de demonstração (substituir a seção inteira)

- A demonstração é feita na hora, com a stack recém-criada: `curl` ou Postman.
- A API key **não é publicada** no README nem em nenhum arquivo. Pegar o valor no Console (API Gateway → API Keys → Show) e repassar diretamente à pessoa.
- O README traz exemplos de `curl` dos três modos com `<API_KEY>` e `<URL>` como placeholders, e avisa que a API só fica no ar durante demonstrações.
- A key muda a cada `destroy` + `apply`, já que o recurso é recriado. Um `apply` sobre a stack existente mantém a mesma key. Nos dois casos nada quebra, porque nenhum arquivo depende do valor dela.

## Ameaças e mitigações

| Linha | Mudança |
|---|---|
| Key de demo pública | Trocar por "API key vazada": a key não é publicada, a stack só fica no ar durante demos, e o throttle, a quota e o kill switch se aplicam. Para invalidar uma key vazada, `destroy` + `apply` |
| Abuso/flood | Kill switch em mais de 10 invocações/min + religamento só por `apply` manual + throttle/quota (best-effort) + Budget + Cost Anomaly Detection |
| Escalonamento via role de deploy | `sub` do OIDC restrito à `main` (mitigação principal) + `iam:*` e `iam:PassRole` restritos ao prefixo `guardrails-*`. Remover a boundary e registrar o risco residual: a role ainda pode criar uma `guardrails-*` com permissões amplas |
| Chave da OpenAI no `.tfstate` | `value_wo`: a chave não vai para o state |
| Chave da OpenAI vazada | Adicionar: projeto dedicado na OpenAI, key restrita e limite de gasto |
| PII enviada à OpenAI | Adicionar a exceção do modo 2 |
| Frontend contornando o CloudFront | Remover |

## Próximos passos (reescrever)

1. `.gitignore` e esqueleto do Terraform (sem VPC).
2. Bootstrap via Console: OIDC provider, role de deploy (prefixo `guardrails-*`, `sub` na `main`), bucket de state, Budget e Cost Anomaly Detection. Na OpenAI: projeto dedicado, key restrita e limite de gasto.
3. Workflows: `apply` e `destroy` manuais, `test` em PR.
4. Terraform:
   - Lambda Python, empacotada com `archive_file`, com HTTP via `urllib` (sem dependências), IAM restrito, log group com retenção e reserved concurrency se a quota permitir;
   - API Gateway REST, com Request Validator (`maxLength` 1.000), uma API key e um Usage Plan (rate 5, burst 10, quota 1.000/dia);
   - parâmetro SSM com `value_wo`.
5. Kill switch: alarme (mais de 10/min, `notBreaching`), SNS com e-mail, Lambda com `PutFunctionConcurrency` e log group com retenção.
6. Implementar Comprehend (lista de entidades e score 0,5), OpenAI Moderation, substituição por `***` com offset decrescente, timeouts e fail closed.
7. Testes unitários com mocks: substituição, combinação do modo 3, fail closed, validação.
8. Confirmar a assinatura de e-mail do SNS. Depois, teste real dos três modos e **teste do kill switch**: mandar 15–20 requisições **em sequência** (uma depois da outra, não em paralelo), todas dentro de 1 minuto. Em paralelo, o burst de 10 e a reserved concurrency de 3 barram parte delas antes da invocação, e o `Sum` pode não passar de 10. Depois, confirmar a concorrência em 0 e o e-mail recebido, rodar o `apply` e confirmar que a stack voltou.
9. README: arquitetura, limitação de região/LGPD, só inglês, exceção do modo 2, pior caso de custo, ameaças e mitigações, `curl` com placeholders e aviso de que a API só fica no ar durante demos.

---

## Descartado (fora do escopo)

| Ideia | Motivo |
|---|---|
| Frontend S3 + CloudFront + OAC, CORS, `config.js`, mensagens de erro no frontend, `status.json` | A demo é feita na hora via `curl`/Postman. Era a maior fonte de complexidade |
| Key de demo pública no README/bundle e Usage Plan "demo" separado | Com a stack no ar só durante a demo, a key é repassada na hora |
| Workflows `enable`/`disable` e stack sempre no ar | Contradiz o objetivo de `destroy` quando não estiver em uso |
| `ignore_changes`, output de concorrência, `prevent_destroy`, checagem de substituição no `plan` | Só existiam porque o `apply` era automático. Com `apply` manual, o próprio `apply` é o religar |
| `plan` em PR, role de `plan`, lock só no `.tflock`, `ssm:GetParameter` para o `plan` | O `plan` em PR não é necessário para um projeto de um único desenvolvedor |
| Permissions boundary e GitHub Environment com reviewer | Num projeto de um único desenvolvedor, o `sub` restrito à `main` já limita quem assume a role. O risco residual fica aceito e documentado (ver "Execução via CI/CD"). O disparo manual já é a aprovação |
| Kill switch desabilitando a API key (`apigateway:PATCH`) | Não há garantia de que um 403 deixe de ser cobrado. Se o abuso persistir com a Lambda zerada, a resposta é o `destroy` |
| Access log do API Gateway e `aws_api_gateway_account` | Não é necessário para o guardrail funcionar |
| Alarmes de erro, throttle e duração, e CloudTrail | O único alarme necessário é o do kill switch |
| Checagem de `get_remaining_time_in_millis()` | O timeout da Lambda já resulta em 5xx (fail closed) |
| `httpx` | Exigiria um passo de build. O `urllib` resolve sem dependências |
