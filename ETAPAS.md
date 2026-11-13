# Etapas do projeto — Guardrail AI Moderator

Checklist do processo de produção, do planejamento à conclusão. Os detalhes de cada decisão estão no [PROJETO.md](PROJETO.md).

Marque `[x]` conforme for concluindo.

---

## 1. Planejamento

### 1.1 Definição do escopo
- [x] Registrar a ideia inicial do projeto
- [x] Definir o objetivo: API que censura PII e conteúdo impróprio com `***`
- [x] Restringir o escopo a texto em inglês (limitação do `DetectPiiEntities`)
- [x] Definir os princípios (simples, no ar só quando usado, derrubada automática, religar só manualmente)

### 1.2 Arquitetura
- [x] Escolher os serviços: Lambda, API Gateway, Comprehend, OpenAI Moderation, Parameter Store
- [x] Definir a região `us-east-1` (Comprehend indisponível em `sa-east-1`)
- [x] Decidir Lambda sem VPC (evitar NAT Gateway)
- [x] Decidir autenticação por API Key + Usage Plan
- [x] Documentar o impacto de região e retenção de dados (LGPD, opt-out da AWS, retenção da OpenAI)

### 1.3 Contrato da API
- [x] Definir o request (`texto` de 1 a 1.000 caracteres, `modo` inteiro 1/2/3)
- [x] Definir o response (`texto_censurado`)
- [x] Definir a tabela de erros (400, 403, 429, 502, outros 5xx) e o corpo `{"erro": ...}`

### 1.4 Lógica de moderação
- [x] Caso 1: lista fechada de entidades de PII e score mínimo 0,5
- [x] Caso 1: fusão de trechos sobrepostos/adjacentes e substituição do fim para o início
- [x] Caso 2: texto sinalizado vira `***` inteiro
- [x] Caso 3: ordem fixa Comprehend → OpenAI, com PII já redigida
- [x] Tratamento de falhas: fail closed e timeouts

### 1.5 Segurança
- [x] Chave da OpenAI em `SecureString` com `value_wo` (fora do state)
- [x] IAM least privilege para as duas Lambdas
- [x] Validação no API Gateway e repetida na Lambda
- [x] Política de logs (só metadados, retenção explícita)
- [x] Tabela de ameaças e mitigações

### 1.6 Custos
- [x] Kill switch (mais de 10 invocações/min zera a concorrência)
- [x] Usage Plan (rate 5, burst 10, quota 1.000/dia), Budget e Cost Anomaly Detection
- [x] Estimativa de custo por volume e pior caso
- [x] Avaliar e descartar o AWS WAF por enquanto

### 1.7 CI/CD e repositório
- [x] Definir os workflows `apply`, `destroy` e `test`
- [x] Definir OIDC, trust policy restrita à `main` e permissões da role de deploy
- [x] Definir state remoto em S3 com versionamento e lock nativo
- [x] Definir boas práticas do repositório público e estrutura de pastas
- [x] Escolher a licença (PolyForm Noncommercial 1.0.0)

### 1.8 Revisão do documento
- [x] Remover conteúdo redundante
- [x] Registrar e aplicar o log de decisões da revisão
- [x] Commitar a revisão atual do `PROJETO.md`

---

## 2. Preparação do repositório

### 2.1 Arquivos base
- [x] Criar o `.gitignore` (`*.tfstate`, `*.tfstate.backup`, `.terraform/`, `*.tfvars`)
- [x] Criar o `terraform.tfvars.example` com valores fictícios
- [x] Criar o `LICENSE` com o texto oficial da PolyForm Noncommercial 1.0.0
- [x] Adicionar a linha `Required Notice: Copyright 2026 <nome do titular>` no `LICENSE`

### 2.2 Estrutura de pastas
- [x] Criar `src/moderador/`
- [x] Criar `src/kill_switch/`
- [x] Criar `tests/`
- [x] Criar `infra/`
- [x] Criar `.github/workflows/`

### 2.3 Configurações do GitHub
- [X] Ativar secret scanning
- [X] Ativar push protection

---

## 3. Bootstrap (uma única vez, via Console)

