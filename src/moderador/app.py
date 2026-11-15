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

TEXTO_MAX = 1000
MODOS = (1, 2, 3)

# Caso 1 do PROJETO.md: só estas entidades, e só com Score >= 0,5.
ENTIDADES_PII = frozenset({
    "NAME", "EMAIL", "PHONE", "ADDRESS", "SSN",
    "CREDIT_DEBIT_NUMBER", "CREDIT_DEBIT_CVV", "CREDIT_DEBIT_EXPIRY",
    "PIN", "BANK_ACCOUNT_NUMBER", "BANK_ROUTING", "PASSPORT_NUMBER",
    "DRIVER_ID", "IP_ADDRESS", "PASSWORD",
})
SCORE_MIN = 0.5
CENSURA = "***"

# Caso 2 do PROJETO.md: Moderation da OpenAI, via urllib (sem dependências).
URL_OPENAI = "https://api.openai.com/v1/moderations"
MODELO_OPENAI = "omni-moderation-latest"
TIMEOUT_OPENAI = 3
# Total de tentativas, contando a primeira: 2 = 1 chamada + 1 retry.
TENTATIVAS_OPENAI = 2
# Só estes status HTTP valem uma segunda tentativa.
STATUS_RETRY = frozenset({500, 502, 503, 504})

# Falhas de moderação que viram 502 (fail closed). O que não está aqui sobe e vira 5xx.
FALHAS = (
    BotoCoreError,  # Comprehend e SSM: timeout, conexão, parâmetros
    ClientError,  # Comprehend e SSM: erro devolvido pelo serviço
    OSError,  # OpenAI: timeout, conexão e HTTPError (4xx/5xx)
    http.client.HTTPException,  # OpenAI: resposta HTTP cortada ou malformada
    ValueError,  # OpenAI: corpo que não é JSON
    LookupError,  # resposta sem o campo esperado (KeyError, IndexError)
    TypeError,  # resposta que não é dicionário/lista
)
MSG_INDISPONIVEL = "serviço de moderação indisponível"

# Timeouts e retries do PROJETO.md ("Tratamento de falhas").
# total_max_attempts conta a chamada inicial: 2 = 1 chamada + 1 retry.
_CONFIG_AWS = Config(
    connect_timeout=1,
    read_timeout=3,
    retries={"total_max_attempts": 2, "mode": "standard"},
)

# Criados uma vez por container, no import (fora do handler).
_ssm = boto3.client("ssm", config=_CONFIG_AWS)
_comprehend = boto3.client("comprehend", config=_CONFIG_AWS)

# Chave da OpenAI: lida do SSM na primeira requisição que precisa dela.
_chave_openai = None


def _resposta(status, corpo):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(corpo, ensure_ascii=False),
    }


def _erro(status, mensagem):
    return _resposta(status, {"erro": mensagem})


def _content_type_json(headers):
    for nome, valor in (headers or {}).items():
        if nome.lower() == "content-type":
            tipo = (valor or "").split(";", 1)[0]
            return tipo.strip().lower() == "application/json"
    return False


def validar(event):
    """Devolve (texto, modo) se a requisição for válida, senão None."""
    if not _content_type_json(event.get("headers")):
        return None

    corpo = event.get("body")
    if not isinstance(corpo, str):
        return None
    try:
        dados = json.loads(corpo)
    except (ValueError, RecursionError):
        return None
    if not isinstance(dados, dict):
        return None

    texto = dados.get("texto")
    modo = dados.get("modo")

    if type(texto) is not str or not 1 <= len(texto) <= TEXTO_MAX:
        return None
    try:
        texto.encode("utf-8")
    except UnicodeEncodeError:
        return None

    if type(modo) is not int or modo not in MODOS:
        return None

    return texto, modo


