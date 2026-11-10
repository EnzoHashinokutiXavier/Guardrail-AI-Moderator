# Lambda principal. Etapa 7.2: validação e clientes; a moderação vem na 7.3 a 7.6.
import json
import os

import boto3
from botocore.config import Config

TEXTO_MAX = 1000
MODOS = (1, 2, 3)

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


def _obter_chave_openai():
    global _chave_openai
    if _chave_openai is None:
        resposta = _ssm.get_parameter(
            Name=os.environ["PARAMETRO_OPENAI"],
            WithDecryption=True,
        )
        _chave_openai = resposta["Parameter"]["Value"]
    return _chave_openai


def handler(event, context):
    requisicao = validar(event)
    if requisicao is None:
        return _erro(400, "requisição inválida")

    texto, modo = requisicao
    if modo in (2, 3):
        # Provisório: só prova a leitura do SSM. A 7.4 usa a chave na OpenAI.
        _obter_chave_openai()

    # Provisório até a 7.5: requisição válida continua em fail closed.
    return _erro(502, "serviço de moderação indisponível")