### 3.0 AWS — usuário admin
- [x] Ativar MFA no root e confirmar que ele não tem access keys
- [x] Ativar o acesso de usuários IAM ao Billing (Account → IAM user and role access to Billing information)
- [x] Criar o grupo `admins` com a policy `AdministratorAccess`
- [x] Criar o usuário admin com acesso ao Console, no grupo `admins`, sem o prefixo `guardrails-*` e sem access keys
- [x] Logar como admin e ativar MFA no usuário
- [x] Seguir o resto do bootstrap como admin (root só para tarefas exclusivas de root)

### 3.1 AWS — acesso do CI
- [x] Criar o IAM OIDC Identity Provider para `token.actions.githubusercontent.com`
- [x] Criar a role de deploy com trust policy `sub = repo:<owner>@<owner_id>/<repo>@<repo_id>:ref:refs/heads/main`
- [x] Nomear a role de deploy fora do prefixo `guardrails-*` (para ela não poder alterar as próprias permissões)
- [x] Ajustar a trust policy gerada pelo Console para `StringEquals` com um único `sub` exato (o assistente gerou `StringLike` com valor duplicado)
- [x] Corrigir o `sub` para o formato com IDs imutáveis (`<owner>@<owner_id>/<repo>@<repo_id>`), que é o que o GitHub envia; o formato só com nomes dava `Not authorized to perform sts:AssumeRoleWithWebIdentity` (valor exato conferido no CloudTrail)
- [x] Anexar as permissões da role de deploy, escopadas ao prefixo `guardrails-*`
- [x] Restringir `iam:PassRole` ao prefixo `guardrails-*` com `iam:PassedToService = lambda.amazonaws.com`
- [x] Usar policy inline (`deploy-permissions`) em vez de managed policy, para a role não poder editar as próprias permissões
- [x] Incluir na policy o acesso ao bucket do state (`guardrails-tfstate-moderator`), que ainda será criado no 3.2

### 3.2 AWS — bucket do state
- [x] Criar o bucket S3 do state (`guardrails-tfstate-moderator`, em `us-east-1`)
- [x] Ativar Block Public Access (4 opções)
- [x] Desativar ACLs (Object Ownership: *Bucket owner enforced*)
- [x] Ativar criptografia padrão SSE-S3 (sem KMS, para a role de deploy não precisar de permissões de KMS)
- [x] Ativar versionamento
- [x] Criar a bucket policy restrita à role de deploy e ao usuário admin
- [x] Escrever a bucket policy só com `Deny`: negar quem não for a role de deploy ou o admin (`aws:PrincipalArn` com `StringNotEquals`)
- [x] Negar na bucket policy o acesso sem HTTPS (`aws:SecureTransport = false`)

### 3.3 AWS — custos e privacidade
- [x] Criar o Budget com alerta por e-mail (ex.: US$ 5)
- [x] Usar o template *Monthly cost budget* de US$ 5/mês (alertas em 85% real, 100% real e 100% previsto)
- [x] Ativar o Cost Anomaly Detection
- [x] Configurar o Cost Anomaly Detection com resumo diário por e-mail e threshold de US$ 1 (sem SNS fora do Terraform)
- [x] Criar a organização no AWS Organizations (se a conta for avulsa)
- [x] Aplicar a AI services opt-out policy
- [x] Escrever a opt-out policy em `default` (todos os AI services), anexada à Root, com `@@none` para impedir sobrescrita
- [x] Conferir em Service Quotas o limite de "Concurrent executions" da Lambda — 10 em `us-east-1`
- [x] Decidir se a reserved concurrency de 3 será usada ou omitida — omitida (limite de 10 abaixo dos 103 necessários, sem pedido de aumento)

### 3.4 OpenAI
- [x] Criar projeto guardrails-moderator
- [x] Criar uma key de service account, Restricted, só com "Model capabilities"
- [x] Configurar a allowlist de modelos (`omni-moderation-latest`)
- [x] Definir budget mensal do projeto de US$ 1.00; auto recharge desligado
- [x] Testar a Moderation; flagged False/True conforme esperado; chat/completions
    bloqueado pela allowlist

### 3.5 GitHub Secrets
- [x] Criar o secret `OPENAI_API_KEY`
- [x] Criar o secret `ALERT_EMAIL`
- [x] Criar o secret `AWS_ROLE_ARN` (ARN da role de deploy, fora do repositório por conter o Account ID)

