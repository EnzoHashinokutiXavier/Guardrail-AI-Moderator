# Guardrail AI Moderator

## Objetivo

API serverless na AWS que recebe um texto **em inglês** e censura partes dele (substituindo por `***`), de acordo com o tipo de censura solicitado: informações pessoais, conteúdo inapropriado, ou ambos.

**Por que só inglês:** o `DetectPiiEntities` do Amazon Comprehend só suporta detecção de PII em inglês e espanhol — português não é suportado. Como o objetivo é usar o Comprehend pronto (sem treinar/mantar um modelo próprio), o escopo do projeto foi restrito a texto em inglês. Isso também elimina a necessidade de detectar documentos específicos de um país (como CPF), já que os tipos de entidade nativos do Comprehend (nome, e-mail, telefone, endereço, SSN, cartão de crédito, etc.) cobrem bem o caso de uso em inglês.

## Arquitetura

- **AWS Lambda** — executa a lógica de moderação, cobrança apenas pelo tempo de execução real.
- **API Gateway** — expõe a Lambda como endpoint HTTP e cuida da autenticação via API Keys.
- **Amazon Comprehend** — detecção de informações pessoais (PII) no texto.
- **OpenAI Moderation API** — detecção de conteúdo inapropriado.
- **AWS Systems Manager Parameter Store** — guarda de forma segura (`SecureString`) a chave da OpenAI usada pela Lambda.
- **CloudWatch Alarm + SNS + Lambda "kill switch"** — contenção automática de custo: se o volume de invocações passar de um limite, zera a reserved concurrency da Lambda principal (ver "Custos").

```
Cliente → API Gateway (valida API Key) → Lambda → Comprehend / OpenAI Moderation → Texto censurado
```

### Região da AWS

O **Amazon Comprehend não está disponível na região São Paulo (`sa-east-1`)**. Isso obriga a stack a rodar em outra região (ex.: `us-east-1`) para poder usar `DetectPiiEntities` — Lambda, API Gateway e Parameter Store devem ficar na mesma região do Comprehend para evitar latência e custo de tráfego entre regiões.

Consequência para a narrativa de LGPD: o texto recebido **sai do Brasil também na etapa de detecção de PII**, não só na de moderação de conteúdo. A justificativa da ordem fixa (Comprehend antes da OpenAI, ver "Caso 3 — Ambos" em "Lógica de moderação") não é sobre manter o processamento dentro do Brasil — é sobre não enviar PII em texto aberto para um **terceiro fora da relação AWS** (OpenAI) sem necessidade. Isso deve ficar documentado explicitamente no README do projeto, como reconhecimento consciente da limitação em vez de omissão.

### Rede — Lambda sem VPC

A Lambda **não é associada a nenhuma VPC**. Nenhum recurso usado (Comprehend, Parameter Store, chamada HTTPS à OpenAI) exige rede privada — Comprehend e SSM são acessados via endpoint público da AWS (SDK padrão, com TLS), e a OpenAI é um endpoint público na internet.

- **Motivo explícito:** colocar uma Lambda dentro de uma VPC só para "seguir o padrão" é o erro de custo mais comum em projetos iniciantes na AWS — para a função ter saída à internet (necessária para chamar a OpenAI) a partir de dentro da VPC, seria preciso um **NAT Gateway**, que custa **~US$ 32/mês fixos só por existir**, além de custo por GB processado — independente de tráfego. Isso destruiria o objetivo de custo zero fora dos períodos de demonstração.
- Sem VPC, a Lambda usa a rede gerenciada da própria AWS para tudo, sem custo de rede adicional.

## Autenticação

Em vez de manter uma lista própria de chaves de acesso, usar **API Gateway API Keys + Usage Plans**:

- A AWS emite e revoga as chaves.
- Usage Plans permitem aplicar rate limit/throttling por cliente sem código extra.
- Reduz a superfície de lógica de autenticação dentro da Lambda.

## Contrato da API

### Request

```json
{
  "texto": "My name is John Smith and my email is john.smith@example.com.",
  "modo": 1
}
```

- `modo`:
  - `1` → censurar apenas informações pessoais
  - `2` → censurar apenas conteúdo inapropriado
  - `3` → censurar informações pessoais e conteúdo inapropriado

A autenticação não vai no corpo do JSON — é feita pelo header `x-api-key` do API Gateway.

### Response

Exemplo com `modo: 1` (apenas PII, nenhum conteúdo sinalizado) — substituição é feita entidade por entidade, preservando o resto do texto:

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

## Lógica de moderação

### Caso 1 — Informações pessoais

