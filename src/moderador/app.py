# Lambda principal. Etapa 7.1: só a validação; a moderação vem na 7.2 a 7.6.
import json

TEXTO_MAX = 1000
MODOS = (1, 2, 3)


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


def handler(event, context):
    requisicao = validar(event)
    if requisicao is None:
        return _erro(400, "requisição inválida")

    # Provisório até a 7.5: requisição válida continua em fail closed.
    return _erro(502, "serviço de moderação indisponível")