---

## 4. Workflows do GitHub Actions

### 4.1 Workflow `test`
- [x] Disparar em pull request e em push na `main`
- [x] Push só na `main` (evita execução duplicada em PR de branch própria)
- [x] Rodar os testes unitários (pytest)
- [x] Fixar a versão do pytest (`9.1.1`) e o Python em `3.13` (mesma da Lambda)
- [x] Aceitar o código 5 do pytest (nenhum teste coletado) só até a etapa 8
- [x] Rodar `terraform fmt -check`
- [x] Rodar `terraform init -backend=false` e `terraform validate`
- [x] Fixar o Terraform em `1.11.4` (mesma versão a usar no `apply`/`destroy`)
- [x] Adiantar o `infra/versions.tf` da 5.1 para o `validate` não falhar com `infra/` vazia
- [x] Garantir que não pede `id-token: write` nem credenciais AWS
- [x] Adiantar a 4.4 no `test`: actions por SHA, `permissions: contents: read`, sem `pull_request_target` e sem `environment:`
- [x] Checkout com `persist-credentials: false` e `timeout-minutes: 10` no job

### 4.2 Workflow `apply`
- [x] Disparar só por `workflow_dispatch`
- [x] Autenticar via OIDC com `aws-actions/configure-aws-credentials`
- [x] Passar `OPENAI_API_KEY` e `ALERT_EMAIL` como variáveis do Terraform
- [x] Rodar `terraform init` e `terraform apply`
- [x] Fixar o `configure-aws-credentials` por SHA (`v6.3.0`) e reusar os SHAs de `checkout` e `setup-terraform` do `test`
- [x] ARN da role no secret `AWS_ROLE_ARN` (Account ID fora do repo) e `mask-aws-account-id: true` (log público)
- [x] `role-session-name: guardrails-apply` para identificar o apply no CloudTrail
- [x] Job só roda na `main` (`if: github.ref == 'refs/heads/main'`)
- [x] Secrets via `TF_VAR_*` só no passo do apply (nada de `-var=` na linha de comando)
- [x] `terraform apply -auto-approve -input=false` (o clique em "Run workflow" é a aprovação)
- [x] `concurrency` group `terraform` compartilhado com o destroy, sem cancelar execução em andamento
- [x] `timeout-minutes: 30` no job e checkout com `persist-credentials: false`
- [x] Adiantar a 4.4: actions por SHA, `contents: read` + `id-token: write`, sem `environment:`
- [x] Smoke test do OIDC com `infra/` vazia (0 recursos)

### 4.3 Workflow `destroy`
- [x] Disparar só por `workflow_dispatch`
- [x] Autenticar via OIDC
- [x] Passar `OPENAI_API_KEY` e `ALERT_EMAIL` (exigidos pelo `destroy`)
- [x] Rodar `terraform init` e `terraform destroy`
- [x] Cópia do `apply.yml` com os mesmos SHAs; só mudam `name`, job, `role-session-name` e o comando final
- [x] `role-session-name: guardrails-destroy` para separar apply e destroy no CloudTrail
- [x] `concurrency` group `terraform` compartilhado com o apply, sem cancelar execução em andamento (destroy pela metade deixaria recursos e lock presos)
- [x] Job só roda na `main` e secrets via `TF_VAR_*` só no passo do destroy
- [x] `terraform plan -destroy -out=tfplan > /dev/null` + `terraform apply tfplan` (o clique em "Run workflow" é a aprovação; o log não imprime o plano, que mostraria a API key — ver 5.4)
- [x] Recursos do bootstrap (state, OIDC, role, Budget) ficam fora do Terraform e não são apagados
- [x] Adiantar a 4.4: actions por SHA, `contents: read` + `id-token: write`, sem `environment:`
- [x] Smoke test do OIDC com `infra/` vazia (0 recursos destruídos)