- Enviar o texto ao **Amazon Comprehend** (`DetectPiiEntities`).
- Para cada entidade PII retornada (nome, e-mail, telefone, endereço, SSN, número de cartão, etc.), substituir o trecho correspondente por `***`.

### Caso 2 — Conteúdo inapropriado

- Enviar o texto ao endpoint gratuito **OpenAI Moderation API**.
- A resposta indica categorias sinalizadas (ódio, violência, sexual, etc.) mas não a posição exata no texto.
- **Decisão:** se o texto for sinalizado, censurar o texto todo (`***`). Um segundo passo com prompt a um modelo da OpenAI para apontar os trechos exatos foi descartado por enquanto — dobraria o custo por requisição (2 chamadas à OpenAI), adiciona latência, e abre risco de prompt injection (o texto do usuário viraria input de um prompt que decide o que censurar). Fica como possível evolução futura se a precisão de censura parcial for realmente necessária.

### Caso 3 — Ambos

- **Ordem fixa: Comprehend (Caso 1) sempre primeiro, depois OpenAI Moderation (Caso 2).**
- **O que a OpenAI efetivamente recebe:** a OpenAI Moderation é chamada com o texto **já processado pelo Comprehend** (entidades de PII já substituídas por `***`), nunca com o texto original. É essa substituição prévia — e não apenas a ordem das chamadas — que evita enviar PII em texto aberto para um serviço de terceiro fora da relação de processamento já existente com a AWS (a OpenAI). Chamar as duas APIs na mesma ordem mas ambas com o texto original não teria esse efeito; a mitigação depende de encadear a saída do Comprehend como entrada da OpenAI.
- Relevante para LGPD, já que o texto recebido pode conter dados pessoais de terceiros, não só do próprio usuário da API. **Nota:** como o Comprehend não está disponível em `sa-east-1` (ver seção "Região da AWS"), essa proteção é sobre *quem* recebe a PII (AWS vs. terceiro externo), não sobre manter o dado dentro do Brasil — o texto sai do país em ambas as chamadas.
- **Combinação final:** se a OpenAI sinalizar o texto (já com PII redigida) como impróprio, vale a regra do Caso 2 — a resposta final é `"***"` inteiro, substituindo inclusive as marcações de PII que já haviam sido feitas (ver exemplo em "Contrato da API"). Se a OpenAI não sinalizar nada, a resposta final é o texto com PII redigida pelo Comprehend, sem alteração adicional.

### Tratamento de falhas

- Se Comprehend ou OpenAI Moderation falharem/timeout, a resposta deve ser um erro (5xx) — **nunca** devolver o texto sem a censura correspondente (fail closed). Um serviço de moderação que falha e libera texto sem censurar é pior do que um serviço que fica indisponível.

## Segurança

- Chave da OpenAI armazenada como `SecureString` no Parameter Store, lida pela Lambda via IAM role (não hardcoded). Parameter Store *standard tier* não tem custo por chamada, então buscar o parâmetro na invocação é aceitável — mas vale cachear em memória fora do handler (variável de módulo) para reduzir latência em invocações subsequentes do mesmo container.
- Autenticação de clientes via API Gateway API Keys + Usage Plan.
  - **API Key não é autenticação de verdade.** A própria documentação da AWS recomenda *não* usar API Keys para autenticar/autorizar acesso (sem expiração automática, sem escopo granular, trafegam em header que pode ser logado). Aqui ela é tratada apenas como **identificador de cliente para throttling/quota** — aceitável para MVP/portfólio, com o risco de custo contido pelos controles da seção "Custos" (e não pelo Usage Plan). Se o projeto evoluir para uso real com múltiplos clientes, trocar por Lambda Authorizer (JWT/Cognito) ou IAM.
  - Usar chaves **geradas pelo API Gateway** (não escolher o valor manualmente) e nunca embutir informação sensível no valor da chave.
