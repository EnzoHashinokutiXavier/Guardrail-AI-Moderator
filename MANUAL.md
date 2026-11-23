# Manual: building your own text moderator

This manual walks through the decisions behind Guardrail AI Moderator in the order you would make them when building a text moderator of your own. For each decision it gives the choice made here, why it was made, its main advantage and its main drawback, followed by alternatives that may fit your case better.

It is written for developers who want to add moderation to their own program: a chat, a forum, a form, a support tool, or a guardrail in front of an LLM. You do not need AWS or this repository's infrastructure to reuse the ideas.

- [Part 1](#part-1--what-the-moderator-does) to [Part 4](#part-4--privacy) are **reusable concepts**: they apply to any moderator, in any language or platform.
- [Part 5](#part-5--running-it-specific-to-this-project) and [Part 6](#part-6--delivery-specific-to-this-project) are **choices specific to this project**: a serverless API on AWS, deployed by GitHub Actions, that is online only during demos. Read them as one worked example, not as requirements.
- [Part 7](#part-7--testing-and-publishing) covers testing and the license, and [Part 8](#part-8--adapting-it-to-your-program) shows how to plug the logic into another program and swap the services.

The [README](README.md) describes what the API does. This manual explains why it does it that way.

## The moderation flow

Every moderator in this family has the same shape, whatever services it uses:

```text
input ──► validate ──► detect PII ──► redact spans ──► check content ──► combine ──► output
             │              │                               │
             └─ reject      └───────── any failure ─────────┴──► error, no text (fail closed)
```

1. **Validate** the input before calling anything that costs money or sends data out.
2. **Detect personal data** (PII) and get the position of each piece.
3. **Redact** those positions, replacing them with a marker such as `***`.
4. **Check the content** for inappropriate material, using the already redacted text.
5. **Combine** the two results into one output.
6. If any step fails, return an error and **never** the text without the censorship it asked for.

In this project the flow lives in one function, `moderate(text, mode)` in [src/moderador/app.py](src/moderador/app.py):

```python
def moderate(text, mode):
    if mode in (1, 3):
        # Case 1: PII replaced with ***.
        text = redact(text, _detect_pii(text))

    if mode in (2, 3) and is_flagged(_query_openai(text)):
        # Case 2: a flagged text becomes *** as a whole.
        text = CENSOR

    return text
```

## Decision map

| # | Decision | Choice in this project | Reusable? |
|---|---|---|---|
| 1 | [Output](#1-what-the-moderator-returns) | Return the text with `***` in place of what was censored | Yes |
| 2 | [Language](#2-language-scope) | English only | Yes |
| 3 | [Modes](#3-what-to-moderate-in-each-request) | Mode per request: PII, content or both | Yes |
| 4 | [PII detector](#4-pii-detector) | Amazon Comprehend `DetectPiiEntities` | Yes, the service is replaceable |
| 5 | [PII types and threshold](#5-which-pii-types-and-which-confidence) | Closed list, score ≥ 0.5 | Yes |
| 6 | [Content detector](#6-content-moderation-detector) | OpenAI Moderation (`omni-moderation-latest`) | Yes, the service is replaceable |
| 7 | [Flagged content](#7-what-to-censor-when-the-content-is-flagged) | The whole text becomes `***` | Yes |
| 8 | [Order](#8-order-of-the-two-checks) | PII first, then content on the redacted text | Yes |
| 9 | [Validation](#9-input-validation-and-size-limit) | Strict types, 1 to 1,000 characters | Yes |
| 10 | [Redaction](#10-redaction-algorithm) | Merge spans, replace from the end | Yes |
| 11 | [Failures](#11-failure-handling) | Fail closed, short timeouts, one retry | Yes |
| 12 | [Logs](#12-logging) | Metadata only | Yes |
| 13 | [Data location](#13-where-the-data-goes) | `us-east-1`, AI opt-out, retention documented | Yes, the analysis |
| 14 | [Hosting](#14-how-the-moderator-is-exposed) | API Gateway REST + Lambda | No |
| 15 | [Network](#15-network-lambda-without-a-vpc) | Lambda without VPC | No |
| 16 | [Access](#16-client-access) | API key + usage plan | No |
| 17 | [Secrets](#17-storing-the-third-party-key) | SSM Parameter Store `SecureString`, write-only in Terraform | No |
| 18 | [Dependencies](#18-third-party-packages) | None: standard library and the runtime's boto3 | Partly |
| 19 | [Cost](#19-cost-containment) | Kill switch at more than 10 invocations a minute | Partly |
| 20 | [Lifecycle](#20-online-only-during-demos-with-infrastructure-as-code) | Online only during demos, Terraform | No |
| 21 | [CI/CD](#21-deployment-pipeline) | GitHub Actions with OIDC, manual `apply`/`destroy` | No |
| 22 | [State](#22-terraform-state) | S3 with versioning and native lock | No |
| 23 | [Deploy role](#23-deploy-role-permissions) | Scoped by the `guardrails-*` prefix | No |
| 24 | [Unit tests](#24-unit-tests) | Pure functions, stubs, no network | Yes |
| 25 | [Real test](#25-test-on-the-real-services) | Measured on AWS before calling it done | Yes |
| 26 | [License](#26-license) | PolyForm Noncommercial 1.0.0 | Your call |

---

## Part 1 — What the moderator does

### 1. What the moderator returns

**Choice:** return the text itself, with each censored part replaced by `***`.

- **Why:** the caller wants a text it can show or store right away, not a report it has to interpret.
- **Advantage:** simple to use; the rest of the text is preserved.
- **Drawback:** the caller loses information: it cannot tell a name from an e-mail, or why a text was censored.

**Alternatives:**

- **Reject or accept** (a yes/no answer): best for forms and posts, where the user can rewrite the text.
- **Labels and scores** (what was found, where, with what confidence): best when your program decides what to do, or sends borderline cases to a human.
- **Type tags** such as `[NAME]` and `[EMAIL]`: keeps the text readable and useful for analytics or for an LLM, at the cost of revealing what kind of data was there.
- **Pseudonymization** (consistent fake values, such as the same fake name for the same real name): keeps the text coherent, but needs a mapping that becomes sensitive data itself.
- **A review queue**: a human decides. Slow and expensive, but the most accurate.

### 2. Language scope

**Choice:** English only.

- **Why:** Comprehend's `DetectPiiEntities` supports English and Spanish, not Portuguese. Using a ready-made service was more important than supporting Portuguese.
- **Advantage:** no model to train or maintain, and the built-in entity types (name, e-mail, phone, address, SSN, cards...) fit English text well.
- **Drawback:** a text in another language is not rejected; its personal data is just not reliably detected. Country-specific identifiers, such as the Brazilian CPF, are not covered.

**Alternatives:**

- A PII service that supports your language. Check each provider's language list before choosing (see [decision 4](#4-pii-detector)).
- Regular expressions for identifiers with a fixed format (CPF, CNPJ, phone numbers, postal codes), combined with a model for names and addresses.
- Detecting the language first (Comprehend has `DetectDominantLanguage`) and rejecting what you cannot moderate, instead of passing it through.

**Tip:** whatever you choose, decide explicitly what happens to unsupported input. Silent pass-through is the worst default for a guardrail.

### 3. What to moderate in each request

**Choice:** the request carries a `mode`: `1` personal data only, `2` inappropriate content only, `3` both.

- **Why:** it shows each check working alone and together, and lets the caller pay only for what it needs (mode 2 does not call Comprehend).
- **Advantage:** flexible for the caller; easy to demonstrate.
- **Drawback:** the caller can choose a weaker mode. Mode 2 sends the original text, personal data included, to OpenAI, and each mode does only its own job: mode 1 lets a threat through, mode 2 lets personal data through.

**Alternatives:**

- **A fixed policy on the server**: every request goes through every check. Safer, since nobody can opt out, and the right default when the moderator protects users rather than serves developers.
- **Independent flags** (`"pii": true, "content": true`): clearer than numeric modes and easier to extend with new checks.
- **A policy per client**, set on the server and tied to the API key or account.

---

## Part 2 — Detection

### 4. PII detector

**Choice:** Amazon Comprehend `DetectPiiEntities`.

- **Why:** a managed service in the same cloud as the rest of the stack, with no model to host, called through the SDK that already ships with Lambda.
- **Advantage:** returns the type, confidence score and exact position (`BeginOffset`, `EndOffset`) of each entity, which is what redaction needs.
- **Drawback:** English and Spanish only, not available in São Paulo (`sa-east-1`), charged per 100 characters with a minimum of 3 units per call, and, like any model, it misses some data and flags some ordinary words.

**Alternatives:**

- **Microsoft Presidio** (open source, self-hosted): regex plus NLP recognizers, custom recognizers for local identifiers, and built-in anonymizers (replace, mask, hash, encrypt). The data never leaves your servers, but you host and tune it.
- **Google Cloud Sensitive Data Protection** (formerly DLP): a large catalog of identifier types, including country-specific ones such as `BRAZIL_CPF_NUMBER`.
- **Azure AI Language PII detection**: managed, with wider language support than Comprehend.
- **Amazon Bedrock Guardrails**: sensitive information filters (mask or block PII, plus your own regex) and content filters in the same service. One call can replace both checks of this project; compare its price and language support.
- **Regex only**: free and predictable for formatted identifiers, useless for names and free-form addresses.
- **An LLM with a prompt**: flexible, but slower, more expensive, non-deterministic and open to prompt injection (see [decision 7](#7-what-to-censor-when-the-content-is-flagged)).

**What to look for in any PII service:** the position of each entity (not only "PII found"), a confidence score, your language, your region, and the provider's data retention policy.

### 5. Which PII types and which confidence

**Choice:** censor only a closed list of 15 entity types (`NAME`, `EMAIL`, `PHONE`, `ADDRESS`, `SSN`, card and bank data, passport, driver's license, IP address, password), and only with a score of at least 0.5.

- **Why:** a closed list makes the behavior predictable and testable. The low threshold follows the guardrail rule: censoring too much is better than letting personal data through. CVV, expiry, PIN and routing number are on the list because leaving them out would undo the censoring of the card or account number.
- **Advantage:** the result does not change when the provider adds a new entity type; every type on the list was chosen on purpose.
- **Drawback:** types left out on purpose (`DATE_TIME`, `AGE`, `URL`) come back as they are, and a low threshold produces more false positives.

**Alternatives:**

- **All types the service returns**: maximum coverage, but the output changes when the provider changes.
- **A threshold per type**: for example, stricter for `NAME` (frequent false positives) and looser for `SSN`.
- **A higher threshold** (0.8 or 0.9): fewer false positives, more personal data let through. Fits analytics, not guardrails.

### 6. Content moderation detector

**Choice:** the OpenAI Moderation API with the `omni-moderation-latest` model.

- **Why:** free, a single HTTPS call, and it covers the usual categories (hate, harassment, violence, sexual content, self-harm...).
- **Advantage:** no cost per request and a simple answer: a `flagged` boolean plus a score per category.
- **Drawback:** a third party outside AWS that keeps API data for up to 30 days for abuse monitoring (unless the law requires longer); it says *whether* the text is a problem, not *where*.

**Hardening the third-party key:** the key used here belongs to a dedicated OpenAI project, is restricted to model capabilities, the project allows only the moderation model, and the project has a monthly budget of US$ 1 with auto recharge off. A leaked key can moderate text and nothing else.

**Alternatives:**

- **Azure AI Content Safety**: managed, with severity levels per category and wider language support.
- **Amazon Comprehend toxicity detection** (`DetectToxicContent`) or **Amazon Bedrock Guardrails content filters**: keep the text inside AWS. Check language support and price.
- **Llama Guard** or another open-weight safety classifier, self-hosted: the data stays with you, but you pay for the GPU and the maintenance.
- **Keyword and pattern lists**: cheap and transparent, easy to evade, and they miss context.

### 7. What to censor when the content is flagged

**Choice:** if the Moderation API flags the text, the whole text becomes `***`.

- **Why:** the API does not return the position of the problem, so there is no safe way to censor only part of it.
- **Advantage:** nothing inappropriate gets through, and no extra call is needed.
- **Drawback:** a long text with one bad sentence loses everything, including what had already been redacted.

**Alternatives:**

- **A second step with a generative model** to point out the exact sentences. Rejected here: it doubles the calls, adds latency and opens the door to **prompt injection**, because the user's text becomes part of a prompt that decides what to censor. Today no generative model decides anything in this project.
- **Sentence-level checks**: split the text into sentences and moderate each one. Partial censoring without an LLM, at the cost of one call per sentence and the loss of context between them.
- **A policy by category and score**: use `category_scores` instead of the `flagged` boolean, with your own thresholds. For example, block violence at a low score and allow mild profanity.

### 8. Order of the two checks

**Choice:** PII detection always runs first, and the content check receives the text **already redacted**.

- **Why:** this way the third party outside AWS (OpenAI) does not receive, in clear text, the personal data that Comprehend detected. It is the chaining, not only the order, that protects the data: the variable `text` is overwritten by the redacted version before the second call.
- **Advantage:** less personal data sent to a third party.
- **Drawback:** the calls are sequential, so the latencies add up; whatever Comprehend misses still reaches OpenAI; and the `***` markers may change the moderation result in rare cases.

**Alternatives:**

- **Both calls in parallel on the original text**: lower latency, but the third party receives the personal data.
- **Content first**: if the text is going to be censored entirely anyway, the PII call can be skipped to save money. The third party then sees the original text.
- **One service that does both** (for example, Bedrock Guardrails): one call, one provider, one data policy.

---

## Part 3 — Processing logic

### 9. Input validation and size limit

**Choice:** reject the request with `400` before any external call unless `text` is a string of 1 to 1,000 characters and `mode` is exactly the integer 1, 2 or 3. The validation runs at the edge (API Gateway's JSON schema) and again in the code.

- **Why:** every external call costs money or sends data out, so invalid input must stop before it. The 1,000-character limit comes from cost: at most 10 Comprehend units per call. Empty text is rejected because Comprehend does not accept it, and its error would become a `502`.
- **Advantage:** predictable cost per request and fewer surprises downstream.
- **Drawback:** long texts must be split by the caller, and the two validations must be kept in sync.

Two Python traps worth copying the fix for:

```python
# True == 1 in Python, so `mode in (1, 2, 3)` alone accepts true.
if type(mode) is not int or mode not in MODES:
    return None

# A lone surrogate ("\ud800") is a valid str but not valid UTF-8:
# reject it here instead of failing later at the provider.
try:
    text.encode("utf-8")
except UnicodeEncodeError:
    return None
```

Also check the `Content-Type` yourself. In this project, API Gateway applies the schema only to bodies sent as `application/json`; a `text/plain` body skips the schema and reaches the code, which rejects it.

**Alternatives:**

- **A validation library** (JSON Schema, Pydantic): less code, one more dependency.
- **Chunking** long texts into pieces and moderating each one: supports large inputs, multiplies cost and needs care with entities split between chunks.

### 10. Redaction algorithm

**Choice:** filter the entities, sort them by start position, merge the spans that overlap or touch, and replace them from the **end** of the text to the beginning.

- **Why:** without merging, overlapping entities corrupt the text and adjacent ones produce `******`. Replacing from the start would shift the positions of every entity after the first one.
- **Advantage:** pure logic with no I/O, the same for any detector that returns positions, and easy to test.
- **Drawback:** two entities separated by a single space stay as two `***`, which still tells the reader that there were two pieces of data.

```python
def _pii_spans(entities):
    """Filter the entities and merge overlapping or adjacent spans."""
    spans = sorted(
        (e["BeginOffset"], e["EndOffset"])
        for e in entities
        if e["Type"] in PII_ENTITIES and e["Score"] >= MIN_SCORE
    )
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:            # touches or overlaps
            merged[-1][1] = max(merged[-1][1], end)      # also covers nested spans
        else:
            merged.append([start, end])
    return merged


def redact(text, entities):
    """Replace each PII span with ***, from the end of the text to the start."""
    for start, end in reversed(_pii_spans(entities)):
        text = text[:start] + CENSOR + text[end:]
    return text
```

**Check the unit of the offsets.** Providers may count positions in Unicode code points, UTF-16 code units or UTF-8 bytes. Comprehend counts code points, which match Python's `str` indices, and its `EndOffset` is exclusive. This was measured, not assumed: `"Café ☕ — my name is John Smith"` and a text with an emoji outside the BMP were redacted at the exact position. With another provider or another language (JavaScript and Java strings use UTF-16), test accents and emoji **before** the PII and convert the offsets if needed.

### 11. Failure handling

**Choice:** **fail closed**. If Comprehend, Parameter Store or OpenAI fails or times out, the answer is `502` with a generic message and no text.

- **Why:** a moderator that fails and lets uncensored text through is worse than one that is unavailable.
- **Advantage:** no failure mode returns text that skipped a check, not even the already redacted text in mode 3.
- **Drawback:** availability depends on every provider; when one is down, the whole moderator is down.

How it is implemented:

```python
# A closed list of moderation failures. Anything else is a code defect:
# it is not caught, and the platform turns it into a 5xx (still fail closed).
FAILURES = (BotoCoreError, ClientError, OSError, http.client.HTTPException,
            ValueError, LookupError, TypeError)

try:
    censored = moderate(text, mode)
except FAILURES as error:
    _log(start, 502, mode, length, error)
    return _error(502, MSG_UNAVAILABLE)

# Only reached when every step of the mode succeeded.
return _response(200, {"censored_text": censored})
```

- **Short timeouts and one retry:** 1 s to connect and 3 s to read on AWS calls, 3 s on the OpenAI call, 2 attempts in total. OpenAI is retried only on timeouts, connection errors and `500/502/503/504`; a `4xx` (bad key, rate limit) fails at once. The Lambda timeout (15 s) is the last ceiling.
- **Read the response defensively:** only the exact value `false` releases the text. A missing field, `null` or `"false"` counts as flagged:

  ```python
  def is_flagged(response):
      return response["results"][0]["flagged"] is not False
  ```

- **Library defaults can surprise you:** in boto3's `Config`, `max_attempts` counts only the retries, so `max_attempts: 2` means 3 attempts. This project uses `total_max_attempts: 2`, which counts the first call.

**Alternatives:**

- **Fail open** (return the text when moderation is down): acceptable only when moderation is advisory, for example a warning label in an internal tool.
- **A fallback provider**: try a second service when the first fails. More availability, two contracts and two behaviors to test.
- **Queue and retry later**: for asynchronous flows (comments published after review), the text waits instead of failing.

### 12. Logging

**Choice:** one JSON line per request with metadata only: `status`, `mode`, `length`, `ms`, and on failures the error class and code. Never the text, never the censored text, never the error message. Logs are kept for 14 days.

- **Why:** the input may contain personal data, and an error message may quote the request.
- **Advantage:** logs can be read, kept and shared without becoming a store of personal data.
- **Drawback:** debugging a wrong censoring requires reproducing it; the logs cannot show what was sent.

**Alternatives:**

- **A hash of the text**: detects repeated inputs without storing them. Short texts can still be guessed by brute force.
- **Encrypted samples with restricted access**: helps tune the detectors, but becomes personal data with its own retention rules.
- **No logs at all**: maximum privacy, no visibility.

---

## Part 4 — Privacy

### 13. Where the data goes

**Choice:** run in `us-east-1`, apply an AWS Organizations AI services opt-out policy, and document the retention of each provider.

- **Why:** Comprehend is not available in São Paulo, so the text leaves Brazil at the first step. Under the LGPD this is an international data transfer, which is why the project is a demo meant for fictitious data. By default, AWS may store and use content processed by its AI services to improve them; the opt-out policy (attached to the root with `@@none`, so it cannot be overridden) stops that.
- **Advantage:** the limitation is stated, not hidden, and the data each provider keeps is known.
- **Drawback:** the project has not gone through an LGPD review and should not process real personal data as it is.

**Questions to answer for your own moderator:**

- In which country does each service process the text?
- How long does each provider keep it, and does it train on it? Is zero data retention available, and under what conditions?
- Does your law require consent, a contract with the provider or a specific legal basis?

**Alternatives:**

- **A provider with a region in your country**, if it offers the services you need there.
- **Self-hosted detectors** (Presidio for PII, Llama Guard for content): the text never leaves your servers.
- **Zero data retention** agreements with the provider, where available.

---

## Part 5 — Running it (specific to this project)

From here on, the decisions are about how this particular project runs. If your moderator is a function inside your own program, most of them do not apply.

### 14. How the moderator is exposed

**Choice:** a REST API in API Gateway (regional endpoint) in front of an AWS Lambda function (Python 3.13, arm64, 256 MB, 15 s timeout).

- **Why:** pay per use and nothing to maintain. API Gateway REST offers API keys, usage plans, JSON schema validation and custom error bodies (Gateway Responses) at no extra cost.
- **Advantage:** costs nothing at rest, scales without configuration, and rejects invalid or unauthorized requests before running any code.
- **Drawback:** US$ 3.50 per million requests, cold starts on the first call of each container, and a per-account concurrency limit (10 in this account).

**Alternatives:**

- **API Gateway HTTP API**: cheaper per request, but without API keys, usage plans or request validation, which this project uses.
- **Lambda function URL**: no charge for the URL, but without key, throttle or validation at the edge.
- **A container** (ECS Fargate, Cloud Run) or a **server**: better for constant traffic or heavy self-hosted models, but with a cost per hour.
- **No API at all**: import the moderation logic as a library inside your program (see [Part 8](#part-8--adapting-it-to-your-program)).

### 15. Network: Lambda without a VPC

**Choice:** the Lambda is not attached to a VPC.

- **Why:** Comprehend, Parameter Store and OpenAI are reached over public endpoints with TLS. Inside a VPC, reaching OpenAI would need a NAT Gateway.
- **Advantage:** no NAT Gateway, which costs about US$ 32 a month just to exist.
- **Drawback:** no private network controls, such as security groups on outbound traffic.

**Alternative:** a VPC with VPC endpoints for AWS services and a NAT Gateway (or a proxy) for the third party. Needed when the function must reach private resources, such as a database.

### 16. Client access

**Choice:** one API key generated by API Gateway, tied to a usage plan (5 req/s, burst 10, 1,000 requests a day).

- **Why:** no code to write; AWS issues, checks and revokes the key.
- **Advantage:** a request without a valid key never reaches the Lambda and costs no Comprehend call.
- **Drawback:** AWS itself does not recommend API keys for authentication: they do not expire, have no scopes, and the throttle and quota are best effort. In the real test, the throttle stopped only 1 of 120 parallel requests. Here the key is a client identifier for a demo, not real authentication.

**Alternatives:**

- **JWT through a Lambda authorizer or Amazon Cognito**: real users, expiration and scopes.
- **IAM authorization (SigV4)**: for callers that already run on AWS.
- **mTLS**: for machine-to-machine clients you control.

### 17. Storing the third-party key

**Choice:** the OpenAI key is a `SecureString` in SSM Parameter Store, encrypted with the AWS-managed `aws/ssm` key. Terraform writes it with the write-only argument `value_wo`, so it never reaches the Terraform state. The Lambda reads it once per container, only when a request needs it, and keeps it in memory.

- **Why:** Parameter Store standard tier has no monthly charge, and manual rotation (change the GitHub secret, increment `value_wo_version`) is enough here.
- **Advantage:** the key is not in the code, not in the state and not in the logs, and only the moderator's role can read that exact parameter.
- **Drawback:** no automatic rotation, and the key lives in the Lambda's memory for the lifetime of each container.

**Alternatives:**

- **AWS Secrets Manager**: automatic rotation and versioning, at about US$ 0.40 per secret a month plus a charge per call.
- **A customer-managed KMS key**: control over the key policy and rotation, at about US$ 1 a month.
- **Environment variables**: simple, but the value appears in the console and in the deployment configuration. Avoid them for secrets.

### 18. Third-party packages

**Choice:** no third-party packages. OpenAI is called with `urllib` from the standard library, and AWS with the boto3 that comes with the Lambda runtime.

- **Why:** a single HTTPS call does not justify an SDK.
- **Advantage:** a tiny deployment package, no layer, no dependency updates and no supply chain risk from packages.
- **Drawback:** the retry, timeout and error handling that an SDK would provide had to be written and tested by hand.

**Alternatives:** the official OpenAI SDK or `requests` in a Lambda layer, which bring built-in retries at the cost of packaging and updates. Pin the boto3 version in your package if you depend on behavior that differs between versions.

### 19. Cost containment

**Choice:** a **kill switch**. A CloudWatch alarm counts the moderator's invocations per clock minute; more than 10 publishes to an SNS topic, which invokes a small Lambda that sets the moderator's reserved concurrency to `0` and sends an e-mail. Nothing turns it back on automatically: only a manual `apply`. A second alarm warns if the kill switch itself fails. AWS Budgets and Cost Anomaly Detection send alerts on top of that.

- **Why:** each request in modes 1 and 3 is a paid Comprehend call, and the usage plan is best effort. The kill switch is the control that actually stops spending.
- **Advantage:** the worst case is bounded at about US$ 6 for a flood (estimated, not measured), with no fixed cost.
- **Drawback:** it reacts in 1 to 3 minutes; the count follows the clock minute, so about 20 invocations split across two minutes do not trigger it; any message published to the topic takes the API down; and a legitimate burst takes it down too.

**Alternatives:**

- **AWS WAF with a rate-based rule**: blocks per IP at the edge, at about US$ 5 a month for the web ACL plus US$ 1 per rule, even with no traffic. Rejected for a demo; worth reconsidering in production.
- **Reserved concurrency** as a ceiling on parallelism (not on volume). It was planned here, but the account's limit of 10 did not allow it.
- **Counting in your own code** (for example, a counter per key in a database), with your own limits.
- **Budgets alone**: only alerts, evaluated a few times a day. Not enough as the only control.

The general lesson applies to any moderator that calls paid services: **set a hard stop, not only an alert**, and make the way back manual.

### 20. Online only during demos, with infrastructure as code

**Choice:** the whole stack is defined in Terraform, created with `apply` before a demo and removed with `destroy` afterwards.

- **Why:** a portfolio project has no real traffic; the safest and cheapest API is one that does not exist.
- **Advantage:** about US$ 0 at rest, and the stack is rebuilt identically in minutes.
- **Drawback:** a new URL and a new key at every `destroy` + `apply`, an e-mail subscription to confirm each time, and log groups left by an earlier run must be removed before the next `apply`.

**Alternatives:**

- **CloudFormation / AWS SAM** or **AWS CDK**: native to AWS, no separate state to manage.
- **Pulumi**: infrastructure in a general-purpose language.
- **Always on**: the normal choice for a real service; then decisions 16 and 19 deserve stronger options.

---

## Part 6 — Delivery (specific to this project)

### 21. Deployment pipeline

**Choice:** GitHub Actions with three workflows. `test` runs on every pull request and push to `main`, with no AWS credentials. `apply` and `destroy` run only when started by hand, only on `main`, and authenticate to AWS through **OIDC**, without any stored access key. Third-party actions are pinned by commit SHA.

- **Why:** nothing runs from a developer's machine, so no long-lived AWS credential exists to leak. A manual `apply` means no merge can turn the API back on after the kill switch.
- **Advantage:** short-lived credentials, a public record of every deploy, and a trust policy that accepts only the `main` branch of this repository.
- **Drawback:** a one-time bootstrap in the AWS Console (OIDC provider, deploy role, state bucket, budgets, opt-out policy), outside Terraform; and public logs need care (the Account ID is masked, and the `destroy` plan is not printed because it would show the API key).

**Alternatives:**

- **Access keys in GitHub secrets**: simpler, but a long-lived credential that can leak. Avoid.
- **Deploy on every merge to `main`**: the normal choice for a service that is always on; here it would undo the kill switch.
- **A deployment platform** (HCP Terraform, Atlantis, Spacelift): plan reviews and approvals, with one more service to trust.

### 22. Terraform state

**Choice:** a private S3 bucket with versioning, SSE-S3 encryption, Block Public Access, ACLs disabled, a deny-only bucket policy (only the deploy role and the admin, HTTPS only) and Terraform's native S3 lock (`use_lockfile = true`).

- **Why:** CI has no persistent disk, and the state contains sensitive values such as the API Gateway key.
- **Advantage:** no DynamoDB table for locking, no KMS permissions for the deploy role, and a corrupted state can be rolled back.
- **Drawback:** the bucket is created by hand in the bootstrap, and a mistake in the admin ARN of the bucket policy can only be fixed by the root user.

**Alternatives:** a DynamoDB table for locking (the older approach, now deprecated by Terraform in favor of the native lock), or HCP Terraform as the backend.

### 23. Deploy role permissions

**Choice:** the deploy role can only create, change and delete resources whose names start with `guardrails-*`, `iam:PassRole` works only for those roles and only toward Lambda, and the role itself is outside the prefix, with an inline policy, so it cannot change its own permissions.

- **Why:** Terraform has to create IAM roles for the Lambdas, which is a powerful permission.
- **Advantage:** the deploy role cannot touch other resources in the account or escalate through its own policy.
- **Drawback:** the prefix restricts the **names** of the roles, not the **permissions** given to them. The deploy role can still create a `guardrails-*` role with broad permissions. The accepted mitigation is the trust policy limited to `main`, in a repository with a single developer.

**Alternative:** a **permissions boundary** that every role created by the deploy role must carry, which caps what those roles can do. The next step if more people get write access to `main`.

---

## Part 7 — Testing and publishing

### 24. Unit tests

**Choice:** split the code into pure functions (`validate`, `redact`, `is_flagged`) and functions that do I/O, and test both with pytest, without network access.

- **Why:** the logic that matters most (validation, redaction, combining results, failures) can be tested without calling any real service.
- **Advantage:** fast, free and deterministic tests; 180 of them run in CI on every push.
- **Drawback:** stubs only reproduce the behavior you already know; the provider's real behavior (offsets, error codes, limits) still has to be measured.

Practices worth copying:

- **Block the network in every test** (a fixture that replaces `socket.getaddrinfo` and `socket.socket.connect`), so a missing stub fails the test instead of calling a real service.
- **Use the real SDK with stubs** (botocore's `Stubber`) instead of fake modules: the tests exercise the SDK's real exception hierarchy and retry behavior.
- **Prove that a service was *not* called**, not only that it was: an active stub with no queued responses fails on any call.
- **Write expected values literally** (`"***"`, `0.5`) instead of reading them from the code under test.
- **Check every failure by its body and its log line**, so that a `502` for the wrong reason does not pass.
- **Mutation testing by hand**: change one operator or condition in the code (`<=` to `<`, remove `reversed`, `is not False` to `is True`) and confirm that at least one test fails.

**Alternatives:** `moto` or LocalStack to emulate AWS, `responses` or `pytest-httpserver` for HTTP, and `mutmut` to automate mutation testing.

### 25. Test on the real services

**Choice:** before calling a stage done, deploy it and measure it against the real services, writing down what was confirmed and what was only expected.

- **Why:** several facts in this project could only be known by measuring: the unit of Comprehend's offsets, that a `text/plain` body skips API Gateway's validator, that the throttle does not stop short bursts, how long the kill switch takes to react.
- **Advantage:** the documentation separates measured facts from assumptions.
- **Drawback:** it costs a little money and time, and it needs care not to trigger your own protections (here, at most 7 Lambda calls per clock minute during tests).

### 26. License

**Choice:** [PolyForm Noncommercial 1.0.0](LICENSE). Anyone may read, study, modify and redistribute the code for noncommercial purposes; commercial use is not allowed.

- **Why:** the code is public as a portfolio and for study, without allowing others to sell it.
- **Advantage:** a license written for software, unlike CC BY-NC 4.0, which Creative Commons itself does not recommend for code.
- **Drawback:** it is not open source under the OSI definition, so companies cannot use the code, and some people avoid source-available projects altogether.

**Alternatives:**

- **MIT or Apache 2.0**: open source and permissive; anyone can use the code, commercially included. Apache 2.0 adds an explicit patent grant.
- **AGPL 3.0**: open source, but anyone who offers a modified version as a network service must publish their changes.
- **No license**: the code is visible on GitHub, but nobody gets the right to reuse it.

Licensing is a decision about your goals, not about moderation. If you build your own moderator from these ideas, choose the license that fits it.

---

## Part 8 — Adapting it to your program

### Choose how your program calls the moderator

| Pattern | When it fits | What to reuse from this project |
|---|---|---|
| **A function in your code** | One program, one language | `validate`, `redact`, `is_flagged`, `moderate` and the fail-closed `try/except` from [app.py](src/moderador/app.py) |
| **A shared HTTP service** | Several programs or languages | The flow, the API contract and the error format of the [README](README.md#api) |
| **A guardrail around an LLM** | Chatbots and AI features | Moderate the user's input before it reaches the model, and the model's output before it reaches the user |
| **An asynchronous step** | Comments, posts, uploads | Moderate before publishing; on failure, keep the item pending instead of publishing it |

### Swap the services behind a small interface

The moderation flow only needs two things from the providers: the **positions** of personal data and a **flagged / not flagged** answer. Keeping them behind two functions makes each service replaceable without touching the rest:

```python
from dataclasses import dataclass


@dataclass
class Span:
    start: int  # inclusive, in the same unit as Python str indices
    end: int    # exclusive


def detect_pii(text: str) -> list[Span]:
    """Return the spans to censor, already filtered by type and confidence.
    Any provider: Comprehend, Presidio, Google, Azure, regex..."""
    raise NotImplementedError


def is_inappropriate(text: str) -> bool:
    """True if the text must be censored. Must raise on any failure,
    never return False when unsure."""
    raise NotImplementedError


def merge(spans: list[Span]) -> list[Span]:
    merged: list[Span] = []
    for s in sorted(spans, key=lambda s: s.start):
        if merged and s.start <= merged[-1].end:
            merged[-1].end = max(merged[-1].end, s.end)
        else:
            merged.append(Span(s.start, s.end))
    return merged


def moderate(text: str, check_pii: bool, check_content: bool) -> str:
    if check_pii:
        for s in reversed(merge(detect_pii(text))):
            text = text[:s.start] + "***" + text[s.end:]
    if check_content and is_inappropriate(text):
        text = "***"
    return text
```

When you plug in a new provider:

1. **Convert its offsets** to your string indices (code points, UTF-16 units or bytes) and test with accents and emoji before the PII.
2. **Map its entity types** to your own closed list, and choose the confidence threshold.
3. **Raise on every failure**: timeouts, error statuses and responses in an unexpected format. The caller turns that into an error, never into uncensored text.
4. **Check its language support, region and data retention** against your users and your law.

### Checklist for your own moderator

- [ ] What does the moderator return: censored text, a decision or labels?
- [ ] Which languages must it support, and what happens to the others?
- [ ] Which personal data types matter for your users, including local identifiers?
- [ ] Which content categories must be censored, and at what score?
- [ ] Does any third party receive personal data in clear text? Can redaction run first?
- [ ] Is every failure closed? Is there a test for each one?
- [ ] Is the input validated before the first paid or external call?
- [ ] Do the logs contain only metadata?
- [ ] Where is the text processed, for how long is it kept, and is that allowed by your law?
- [ ] Is there a hard stop for cost, not only an alert?
- [ ] Were the provider's offsets, limits and errors measured, not assumed?

## Related documents

- [README.md](README.md): what the API does, how to call it, limitations, cost and threats.
- [PROJETO.md](PROJETO.md) (Portuguese): the full design document, with every decision in detail.
- [ETAPAS.md](ETAPAS.md) (Portuguese): the build checklist, with what was measured at each stage.
- [LICENSE](LICENSE): the code snippets above come from this repository, which is licensed for noncommercial use only.