### 4.4 Hardening
- [x] Fixar todas as actions de terceiros por SHA de commit
- [x] Declarar `permissions:` mínimas (`contents: read` em todos, `id-token: write` só no `apply` e `destroy`)
- [x] Confirmar que nenhum workflow usa `pull_request_target`
- [x] Confirmar que nenhum job usa `environment:`
- [x] Etapa só de conferência: os três workflows já nasceram conformes (4.1 a 4.3), sem mudança de arquivo
- [x] SHAs conferidos contra as tags com `git ls-remote` e no log ("Download action repository" com SHA completo)
- [x] Buscas extras sem ocorrência: access keys AWS, `arn:aws:`, Account ID, `upload-artifact`, `-var=`, gatilhos `workflow_run`/`issue_comment`/`schedule`
- [x] OIDC comprovado pelo log do `configure-aws-credentials` ("Authenticated as ...:guardrails-apply"); a lista "GITHUB_TOKEN Permissions" pode omitir o IdToken
- [x] Repo (Settings → Actions → General): token padrão só leitura, Actions sem permissão de criar/aprovar PR, actions exigidas por SHA completo e aprovação obrigatória para workflows de PR de fork

---

## 5. Infraestrutura (Terraform)

### 5.1 Base
- [x] Fixar `required_version` (≥ 1.11) e a versão do provider AWS (`~> 6.69`, adiantado na 4.1)
- [x] Configurar o backend S3 com `use_lockfile = true`
- [x] Declarar as variáveis `openai_api_key` (`sensitive` e `ephemeral`) e `alert_email`
- [x] Usar `aws_caller_identity` no lugar de Account ID hardcoded
- [x] Versionar o `.terraform.lock.hcl`
- [x] Bloco `provider "aws"` com `region = "us-east-1"` e `default_tags` (`Projeto`, `Gerenciado`; a role de deploy já tem `iam:TagRole`/`iam:TagPolicy`)
- [x] Backend sem `dynamodb_table` e sem KMS (`encrypt = true` com SSE-S3)
- [x] Key do state: `guardrails-moderator/terraform.tfstate`
- [x] `alert_email` `sensitive` (dado pessoal) e não `ephemeral` (fica no state pela assinatura SNS)
- [x] Lock com hashes de `linux_amd64` e `windows_amd64` (`terraform providers lock`)
- [x] CI com `-lockfile=readonly` no `terraform init` dos três workflows
- [x] Smoke test do backend com 0 recursos (apply e destroy; state no S3, sem `.tflock` sobrando)

### 5.2 Parameter Store
- [x] Criar o parâmetro `SecureString` `guardrails-*` com `value_wo` e `value_wo_version`
- [x] Usar a chave gerenciada `alias/aws/ssm`
- [x] Nome `guardrails-openai-api-key`, sem barra (casa com `parameter/guardrails-*` da `deploy-permissions`)
- [x] `tier = "Standard"` e `key_id` explícito (padrão documentado)
- [x] Rotação: trocar o secret `OPENAI_API_KEY` e incrementar `value_wo_version`
- [x] Conferido no Console (tipo, tier, KMS, tags, criado pela sessão `guardrails-apply`)
- [x] Conferido que o state não contém a chave (busca por `sk-` na versão do state com o parâmetro)
- [x] Smoke test com 1 recurso (apply, apply sem mudança, destroy; sem `.tflock` sobrando)

### 5.3 Lambda principal
- [x] Acrescentar `iam:ListRoleTags` e `iam:ListPolicyTags` ao bloco `IamRolesAndPolicies` da `deploy-permissions` (exigidas pelo `default_tags`)
- [x] Criar o log group com retenção (ex.: 14 dias) antes da função
- [x] Criar a role com `comprehend:DetectPiiEntities`, `ssm:GetParameter` no ARN exato e logs no ARN do log group
- [x] Empacotar o código com `archive_file`
- [x] Configurar Python 3.13, arm64, 256 MB, timeout de 15 s, sem VPC
- [x] Não definir `reserved_concurrent_executions` (reserved concurrency omitida na 3.3)
- [x] Provider `hashicorp/archive` (`~> 2.8`) no lock, com hashes de `linux_amd64` e `windows_amd64`
- [x] Código provisório `src/moderador/app.py` (502 fail closed) até a etapa 7; handler `app.handler`
- [x] Nomes `guardrails-moderador` e `guardrails-moderador-role`; policy inline, sem managed policy da AWS
- [x] `resources = ["*"]` só em `DetectPiiEntities` (ação sem resource type)
- [x] Logs no ARN do log group com sufixo `:*`
- [x] Dependência do log group via `logging_config`, sem `depends_on`
- [x] Omitido, `reserved_concurrent_executions` vale -1, o que permite ao `apply` religar a função depois do kill switch (prova real na 9.3)
- [x] Conferências finais no Console e smoke test concluídos

