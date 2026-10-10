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
- [ ] Criar `src/moderador/`
- [ ] Criar `src/kill_switch/`
- [ ] Criar `tests/`
- [ ] Criar `infra/`
- [ ] Criar `.github/workflows/`

### 2.3 Configurações do GitHub
- [ ] Ativar secret scanning
- [ ] Ativar push protection

---

## 3. Bootstrap (uma única vez, via Console)

### 3.1 AWS — acesso do CI
- [ ] Criar o IAM OIDC Identity Provider para `token.actions.githubusercontent.com`
- [ ] Criar a role de deploy com trust policy `sub = repo:<owner>/<repo>:ref:refs/heads/main`
- [ ] Anexar as permissões da role de deploy, escopadas ao prefixo `guardrails-*`
- [ ] Restringir `iam:PassRole` ao prefixo `guardrails-*` com `iam:PassedToService = lambda.amazonaws.com`

### 3.2 AWS — bucket do state
- [ ] Criar o bucket S3 do state
- [ ] Ativar Block Public Access (4 opções)
- [ ] Ativar criptografia padrão
- [ ] Ativar versionamento
- [ ] Criar a bucket policy restrita à role de deploy e ao usuário admin

### 3.3 AWS — custos e privacidade
- [ ] Criar o Budget com alerta por e-mail (ex.: US$ 5)
- [ ] Ativar o Cost Anomaly Detection
- [ ] Criar a organização no AWS Organizations (se a conta for avulsa)
- [ ] Aplicar a AI services opt-out policy
- [ ] Conferir em Service Quotas o limite de "Concurrent executions" da Lambda
- [ ] Decidir se a reserved concurrency de 3 será usada ou omitida

### 3.4 OpenAI
- [ ] Criar um projeto dedicado
- [ ] Criar uma key restrita com a menor permissão
- [ ] Configurar a allowlist de modelos (`omni-moderation-latest`), se disponível
- [ ] Definir um limite de gasto mensal baixo
- [ ] Testar a Moderation com o limite ativo

### 3.5 GitHub Secrets
- [ ] Criar o secret `OPENAI_API_KEY`
- [ ] Criar o secret `ALERT_EMAIL`

---

## 4. Workflows do GitHub Actions

### 4.1 Workflow `test`
- [ ] Disparar em pull request e em push na `main`
- [ ] Rodar os testes unitários (pytest)
- [ ] Rodar `terraform fmt -check`
- [ ] Rodar `terraform init -backend=false` e `terraform validate`
- [ ] Garantir que não pede `id-token: write` nem credenciais AWS

### 4.2 Workflow `apply`
- [ ] Disparar só por `workflow_dispatch`
- [ ] Autenticar via OIDC com `aws-actions/configure-aws-credentials`
- [ ] Passar `OPENAI_API_KEY` e `ALERT_EMAIL` como variáveis do Terraform
- [ ] Rodar `terraform init` e `terraform apply`

### 4.3 Workflow `destroy`
- [ ] Disparar só por `workflow_dispatch`
- [ ] Autenticar via OIDC
- [ ] Passar `OPENAI_API_KEY` e `ALERT_EMAIL` (exigidos pelo `destroy`)
- [ ] Rodar `terraform init` e `terraform destroy`

### 4.4 Hardening
- [ ] Fixar todas as actions de terceiros por SHA de commit
- [ ] Declarar `permissions:` mínimas (`contents: read` em todos, `id-token: write` só no `apply` e `destroy`)
- [ ] Confirmar que nenhum workflow usa `pull_request_target`
- [ ] Confirmar que nenhum job usa `environment:`

---

## 5. Infraestrutura (Terraform)

### 5.1 Base
- [ ] Fixar `required_version` (≥ 1.11) e a versão do provider AWS
- [ ] Configurar o backend S3 com `use_lockfile = true`
- [ ] Declarar as variáveis `openai_api_key` (`sensitive` e `ephemeral`) e `alert_email`
- [ ] Usar `aws_caller_identity` no lugar de Account ID hardcoded
- [ ] Versionar o `.terraform.lock.hcl`

### 5.2 Parameter Store
- [ ] Criar o parâmetro `SecureString` `guardrails-*` com `value_wo` e `value_wo_version`
- [ ] Usar a chave gerenciada `alias/aws/ssm`