def _intervalos_pii(entidades):
    """Filtra as entidades e funde os trechos sobrepostos ou adjacentes."""
    trechos = sorted(
        (e["BeginOffset"], e["EndOffset"])
        for e in entidades
        if e["Type"] in ENTIDADES_PII and e["Score"] >= SCORE_MIN
    )
    fundidos = []
    for inicio, fim in trechos:
        if fundidos and inicio <= fundidos[-1][1]:
            fundidos[-1][1] = max(fundidos[-1][1], fim)
        else:
            fundidos.append([inicio, fim])
    return fundidos


def redigir(texto, entidades):
    """Troca cada trecho de PII por ***, do fim para o início do texto."""
    for inicio, fim in reversed(_intervalos_pii(entidades)):
        texto = texto[:inicio] + CENSURA + texto[fim:]
    return texto


def _detectar_pii(texto):
    resposta = _comprehend.detect_pii_entities(Text=texto, LanguageCode="en")
    return resposta["Entities"]


def _obter_chave_openai():
    global _chave_openai
    if _chave_openai is None:
        resposta = _ssm.get_parameter(
            Name=os.environ["PARAMETRO_OPENAI"],
            WithDecryption=True,
        )
        _chave_openai = resposta["Parameter"]["Value"]
    return _chave_openai


def _consultar_openai(texto):
    """Manda o texto à Moderation e devolve o JSON da resposta."""
    corpo = json.dumps({"model": MODELO_OPENAI, "input": texto}).encode("utf-8")
    requisicao = urllib.request.Request(
        URL_OPENAI,
        data=corpo,
        headers={
            "Authorization": "Bearer " + _obter_chave_openai(),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    for tentativa in range(1, TENTATIVAS_OPENAI + 1):
        try:
            with urllib.request.urlopen(requisicao, timeout=TIMEOUT_OPENAI) as resposta:
                return json.load(resposta)
        except urllib.error.HTTPError as erro:
            if tentativa == TENTATIVAS_OPENAI or erro.code not in STATUS_RETRY:
                raise
        except OSError:
            if tentativa == TENTATIVAS_OPENAI:
                raise


def sinalizado(resposta):
    """True se a Moderation sinalizou o texto. Só o False exato libera."""
    return resposta["results"][0]["flagged"] is not False


def moderar(texto, modo):
    """Devolve o texto censurado conforme o modo. Qualquer falha levanta exceção."""
    if modo in (1, 3):
        # Caso 1: PII trocada por ***.
        texto = redigir(texto, _detectar_pii(texto))

    if modo in (2, 3) and sinalizado(_consultar_openai(texto)):
        # Caso 2: texto sinalizado sai inteiro como ***.
        texto = CENSURA

    return texto


def _codigo(erro):
    """Status HTTP da OpenAI ou código de erro da AWS. Nunca a mensagem."""
    if isinstance(erro, urllib.error.HTTPError):
        return erro.code
    if isinstance(erro, ClientError):
        return erro.response.get("Error", {}).get("Code")
    return None


def _logar(inicio, status, modo=None, tamanho=None, erro=None):
    """Uma linha de metadados por requisição. Nunca o texto nem a mensagem do erro."""
    registro = {
        "status": status,
        "modo": modo,
        "tamanho": tamanho,
        "ms": round((time.monotonic() - inicio) * 1000),
    }
    if erro is not None:
        registro["falha"] = type(erro).__name__
        registro["codigo"] = _codigo(erro)
    print(json.dumps(registro))


def handler(event, context):
    inicio = time.monotonic()

    requisicao = validar(event)
    if requisicao is None:
        _logar(inicio, 400)
        return _erro(400, "requisição inválida")

    texto, modo = requisicao
    tamanho = len(texto)
    try:
        censurado = moderar(texto, modo)
    except FALHAS as erro:
        # Fail closed: a falha termina aqui, sem texto na resposta.
        _logar(inicio, 502, modo, tamanho, erro)
        return _erro(502, MSG_INDISPONIVEL)

    # Só chega aqui o texto que passou por todas as etapas do modo, sem erro.
    _logar(inicio, 200, modo, tamanho)
    return _resposta(200, {"texto_censurado": censurado})