### 5.4 API Gateway
- [x] Criar a API REST e o recurso/método `POST`
- [x] Criar o modelo JSON (`texto` 1–1.000, `modo` inteiro em `[1, 2, 3]`, ambos obrigatórios)
- [x] Criar o Request Validator
- [x] Configurar as Gateway Responses no formato `{"erro": ...}` (400, 403, 429, 5xx)
- [x] Exigir API key no método
- [x] Criar a API key gerada pelo API Gateway
- [x] Criar o Usage Plan (rate 5, burst 10, quota 1.000/dia) e associar a key
- [x] Criar o deployment e o stage
- [x] Rota `POST /v1/moderar`, endpoint `REGIONAL`
- [x] `aws_lambda_permission` com `source_arn` exato (stage, método e caminho); o botão Test do Console do API Gateway fica de fora
- [x] Deployment com `triggers` no hash do `api_gateway.tf` e `create_before_destroy`
- [x] Gateway Responses com `status_code` igual ao padrão da AWS nos 5 tipos (sem isso, todo `apply` mostra 5 changes; corrigido e conferido na 6.1) e nulo em `DEFAULT_4XX` e `DEFAULT_5XX`, a rede de segurança
- [x] `value` da `usage_plan_key` não é `sensitive` no provider 6.70.0: workflow `destroy` sem imprimir o plano
- [x] Medido: `modo` `1.0` e `application/json; charset=utf-8` com corpo inválido voltam 400 no validator
- [x] Medido: `text/plain` com corpo inválido passa sem validar e invoca a Lambda (desvio aceito no PROJETO.md)
- [x] Medido: em rajadas de 40 em ~0,6 s, só 1 de 120 requisições recebeu 429; o throttle não segura rajada curta, e o excesso apareceu como 500 no formato `{"erro": ...}`
- [x] Smoke test concluído (apply, apply sem mudança, testes de 400/403/429, logs, destroy)

---

## 6. Kill switch

### 6.1 Notificação
- [x] Criar o tópico SNS `guardrails-*`
- [x] Criar a assinatura de e-mail a partir de `ALERT_EMAIL`
- [x] Tópico `guardrails-alertas`, Standard, em `infra/kill_switch.tf` (a 6.2 e a 6.3 continuam nele)
- [x] Sem `kms_master_key_id`: alarme do CloudWatch não publica em tópico com `alias/aws/sns` (a 6.3 provou que publica no tópico sem criptografia)
- [x] Protocolo `email`, com `endpoint` vindo de `var.alert_email` (`(sensitive value)` no plano)
- [x] Confirmação manual a cada `destroy` + `apply`; a AWS apaga a assinatura pendente em 48 h
- [x] Smoke test concluído (apply, confirmação, publicação manual, apply sem mudança, destroy)

### 6.2 Lambda kill switch
- [x] Criar o log group com retenção antes da função
- [x] Criar a role com `lambda:PutFunctionConcurrency` no ARN da principal e logs no ARN do log group
- [x] Implementar o código que zera a concorrência da Lambda principal
- [x] Inscrever a Lambda no tópico SNS e dar permissão de invocação ao SNS
- [x] Nomes `guardrails-kill-switch` e `guardrails-kill-switch-role`, em `infra/kill_switch.tf`, reaproveitando a trust policy da principal
- [x] Python 3.13, arm64, 128 MB, timeout de 10 s, sem VPC e sem reserva
- [x] Nome da principal em `FUNCAO_ALVO`; o código não lê o evento e não captura exceção (erro conta no alarme de `Errors` da 6.3)
- [x] `aws_lambda_permission` com `source_arn` no tópico; a assinatura `lambda` se confirma sozinha
- [x] Smoke test concluído (apply, Publish manual zera a concorrência, apply religa com `0 -> -1`, apply sem mudança, destroy); `PutFunctionConcurrency(0)` aceito com o limite de 10 da conta