### 5.3 Lambda principal
- [ ] Criar o log group com retenção (ex.: 14 dias) antes da função
- [ ] Criar a role com `comprehend:DetectPiiEntities`, `ssm:GetParameter` no ARN exato e logs no ARN do log group
- [ ] Empacotar o código com `archive_file`
- [ ] Configurar Python 3.13, arm64, 256 MB, timeout de 15 s, sem VPC
- [ ] Configurar a reserved concurrency de 3 (se a quota permitir)

### 5.4 API Gateway
- [ ] Criar a API REST e o recurso/método `POST`
- [ ] Criar o modelo JSON (`texto` 1–1.000, `modo` inteiro em `[1, 2, 3]`, ambos obrigatórios)
- [ ] Criar o Request Validator
- [ ] Configurar as Gateway Responses no formato `{"erro": ...}` (400, 403, 429, 5xx)
- [ ] Exigir API key no método
- [ ] Criar a API key gerada pelo API Gateway
- [ ] Criar o Usage Plan (rate 5, burst 10, quota 1.000/dia) e associar a key
- [ ] Criar o deployment e o stage

---

## 6. Kill switch

### 6.1 Notificação
- [ ] Criar o tópico SNS `guardrails-*`
- [ ] Criar a assinatura de e-mail a partir de `ALERT_EMAIL`

### 6.2 Lambda kill switch
- [ ] Criar o log group com retenção antes da função
- [ ] Criar a role com `lambda:PutFunctionConcurrency` no ARN da principal e logs no ARN do log group
- [ ] Implementar o código que zera a concorrência da Lambda principal
- [ ] Inscrever a Lambda no tópico SNS e dar permissão de invocação ao SNS

### 6.3 Alarmes
- [ ] Alarme em `Invocations` da principal: `Sum > 10`, 60 s, 1 de 1, `notBreaching`, ação no SNS
- [ ] Alarme em `Errors` da kill switch: `Sum > 0`, 60 s, ação no SNS

---

## 7. Aplicação (Lambda principal)

### 7.1 Validação
- [ ] Conferir o `Content-Type` = `application/json`
- [ ] Validar o JSON e a presença de `texto` e `modo`
- [ ] Validar `texto` como string de 1 a 1.000 caracteres
- [ ] Validar `modo` com `type(modo) is int` e valor em 1, 2 ou 3
- [ ] Devolver 400 com mensagem genérica

### 7.2 Configuração e clientes
- [ ] Ler a chave da OpenAI do SSM uma vez por container e cachear fora do handler
- [ ] Configurar boto3 com `connect_timeout=1`, `read_timeout=3` e `max_attempts=2`

### 7.3 Caso 1 — PII (Comprehend)
- [ ] Chamar `DetectPiiEntities`
- [ ] Filtrar pela lista fechada de entidades e score ≥ 0,5
- [ ] Fundir trechos sobrepostos ou adjacentes
- [ ] Substituir por `***` do fim para o início
- [ ] Converter offsets se necessário (caracteres não ASCII)

### 7.4 Caso 2 — Conteúdo impróprio (OpenAI)
- [ ] Chamar a Moderation (`omni-moderation-latest`) via `urllib`, timeout de 3 s e 1 retry
- [ ] Se sinalizado, devolver `***`

### 7.5 Caso 3 e resposta
- [ ] Encadear Comprehend → OpenAI com o texto já redigido
- [ ] Combinar o resultado final (`***` se sinalizado, senão texto com PII redigida)
- [ ] Devolver `{"texto_censurado": ...}`

### 7.6 Falhas e logs
- [ ] Devolver 502 em falha/timeout de Comprehend ou OpenAI (fail closed)
- [ ] Logar só metadados (modo, tamanho, latência, status)

---

## 8. Testes unitários

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

---

## 11. Conclusão

### 11.1 Revisão final
- [ ] Conferir que nenhum segredo, e-mail ou Account ID está nos arquivos versionados
- [ ] Conferir que o `PROJETO.md` reflete o que foi implementado
- [ ] Conferir que o workflow `test` passa na `main`

### 11.2 Entrega
- [ ] Confirmar que a stack está destruída (custo zero em repouso)
- [ ] Deixar o repositório público e pronto para demonstração