- **IAM least privilege:** a role de execução da Lambda deve ter permissões restritas ao mínimo necessário — `comprehend:DetectPiiEntities` e `ssm:GetParameter` limitado ao ARN exato do parâmetro da chave da OpenAI (nunca `ssm:GetParameter*` genérico ou `comprehend:*`).
- Validar `modo` (aceitar somente 1, 2 ou 3) e tamanho máximo do texto recebido antes de chamar serviços externos.
  - Fazer essa validação também via **Request Validator do API Gateway** (schema JSON), sem custo adicional — rejeita requisição malformada antes de invocar a Lambda, reduzindo custo e superfície de ataque.
  - **O limite de tamanho deve ser derivado dos limites reais dos serviços downstream e do custo aceitável, não de um número arbitrário:** `DetectPiiEntities` do Comprehend aceita no máximo **100 KB** de UTF-8 por chamada síncrona (conferir na documentação oficial da API antes de fixar o valor), e a OpenAI Moderation também tem um limite de input próprio. Como o limite do Comprehend é folgado, o `maxLength` efetivo deve ser definido principalmente pelo **custo por requisição** (cobrança por blocos de 100 caracteres) e pelo pior caso descrito em "Custos" — um valor pequeno (ex.: 1.000–2.000 caracteres) é suficiente para uma demo. Sempre com margem abaixo do menor limite downstream, para a Lambda nunca invocar um serviço externo (gerando custo) apenas para receber um erro de tamanho.
- **Aplicação das substituições:** ao trocar as entidades do Comprehend por `***`, aplicar as substituições **do fim para o início do texto** (ordenando por `BeginOffset` decrescente), senão os offsets das entidades seguintes se deslocam. Definir explicitamente um `Score` mínimo para aceitar uma entidade (ou decidir conscientemente não filtrar). O Comprehend pode errar (falso negativo/positivo); o README não deve apresentar o serviço como garantia de anonimização.
- **Logs:** nunca logar `texto` ou `texto_censurado` em texto puro no CloudWatch (podem conter PII). Logar apenas metadados: modo, tamanho do texto, latência, status da resposta.
  - Definir **retenção explícita nos log groups** (Lambda e API Gateway) via Terraform — ex.: 14 dias. Por padrão o CloudWatch Logs mantém os logs indefinidamente ("never expire"); num projeto de demonstração isso não tem motivo para existir e é um detalhe que sinaliza atenção a quem revisar o código.
- Manter Parameter Store (não migrar para Secrets Manager) — Secrets Manager cobra por secret armazenado (~$0,40/mês) e por chamada; não traz benefício necessário aqui (a chave da OpenAI não precisa de rotação automática por enquanto), então o Parameter Store `SecureString` é a opção correta tanto por segurança quanto por custo.
  - Usar a **chave gerenciada pela AWS (`alias/aws/ssm`)** para a criptografia do `SecureString`, não uma KMS key própria (customer-managed key) — uma CMK custa ~US$ 1/mês só por existir, e não há necessidade de controle de rotação/política própria sobre a chave neste projeto.

## Custos e proteção contra gastos inesperados

Como este é um projeto de portfólio (sem tráfego real esperado), o foco é evitar qualquer custo fixo desnecessário e conter a exposição a abuso/flood, já que cada requisição custa uma chamada à OpenAI e possivelmente ao Comprehend:

- **Sem NAT Gateway** (ver "Rede — Lambda sem VPC"): é o único item que, se existisse, geraria custo fixo por hora independente de tráfego. Todo o resto da stack (Lambda, API Gateway, Comprehend, Parameter Store) é 100% pay-per-use ou gratuito em repouso.
- **Comprehend sem free tier neste projeto:** o free tier de `DetectPiiEntities` (50.000 unidades/mês) só se aplica nos primeiros 12 meses de vida da conta AWS. Como a conta usada aqui tem mais de um ano, **toda chamada ao Comprehend já é cobrada desde a primeira requisição** — cobrança por blocos de 100 caracteres, com mínimo de 3 unidades (300 caracteres) por chamada. O valor por requisição é da ordem de frações de centavo de dólar, mas deve ser tratado como custo real (não "coberto por free tier") ao dimensionar quota do Usage Plan e o valor do alerta do Budget. Conferir o preço vigente na página oficial de preços do Comprehend antes de fixar os limites de quota.
- **Reserved concurrency** na Lambda: limitar a concorrência máxima (ex.: 2–5 execuções simultâneas) para impor um teto de **paralelismo** mesmo em caso de abuso ou bug em loop. Sem custo adicional. **Atenção:** ela limita paralelismo, *não* o volume total de requisições — sozinha não é um teto de custo (ver "Pior caso de custo" abaixo). Além disso, a AWS só permite reservar até o valor de *Unreserved account concurrency* menos 100; se o limite de concorrência da conta for baixo (contas novas podem ter 10), o `apply` falha. **Conferir em Service Quotas → Lambda → "Concurrent executions" antes do primeiro deploy** e pedir aumento se necessário.
- **AWS Budgets:** configurar um budget com alerta (ex.: em $5 ou $10) para ser avisado por e-mail antes de qualquer gasto relevante. Os dois primeiros budgets são gratuitos. **Budgets só avisa, não interrompe nada** e é avaliado poucas vezes por dia — por isso não substitui o kill switch abaixo.
- **AWS Cost Anomaly Detection** (gratuito): complementa o Budgets. Anomaly Detection reage mais rápido a padrões fora do normal. Baixo esforço, vale ativar junto com o Budget (ativar já no bootstrap manual, antes do primeiro deploy).
- **Usage Plan** com throttle (rate) e quota (requisições/dia ou /mês) definidos explicitamente — não deixar sem limite. **Mas a documentação da AWS é explícita: throttle e quota do Usage Plan são *best-effort*, não limites rígidos, e não devem ser usados como controle de custo.** Tratar como camada de redução de abuso, não como garantia.
- **Kill switch automático (obrigatório):** um **alarme do CloudWatch** sobre `Invocations` da Lambda (ex.: mais de N invocações em 5–15 minutos, com N muito acima do uso esperado de demo) publica em um tópico **SNS**, que aciona uma pequena Lambda que chama `PutFunctionConcurrency` com `0` (throttle total da função). Isso transforma o custo máximo em algo limitado de fato, em vez de depender de um e-mail lido depois. A remoção do bloqueio é manual (reaplicar o Terraform ou remover a reserva). A Lambda do kill switch precisa de permissão apenas para `lambda:PutFunctionConcurrency` no ARN da função principal. Também vale um alarme de e-mail no mesmo tópico SNS.
- **Pior caso de custo (documentar no README):** com a chave de demo exposta no bundle do frontend, qualquer visitante pode extraí-la. Com concorrência 5 e ~300 ms por chamada, o teto teórico é ~16 req/s ≈ 1,4 milhão de chamadas/dia; a US$ 0,0001 por unidade do Comprehend e mínimo de 3 unidades por chamada (**confirmar o preço vigente**), isso passaria de **~US$ 400/dia** sem o kill switch. Por isso: concorrência baixa (2–3), `maxLength` pequeno (cada chamada custa no mínimo 3 unidades independente do tamanho, então texto curto não reduz abaixo disso, mas evita custo maior) e kill switch ativo.
- **AWS WAF foi avaliado e descartado por enquanto:** tem custo fixo mensal (~$5-6 de Web ACL + $1/regra + $0,60 por milhão de requisições) mesmo sem tráfego, o que não se justifica neste estágio. **Essa decisão só se sustenta com o kill switch acima** — Usage Plan + Budget sozinhos não bastam, já que a AWS não os considera limites rígidos. Reavaliar (WAF com rate-based rule) se o projeto sair do portfólio e for para produção com tráfego real.
- **Infraestrutura como código com `destroy` fácil:** como o objetivo é demonstração (não uso contínuo), definir a stack via Terraform permite rodar `terraform destroy` quando o projeto não estiver sendo mostrado a ninguém, garantindo custo zero absoluto fora dos períodos de demonstração, e `terraform apply` para recriar tudo em minutos. Como nada roda localmente (ver "Execução via CI/CD"), tanto `apply` quanto `destroy` são disparados manualmente como workflows do GitHub Actions.

### Estimativa de custo por volume de uso

Estimativa **conservadora** para a stack no ar (região `us-east-1`, valores em US$, mês = 30 dias). Preços conferidos nas páginas oficiais da AWS em 24/09/2026 — **reconferir antes de fixar quotas e alertas**.

**Premissas por requisição:**

| Componente | Preço | Custo por requisição |
|---|---|---|
| Comprehend `DetectPiiEntities` (modos 1 e 3) | US$ 0,0001 por unidade de 100 caracteres, **mínimo 3 unidades** por chamada | texto ≤ 300 caracteres: **US$ 0,0003**; texto de 2.000 caracteres (20 unidades): **US$ 0,0020** |
| API Gateway (REST) | US$ 3,50 por milhão de requisições | US$ 0,0000035 |
| Lambda (256 MB, ~500 ms, x86) | US$ 0,20 por milhão de requisições + duração por GB-s (preço por GB-s não conferido na página, mas o impacto é < US$ 0,000003 por chamada) | ~US$ 0,0000023 |
| CloudWatch Logs (só metadados, ~1 KB por chamada) | US$ 0,50 por GB ingerido | ~US$ 0,0000005 |
| OpenAI Moderation (modos 2 e 3) | gratuita | US$ 0 |