### 6.3 Alarmes
- [x] Alarme em `Invocations` da principal: `Sum > 10`, 60 s, 1 de 1, `notBreaching`, ação no SNS
- [x] Alarme em `Errors` da kill switch: `Sum > 0`, 60 s, ação no SNS
- [x] Nomes `guardrails-moderador-invocacoes` e `guardrails-kill-switch-erros`, no fim do `infra/kill_switch.tf`
- [x] Alarme de `Errors` também com 1 de 1 e `notBreaching` (com `missing` ficaria preso em ALARM e não avisaria uma segunda falha)
- [x] Só `alarm_actions`: `ok_actions` no tópico derrubaria a API a cada volta para OK
- [x] Sem `aws_sns_topic_policy`: a política padrão deixa o CloudWatch da conta publicar no tópico sem criptografia (comprovado no smoke test)
- [x] Smoke test concluído (15 chamadas em sequência zeram a concorrência em ~65 s, `set-alarm-state` prova a ação do alarme de `Errors`, apply religa, apply sem mudança, destroy); o alarme volta a OK em ~6 min

---

## 7. Aplicação (Lambda principal)

### 7.1 Validação
- [x] Conferir o `Content-Type` = `application/json`, sem diferenciar maiúsculas e aceitando parâmetros como `; charset=utf-8` (o validator aceita, ver 5.4)
- [x] Validar o JSON e a presença de `texto` e `modo`
- [x] Validar `texto` como string de 1 a 1.000 caracteres
- [x] Validar `modo` com `type(modo) is int` e valor em 1, 2 ou 3
- [x] Devolver 400 com mensagem genérica
- [x] `validar(event)` separada do handler: devolve `(texto, modo)` ou `None`; o 400 usa a mesma mensagem da Gateway Response `BAD_REQUEST_BODY` (`"requisição inválida"`)
- [x] `Content-Type` ausente é recusado; comparação exata do tipo, sem o parâmetro (`application/jsonx` é recusado)
- [x] `RecursionError` capturado junto com `ValueError` (JSON muito aninhado chega sem validação via `text/plain`)
- [x] Surrogate solto (`"\ud800"`) recusado com 400 antes de qualquer chamada externa
- [x] Requisição válida continua em 502 (fail closed) até a 7.5
- [x] Medido: `text/plain` passa a receber 400 da Lambda (na 5.4 recebia 502); 6 chamadas na Lambda, nenhuma no limite do kill switch
- [x] Quem respondeu se vê pelo corpo: a Lambda devolve `{"erro": "..."}` (com espaço), o API Gateway `{"erro":"..."}` (sem espaço)

### 7.2 Configuração e clientes
- [x] Ler a chave da OpenAI do SSM uma vez por container e cachear fora do handler
- [x] Configurar boto3 com `connect_timeout=1`, `read_timeout=3` e `total_max_attempts=2` (2 tentativas: 1 chamada + 1 retry)
- [x] `total_max_attempts: 2` em vez de `max_attempts: 2`: no `Config` do boto3, `max_attempts` conta só os retries (seriam 3 tentativas e ~30 s no pior caso); conferido no código do botocore
- [x] Clientes `ssm` e `comprehend` criados no import, com a mesma `Config`
- [x] Chave lida na primeira requisição de modo 2 ou 3 (não no import): 400 e modo 1 não chamam o SSM, e falha do SSM não fica no cache nem derruba a fase INIT
- [x] Erro do SSM sobe sem tratamento até a 7.6 (vira 5xx, fail closed)
- [x] Medido: 5 chamadas com a resposta esperada (modo 1, 2, 3 e 2 → 502 da Lambda; `text/plain` → 400); 1 `GetParameter` no CloudTrail para 3 chamadas de modo 2/3, com `withDecryption` true e sem o valor da chave

