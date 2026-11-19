# Lambda principal. Etapa 7.6: validação, PII, OpenAI, resposta, falhas (502) e log de metadados.
import http.client
import json
import os
import time
import urllib.error
import urllib.request

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

MAX_TEXT_LENGTH = 1000
MODES = (1, 2, 3)

# Caso 1 do PROJETO.md: só estas entidades, e só com Score >= 0,5.
PII_ENTITIES = frozenset({
    "NAME", "EMAIL", "PHONE", "ADDRESS", "SSN",
    "CREDIT_DEBIT_NUMBER", "CREDIT_DEBIT_CVV", "CREDIT_DEBIT_EXPIRY",
    "PIN", "BANK_ACCOUNT_NUMBER", "BANK_ROUTING", "PASSPORT_NUMBER",
    "DRIVER_ID", "IP_ADDRESS", "PASSWORD",
})
MIN_SCORE = 0.5
CENSOR = "***"

# Caso 2 do PROJETO.md: Moderation da OpenAI, via urllib (sem dependências).
OPENAI_URL = "https://api.openai.com/v1/moderations"
OPENAI_MODEL = "omni-moderation-latest"
OPENAI_TIMEOUT = 3
# Total de tentativas, contando a primeira: 2 = 1 chamada + 1 retry.
OPENAI_ATTEMPTS = 2
# Só estes status HTTP valem uma segunda tentativa.
RETRY_STATUSES = frozenset({500, 502, 503, 504})

# Falhas de moderação que viram 502 (fail closed). O que não está aqui sobe e vira 5xx.
FAILURES = (
    BotoCoreError,  # Comprehend e SSM: timeout, conexão, parâmetros
    ClientError,  # Comprehend e SSM: erro devolvido pelo serviço
    OSError,  # OpenAI: timeout, conexão e HTTPError (4xx/5xx)
    http.client.HTTPException,  # OpenAI: resposta HTTP cortada ou malformada
    ValueError,  # OpenAI: corpo que não é JSON
    LookupError,  # resposta sem o campo esperado (KeyError, IndexError)
    TypeError,  # resposta que não é dicionário/lista
)
MSG_INVALID = "invalid request"
MSG_UNAVAILABLE = "moderation service unavailable"

# Timeouts e retries do PROJETO.md ("Tratamento de falhas").
# total_max_attempts conta a chamada inicial: 2 = 1 chamada + 1 retry.
_AWS_CONFIG = Config(
    connect_timeout=1,
    read_timeout=3,
    retries={"total_max_attempts": 2, "mode": "standard"},
)

# Criados uma vez por container, no import (fora do handler).
_ssm = boto3.client("ssm", config=_AWS_CONFIG)
_comprehend = boto3.client("comprehend", config=_AWS_CONFIG)

# Chave da OpenAI: lida do SSM na primeira requisição que precisa dela.
_openai_key = None


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _error(status, message):
    return _response(status, {"error": message})


def _is_json_content_type(headers):
    for name, value in (headers or {}).items():
        if name.lower() == "content-type":
            media_type = (value or "").split(";", 1)[0]
            return media_type.strip().lower() == "application/json"
    return False


def validate(event):
    """Devolve (text, mode) se a requisição for válida, senão None."""
    if not _is_json_content_type(event.get("headers")):
        return None

    body = event.get("body")
    if not isinstance(body, str):
        return None
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None

    text = data.get("text")
    mode = data.get("mode")

    if type(text) is not str or not 1 <= len(text) <= MAX_TEXT_LENGTH:
        return None
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return None

    if type(mode) is not int or mode not in MODES:
        return None

    return text, mode


def _pii_spans(entities):
    """Filtra as entidades e funde os trechos sobrepostos ou adjacentes."""
    spans = sorted(
        (e["BeginOffset"], e["EndOffset"])
        for e in entities
        if e["Type"] in PII_ENTITIES and e["Score"] >= MIN_SCORE
    )
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def redact(text, entities):
    """Troca cada trecho de PII por ***, do fim para o início do texto."""
    for start, end in reversed(_pii_spans(entities)):
        text = text[:start] + CENSOR + text[end:]
    return text


def _detect_pii(text):
    response = _comprehend.detect_pii_entities(Text=text, LanguageCode="en")
    return response["Entities"]


def _get_openai_key():
    global _openai_key
    if _openai_key is None:
        response = _ssm.get_parameter(
            Name=os.environ["OPENAI_KEY_PARAMETER"],
            WithDecryption=True,
        )
        _openai_key = response["Parameter"]["Value"]
    return _openai_key


def _query_openai(text):
    """Manda o texto à Moderation e devolve o JSON da resposta."""
    body = json.dumps({"model": OPENAI_MODEL, "input": text}).encode("utf-8")
    request = urllib.request.Request(
        OPENAI_URL,
        data=body,
        headers={
            "Authorization": "Bearer " + _get_openai_key(),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    for attempt in range(1, OPENAI_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=OPENAI_TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if attempt == OPENAI_ATTEMPTS or error.code not in RETRY_STATUSES:
                raise
        except OSError:
            if attempt == OPENAI_ATTEMPTS:
                raise


def is_flagged(response):
    """True se a Moderation sinalizou o texto. Só o False exato libera."""
    return response["results"][0]["flagged"] is not False


def moderate(text, mode):
    """Devolve o texto censurado conforme o modo. Qualquer falha levanta exceção."""
    if mode in (1, 3):
        # Caso 1: PII trocada por ***.
        text = redact(text, _detect_pii(text))

    if mode in (2, 3) and is_flagged(_query_openai(text)):
        # Caso 2: texto sinalizado sai inteiro como ***.
        text = CENSOR

    return text


def _error_code(error):
    """Status HTTP da OpenAI ou código de erro da AWS. Nunca a mensagem."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code
    if isinstance(error, ClientError):
        return error.response.get("Error", {}).get("Code")
    return None


def _log(start, status, mode=None, length=None, error=None):
    """Uma linha de metadados por requisição. Nunca o texto nem a mensagem do erro."""
    record = {
        "status": status,
        "mode": mode,
        "length": length,
        "ms": round((time.monotonic() - start) * 1000),
    }
    if error is not None:
        record["failure"] = type(error).__name__
        record["code"] = _error_code(error)
    print(json.dumps(record))


def handler(event, context):
    start = time.monotonic()

    request = validate(event)
    if request is None:
        _log(start, 400)
        return _error(400, MSG_INVALID)

    text, mode = request
    length = len(text)
    try:
        censored = moderate(text, mode)
    except FAILURES as error:
        # Fail closed: a falha termina aqui, sem texto na resposta.
        _log(start, 502, mode, length, error)
        return _error(502, MSG_UNAVAILABLE)

    # Só chega aqui o texto que passou por todas as etapas do modo, sem erro.
    _log(start, 200, mode, length)
    return _response(200, {"censored_text": censored})