- **Sem free tier:** a conta tem mais de 12 meses, então o free tier de 12 meses do Comprehend e do API Gateway não se aplica. O free tier permanente da Lambda (1 milhão de requisições e 400 mil GB-s por mês) e o de 5 GB do CloudWatch Logs cobririam todos os volumes abaixo, mas foram ignorados por conservadorismo — o custo real da Lambda e dos logs tende a zero.
- **Custo fixo mensal (independe do tráfego): ~US$ 0.** Os 2 alarmes do kill switch cabem nos 10 alarmes gratuitos do CloudWatch; Parameter Store *standard* e a chave `alias/aws/ssm` não cobram; CloudTrail (management events) e os dois primeiros Budgets são gratuitos; S3 do state e do frontend + CloudFront somam centavos por mês.
- **O Comprehend domina o custo** (~98% da requisição nos modos 1 e 3). O modo 2 (só OpenAI Moderation) não chama o Comprehend e custa uma fração disso.

**Custo estimado (modos 1 ou 3, texto ≤ 300 caracteres — cenário típico de demo):**

| Requisições/dia | Custo/dia | Custo/mês |
|---:|---:|---:|
| 0 | US$ 0,00 | US$ 0,00 |
| 1 | US$ 0,0003 | US$ 0,01 |
| 5 | US$ 0,0015 | US$ 0,05 |
| 10 | US$ 0,0031 | US$ 0,09 |
| 100 | US$ 0,031 | US$ 0,92 |
| 500 | US$ 0,15 | US$ 4,59 |
| 1.000 | US$ 0,31 | US$ 9,19 |

**Comparativo por cenário de uso (custo/mês):**

| Requisições/dia | Modo 2 (só OpenAI, sem Comprehend) | Modos 1/3, texto ≤ 300 car. | Modos 1/3, texto de 2.000 car. (pior caso legítimo) |
|---:|---:|---:|---:|
| 0 | US$ 0,00 | US$ 0,00 | US$ 0,00 |
| 1 | US$ 0,0002 | US$ 0,01 | US$ 0,06 |
| 5 | US$ 0,0009 | US$ 0,05 | US$ 0,30 |
| 10 | US$ 0,002 | US$ 0,09 | US$ 0,60 |
| 100 | US$ 0,02 | US$ 0,92 | US$ 6,02 |
| 500 | US$ 0,09 | US$ 4,59 | US$ 30,09 |
| 1.000 | US$ 0,17 | US$ 9,19 | US$ 60,19 |

**Como usar esta tabela:**
- Uso legítimo de demonstração (dezenas de chamadas por dia) custa **centavos por mês**. O risco financeiro do projeto não está no uso normal, e sim no abuso — o cenário de ~US$ 400/dia descrito em "Pior caso de custo" está muito acima de qualquer linha desta tabela, e é por isso que o kill switch é obrigatório.
- **Calibrar o Budget:** com texto curto, um Budget de US$ 5/mês dispara por volta de 500 requisições/dia sustentadas; um de US$ 10, por volta de 1.000/dia. Como esses volumes já seriam muito acima do esperado para uma demo, qualquer alerta neles indica uso anormal.
- **Calibrar o kill switch:** o limite `N` do alarme de invocações deve ficar bem acima da última linha desta tabela que você considera uso legítimo (ex.: se a demo nunca deve passar de ~100 chamadas/dia, `N` na ordem de algumas centenas por hora já é anormal).
- Se o projeto sair do portfólio com tráfego real, refazer a conta: acima de ~milhares de requisições/dia o Comprehend continua dominando, e o free tier de 50 mil unidades/mês do Comprehend (válido só nos primeiros 12 meses de uma conta) não estará disponível.

## Execução via CI/CD (GitHub Actions) — tudo roda na nuvem

O objetivo é que nada do sistema — aplicação ou infraestrutura — dependa de rodar/manter algo na máquina local; tudo vive na AWS e o deploy do dia a dia (`plan`/`apply`/`destroy`) é automatizado via workflow do **GitHub Actions**, o que elimina o problema clássico de credenciais da AWS armazenadas/vazadas em uma máquina de desenvolvedor.

**Exceção pontual — bootstrap inicial:** antes de o primeiro workflow existir, alguém precisa criar manualmente o IAM OIDC Identity Provider, a IAM Role de deploy e o bucket S3 do state (ver passo 3 de "Próximos passos") — é um problema do tipo "ovo e galinha", já que o CI ainda não tem como se autenticar sozinho. Esse passo único é feito **via Console da AWS** (não via AWS CLI local com credenciais salvas), para não contradizer o objetivo de não manter credenciais de longa duração numa máquina local. Depois desse bootstrap, nenhum comando de deploy roda fora do GitHub Actions.