### 7.3 Caso 1 — PII (Comprehend)
- [x] Chamar `DetectPiiEntities`
- [x] Filtrar pela lista fechada de entidades e score ≥ 0,5
- [x] Fundir trechos sobrepostos ou adjacentes
- [x] Substituir por `***` do fim para o início
- [x] Converter offsets se necessário (caracteres não ASCII): não necessário, offsets em code points
- [x] `redigir(texto, entidades)` separada da chamada ao Comprehend (`_detectar_pii`): a lógica de substituição testa sem mock
- [x] Fusão com `<=` (adjacentes viram um `***` só) e `max` (entidade dentro de outra); separadas por 1 caractere continuam dois `***`
- [x] O texto redigido substitui `texto` no handler: no modo 3 a OpenAI só recebe o texto já redigido
- [x] Comprehend chamado antes da leitura do SSM (ordem do Caso 3)
- [x] Medido no CloudShell: offsets em code points (20 em `"Café ☕ — ..."`, 13 com emoji fora do BMP), `EndOffset` exclusivo; texto só com espaços devolve `{"Entities": []}` sem erro
- [x] Medido pela API: modos 1, 3 e 2 → 502 da Lambda (Comprehend chamado pela role sem erro); `text/plain` → 400

### 7.4 Caso 2 — Conteúdo impróprio (OpenAI)
- [x] Chamar a Moderation (`omni-moderation-latest`) via `urllib`, timeout de 3 s e 1 retry
- [x] Se sinalizado, devolver `***`
- [x] `_consultar_openai(texto)` (rede e retry) separada de `sinalizado(resposta)` (função pura, testa sem mock)
- [x] 2 tentativas no total, sem espera entre elas; retry só em timeout, erro de conexão e HTTP 500/502/503/504; 4xx (401, 403, 429) sobe na primeira
- [x] `flagged is not False`: só o `false` exato libera; resposta fora do formato levanta erro (fail closed)
- [x] Texto sinalizado já devolve 200 com `{"texto_censurado": "***"}`; texto não sinalizado continua em 502 até a 7.5
- [x] A chave é lida dentro de `_consultar_openai`: modo 1 e 400 não leem o SSM nem chamam a OpenAI
- [x] Erro da OpenAI sobe sem tratamento até a 7.6 (vira 5xx, fail closed)
- [x] `timeout=3` do `urlopen` vale por operação do socket (conectar e cada leitura), não pela chamada inteira; o teto é o timeout de 15 s da Lambda
- [x] Medido pela API: modos 2 e 3 com texto sinalizado → 200 `***`; texto limpo e modo 1 → 502 da Lambda; `text/plain` → 400

### 7.5 Caso 3 e resposta
- [x] Encadear Comprehend → OpenAI com o texto já redigido
- [x] Combinar o resultado final (`***` se sinalizado, senão texto com PII redigida)
- [x] Devolver `{"texto_censurado": ...}`
- [x] Encadeamento já vinha da 7.4 (mesma variável `texto`, Comprehend antes da OpenAI); a 7.5 só acrescenta a prova
- [x] Uma saída só: `texto = CENSURA` se sinalizado e um único `return _resposta(200, {"texto_censurado": texto})`
- [x] Resposta só com `texto_censurado`; acento e emoji voltam como caracteres (`ensure_ascii=False`)
- [x] Sem `try/except` no handler: toda falha sobe e vira 5xx do API Gateway até a 7.6; o 502 provisório saiu
- [x] Modo 1 devolve conteúdo impróprio sem censura; modo 2 devolve a PII (comportamento esperado dos modos)
- [x] Medido localmente: `py_compile` sem saída e 44 verificações, 0 falhas (Comprehend e OpenAI falsos)
- [x] Medido pela API: os três modos com 200 (PII → `***`, texto limpo volta igual, sinalizado → `***`, nome + ameaça no modo 3 → só `***`); `"Café ☕ — my name is John Smith"` → `"Café ☕ — my name is ***"`; texto só com espaços no modo 3 volta igual (a Moderation aceita); `text/plain` → 400
- [x] Conferido no Console depois do teste: logs só com START/END/REPORT (nenhum texto), 9 invocações e 0 erros, alarme OK

### 7.6 Falhas e logs
- [ ] Devolver 502 em falha/timeout de Comprehend ou OpenAI (fail closed)
- [ ] Logar só metadados (modo, tamanho, latência, status)

---

## 8. Testes unitários