- **Autenticação via OIDC, sem access keys estáticas:** configurar um **IAM OIDC Identity Provider** para `token.actions.githubusercontent.com` e uma **IAM Role** cuja trust policy restringe o assume-role ao repositório (e, idealmente, à branch `main` para `apply`/`destroy`) via `sub` no token do GitHub Actions. O workflow assume essa role via `aws-actions/configure-aws-credentials` com `id-token: write`. Não existe nenhum `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` salvo em secret do GitHub — remove o risco de uma chave de longa duração vazar do repositório ou dos logs do workflow.
- **Permissões da role de deploy:** essa role é diferente e mais ampla que a role de execução da Lambda (precisa criar/alterar Lambda, API Gateway, IAM roles, SSM parameters, S3, CloudFront, log groups) — mesmo assim, escopar por serviço/prefixo de recurso sempre que possível, evitando `*:*`.
  - **Risco de escalonamento de privilégio:** uma role que pode criar/alterar roles IAM pode conceder a si mesma (ou a terceiros) permissões maiores. Mitigar com uma **permissions boundary** aplicada às roles criadas pelo Terraform (limitando o teto de permissões que elas podem ter) e restringindo `iam:CreateRole`/`iam:PutRolePolicy`/`iam:AttachRolePolicy` a um prefixo de nome (ex.: `guardrails-*`).
  - **Trust policy do OIDC:** o `sub` do token deve ser específico — para `apply`/`destroy`, usar `repo:<owner>/<repo>:environment:<nome-do-environment>` (amarrado ao GitHub Environment com required reviewer) ou `repo:<owner>/<repo>:ref:refs/heads/main`; nunca um wildcard como `repo:<owner>/<repo>:*`. A role de `plan` (somente leitura) pode aceitar `pull_request`, mas sem nenhuma permissão de escrita. Como o repositório é público, lembrar que PRs de forks não recebem `id-token: write` por padrão — manter assim.
- **State do Terraform remoto (obrigatório, já que não há máquina local persistente):** backend em **S3** com **versionamento habilitado** no bucket (permite reverter um state corrompido) e **locking nativo do S3** (Terraform ≥ 1.10, `use_lockfile = true`) para evitar concorrência entre execuções — dispensa criar uma tabela DynamoDB só para lock. O bucket de state guarda dados sensíveis: **o valor da chave da OpenAI (`SecureString`) aparece em texto puro no `terraform.tfstate`** — marcar a variável como `sensitive` só a esconde dos logs, não do state. Por isso o bucket **deve** (não "se possível") ter: acesso público bloqueado (Block Public Access nas 4 opções), criptografia padrão SSE-S3/KMS, versionamento, e uma bucket policy que restrinja o acesso apenas às roles de deploy/plan. Custo: armazenamento de um arquivo de poucos KB, irrelevante.
- **Fluxo dos workflows:**
  - Pull request → `terraform plan` (somente leitura/diff, comentado no PR) — usa uma role com permissões só de leitura/plan.
  - Merge na `main` → `terraform apply`, disparado automaticamente ou com aprovação manual via [GitHub Environments](https://docs.github.com/actions/deployment/targeting-different-environments/using-environments-for-deployment) com *required reviewer* — importante para nunca aplicar uma mudança de IAM/custo sem revisão consciente.
  - `terraform destroy` como workflow separado, disparado manualmente (`workflow_dispatch`), usado para zerar custo entre demonstrações.
- **Segredo da OpenAI:** o valor da API key da OpenAI é passado ao Terraform como uma **GitHub Actions Secret** (`OPENAI_API_KEY`), nunca commitado em `.tfvars`, e gravado no Parameter Store como `SecureString` pelo próprio `apply` — o valor em si nunca aparece em log do workflow (Terraform sensibiliza variáveis marcadas como `sensitive`).

## Boas práticas de repositório (projeto open source)

Como o código vai para um repositório público, alguns cuidados evitam vazamento de segredo e ruído no histórico do Git:

- **`.gitignore`** cobrindo `*.tfstate`, `*.tfstate.backup`, `.terraform/`, `*.tfvars` (exceto um `terraform.tfvars.example` com valores fictícios, versionado como referência).
- **Nenhum Account ID, ARN real ou valor de segredo hardcoded** nos arquivos `.tf` versionados — usar variáveis (`var.aws_account_id`, data sources como `aws_caller_identity`, etc.).
- Já que o state agora é remoto em S3 (ver "Execução via CI/CD"), reforçar que **o bucket de state nunca deve ser público** e não faz parte do frontend de demo.
- README deve deixar claro que o repositório é a definição da infraestrutura, não um ambiente permanentemente no ar — reforça a narrativa de "destroy fora de demonstração" já documentada em "Custos".

## Acesso de demonstração (portfólio)

Como este projeto usa `x-api-key`, um visitante do GitHub (recrutador, avaliador) não tem uma chave por padrão. **Oferecer os dois métodos de acesso, que não são excludentes**, ambos usando a **mesma key de demo e a mesma Usage Plan "demo"**:

1. **Frontend estático de demo (caminho principal)** (S3 + CloudFront, dentro do free tier) chamando a API diretamente do navegador. A key fica exposta no JS do bundle, então precisa de uma **Usage Plan separada, "demo", com quota agressiva** (ex.: poucas dezenas de requisições/dia) — nunca a mesma quota/chave usada para testes próprios.
   - O bucket S3 deve ser **privado**, servido pelo CloudFront via **Origin Access Control (OAC)** — nunca um bucket com acesso público direto. Sem custo adicional, evita que alguém contorne o CloudFront (e qualquer limite/cache configurado nele) acessando o S3 diretamente.
2. **`curl`/Postman documentado no README (caminho secundário):** exemplo de chamada para cada um dos três modos, usando a mesma chave de demo publicada no próprio README. Atende quem quer ver o contrato da API sem passar pelo frontend.

**Por que a mesma key/Usage Plan nos dois:**
- A key de demo já é pública por design (está no bundle), então publicá-la no README não amplia a exposição.
- Uma segunda key com quota própria dobraria o teto de requisições da demo sem necessidade. Com uma só, a quota vale para o conjunto dos dois caminhos.
- Rotacionar em caso de abuso é trocar um único valor (Terraform + bundle + README).

**Limites a ter claros:**
- O `curl` chama a API direto, sem passar pelo CloudFront/OAC. Não é um risco novo — a API já é chamável direto com a key do bundle. O CloudFront/OAC protege o bucket S3, não a API.
- O controle de custo real continua sendo o reserved concurrency baixo e, principalmente, o kill switch automático; a quota da Usage Plan ajuda, mas é best-effort (ver "Custos"). Por isso o kill switch precisa estar testado antes de publicar qualquer um dos dois acessos.
- `terraform destroy` derruba os dois métodos juntos; o README deve avisar que a demo pode estar desligada fora dos períodos de demonstração.

## Ameaças e mitigações

Resumo das principais ameaças consideradas e como o design responde a cada uma — útil tanto como checklist de segurança quanto para documentar no README a maturidade do projeto:

| Ameaça | Mitigação |
|---|---|
| Vazamento/uso indevido da API key (a key de demo é pública por design, no bundle do frontend e no README) | Usage Plan com throttle + quota por chave (best-effort, não é limite rígido); key de demo separada da key "real", com quota bem menor; custo contido pelo kill switch, não pela key |
| Abuso/flood gerando custo inesperado | Reserved concurrency baixa (teto de paralelismo) + **kill switch automático** (alarme CloudWatch → SNS → Lambda que zera a concorrência) + Usage Plan (best-effort) + AWS Budgets (só avisa) + Cost Anomaly Detection |
| Escalonamento de privilégio via role de deploy (que cria roles IAM) | Permissions boundary nas roles criadas + `iam:*` restrito a prefixo de nome + trust policy do OIDC com `sub` específico (Environment/branch `main`) |
| Chave da OpenAI legível no `.tfstate` (texto puro) | Bucket de state privado, criptografado, versionado, com bucket policy restrita às roles de deploy/plan |
| Vazamento da chave da OpenAI | Armazenada como `SecureString` no Parameter Store, nunca hardcoded; acesso via IAM role restrito ao ARN exato do parâmetro |
| Escalonamento de privilégios via IAM da Lambda | Least privilege: permissões limitadas a `comprehend:DetectPiiEntities` e `ssm:GetParameter` no ARN exato, nunca wildcard |
| Vazamento de PII via logs | Proibido logar `texto`/`texto_censurado`; apenas metadados (modo, tamanho, latência, status) |
| Envio de PII em texto aberto para terceiro externo (OpenAI) | Ordem fixa Comprehend → OpenAI Moderation, com a OpenAI recebendo o texto já com PII substituída por `***` (ver "Caso 3 — Ambos" e "Região da AWS") |
| Prompt injection via texto do usuário | Descartada a abordagem de usar um modelo generativo da OpenAI para apontar trechos exatos a censurar — justamente para não transformar input do usuário em prompt de decisão |
| Falha de um serviço de moderação liberando conteúdo não censurado | Fail-closed: qualquer erro/timeout do Comprehend ou da OpenAI retorna 5xx, nunca o texto sem a censura correspondente |
| Requisição malformada consumindo invocação da Lambda | Validação de `modo` e tamanho do texto via Request Validator do API Gateway, antes de invocar a Lambda |
| Custo fixo por hora sem tráfego (ex.: NAT Gateway) | Lambda sem VPC — nenhum recurso da stack tem custo fixo por hora (ver "Rede — Lambda sem VPC") |
| Credenciais AWS de longa duração vazadas (repositório, máquina local, logs de CI) | Deploy do dia a dia via GitHub Actions com **OIDC** (sem access keys estáticas), role restrita ao repositório; único passo manual (bootstrap do OIDC/role/bucket) feito via Console AWS, não por CLI local com credenciais salvas (ver "Execução via CI/CD") |
| Segredo/dado sensível vazado via `.tfstate` ou `.tfvars` commitado no repositório público | `.gitignore` cobrindo state e `.tfvars`; state remoto em bucket S3 privado, nunca versionado no Git (ver "Boas práticas de repositório" e "Execução via CI/CD") |
| Acesso ao frontend de demo (S3) contornando o CloudFront/CORS | Bucket S3 privado + Origin Access Control (OAC), sem acesso público direto ao bucket (ver "Acesso de demonstração") |

## Próximos passos

1. Escolher a região da AWS com base na disponibilidade do Comprehend (`sa-east-1` está descartada — ver "Região da AWS") e documentar a decisão no README.
2. Criar o repositório com `.gitignore` (state/tfvars — ver "Boas práticas de repositório") e o esqueleto do Terraform, **sem VPC** para a Lambda (ver "Rede").
3. Criar manualmente **via Console da AWS** (uma única vez, fora do fluxo normal — ver "Execução via CI/CD") o **IAM OIDC Identity Provider** do GitHub Actions e a **IAM Role de deploy** com trust policy restrita ao repositório, e um **bucket S3 privado** para o state remoto do Terraform (versionado, sem acesso público) — pré-requisitos de bootstrap que precisam existir antes do primeiro `apply` via workflow.
4. Configurar os workflows do GitHub Actions (`plan` em PR, `apply` protegido por GitHub Environment em merge na `main`, `destroy` manual via `workflow_dispatch`) usando a role OIDC do passo 3.
   - Antes do `apply`: conferir em Service Quotas o limite de concorrência da conta (a reserved concurrency exige margem — ver "Custos") e confirmar o preço vigente do Comprehend.
5. Definir o restante da infraestrutura como código em **Terraform**: Lambda (permissões IAM restritas para Comprehend e Parameter Store, reserved concurrency baixa, log group com retenção definida), API Gateway (REST API, Request Validator com `maxLength` derivado de custo e dos limites downstream — ver "Segurança", API Keys geradas pelo API Gateway, Usage Plans incluindo a "demo" separada, CORS habilitado — necessário porque o frontend de demo escolhido em "Acesso de demonstração" chama a API direto do navegador).
   - Para o access log do API Gateway funcionar, configurar `aws_api_gateway_account` com uma role do CloudWatch Logs (configuração por conta/região, fácil de esquecer).
6. Configurar AWS Budgets e Cost Anomaly Detection com alerta de custo (considerando que o Comprehend já é cobrado desde a primeira chamada nesta conta — ver "Custos") e o **kill switch automático** (alarme CloudWatch → SNS → Lambda que zera a reserved concurrency — ver "Custos"). Testar o kill switch de fato (simulando o alarme) antes de publicar o frontend de demo.
7. Implementar detecção de PII (Comprehend).
8. Implementar detecção de conteúdo inapropriado (OpenAI Moderation), com a `OPENAI_API_KEY` chegando ao Terraform via GitHub Actions Secret.
9. Implementar a substituição por `***` e montar a resposta, com fail-closed em caso de erro.
10. Testar os três modos com casos reais (via workflow/ambiente de demo, já que não há execução local).
11. Configurar logs (sem PII) e alarmes básicos de monitoramento (CloudWatch: erros, throttles, duração); habilitar CloudTrail (management events, gratuito) para auditoria básica da conta.
12. Implementar o acesso de demonstração já decidido: frontend estático com S3+CloudFront+OAC (caminho principal), com a mesma key/Usage Plan "demo" também usada pelos exemplos de `curl` do README (ver "Acesso de demonstração").
13. Escrever o README com arquitetura, decisões de segurança/custo, a seção "Ameaças e mitigações", exemplos de `curl` para os três modos com a key de demo e o aviso de que a demo pode estar desligada (`destroy`), já pensando na apresentação como projeto de portfólio.