### 8.0 Configuração
- [ ] Criar o `pytest.ini` (`testpaths = tests`, `pythonpath = src`)
- [ ] Remover do workflow `test` a aceitação do código 5 (`|| [ "$?" -eq 5 ]`)

### 8.1 Substituição
- [ ] Substituição simples de uma entidade
- [ ] Entidades sobrepostas
- [ ] Entidades adjacentes (sem gerar `******`)
- [ ] Entidades fora da lista ou abaixo do score são ignoradas

### 8.2 Modos
- [ ] Modo 1
- [ ] Modo 2 (sinalizado e não sinalizado)
- [ ] Modo 3 (OpenAI recebe o texto redigido, combinação final)

### 8.3 Validação
- [ ] Texto vazio, ausente e acima de 1.000 caracteres
- [ ] `modo` como `"1"`, `1.0` e `true`
- [ ] JSON malformado e `Content-Type` errado

### 8.4 Falhas
- [ ] Fail closed com erro do Comprehend
- [ ] Fail closed com erro/timeout da OpenAI

### 8.5 Kill switch
- [ ] Teste da Lambda kill switch com mock do `PutFunctionConcurrency`

---

## 9. Teste real na AWS

### 9.1 Primeiro deploy
- [ ] Apagar log groups `guardrails-*` que sobraram de um `destroy` anterior, se houver
- [ ] Rodar o workflow `apply`
- [ ] Confirmar a assinatura de e-mail do SNS
- [ ] Pegar a API key no Console

### 9.2 Funcionalidade
- [ ] Testar o modo 1
- [ ] Testar o modo 2
- [ ] Testar o modo 3
- [ ] Testar offsets com acento e emoji antes da PII (`"Café ☕ — my name is John Smith"`)
- [ ] Testar os erros 400, 403 e 429 no formato `{"erro": ...}`
- [ ] Testar `Content-Type` diferente de `application/json` e documentar o resultado

### 9.3 Kill switch
- [ ] Logo após a virada de um minuto, mandar ~25 requisições em sequência
- [ ] Confirmar a concorrência da Lambda principal em 0
- [ ] Confirmar o e-mail de aviso recebido
- [ ] Rodar o `apply` e confirmar que a stack voltou

### 9.4 Encerramento do teste
- [ ] Conferir nos logs que não há texto nem PII
- [ ] Rodar o workflow `destroy`
- [ ] Conferir no Console que os recursos foram removidos

---

## 10. Documentação (README)

### 10.1 Conteúdo técnico
- [ ] Arquitetura e diagrama
- [ ] Contrato da API e exemplos de `curl` dos três modos com `<API_KEY>` e `<URL>`
- [ ] Aviso de que a API só fica no ar durante demonstrações

### 10.2 Limitações e riscos
- [ ] Só inglês
- [ ] Limitação de região/LGPD
- [ ] Retenção de dados pela AWS e pela OpenAI
- [ ] Exceção do modo 2 (texto original vai para a OpenAI)
- [ ] Comprehend não é garantia de anonimização
- [ ] Risco residual da role de deploy
- [ ] Desvio do `Content-Type`, se confirmado no teste real

### 10.3 Custos, segurança e licença
- [ ] Pior caso de custo
- [ ] Tabela de ameaças e mitigações
- [ ] Licença não comercial (código público, não open source)

### 10.4 Manual para criar moderadores de texto (pós-conclusão)
- [ ] Após concluir e revisar o projeto, criar um manual em Markdown para ensinar a adaptar sua lógica e criar moderadores de texto para outros programas
- [ ] Explicar o fluxo de moderação e como substituir ou integrar os serviços usados neste projeto
- [ ] Separar os conceitos reutilizáveis das escolhas específicas de infraestrutura e serviços deste projeto

---

## 11. Conclusão

### 11.1 Revisão final
- [ ] Conferir que nenhum segredo, e-mail ou Account ID está nos arquivos versionados
- [ ] Conferir que o `PROJETO.md` reflete o que foi implementado
- [ ] Conferir que o workflow `test` passa na `main`

### 11.2 Entrega
- [ ] Confirmar que a stack está destruída (custo zero em repouso)
- [ ] Deixar o repositório público e pronto para demonstração
