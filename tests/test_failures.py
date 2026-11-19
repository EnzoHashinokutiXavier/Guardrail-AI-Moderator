# Etapa 8.4: fail closed. Falha ou timeout de Comprehend, SSM ou OpenAI vira 502, sem texto.
import http.client
import json
import types
import urllib.error

import pytest
from botocore.exceptions import ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError

from moderador import app

KEY = "sk-test-fake"
URL = "https://api.openai.com/v1/moderations"
T = "My name is John Smith."
REDACTED = "My name is ***."
NAME = [{"Type": "NAME", "BeginOffset": 11, "EndOffset": 21, "Score": 0.99}]
CLEAN = {"results": [{"flagged": False}]}
FLAGGED = {"results": [{"flagged": True}]}
BODY_502 = '{"error": "moderation service unavailable"}'


def event(text, mode):
    return {
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"text": text, "mode": mode}),
    }


def call(text, mode, capsys):
    """Chama o handler. Devolve a resposta e a linha de log (tem de ser exatamente uma)."""
    response = app.handler(event(text, mode), None)
    [line] = capsys.readouterr().out.splitlines()
    return response, line


def check_502(response, line, mode, failure, code=None):
    """502 da Lambda com o corpo exato, e o log com a falha certa e só metadados."""
    assert response["statusCode"] == 502
    assert response["headers"] == {"Content-Type": "application/json"}
    assert response["body"] == BODY_502
    record = json.loads(line)
    assert type(record.pop("ms")) is int
    assert record == {"status": 502, "mode": mode, "length": 22, "failure": failure, "code": code}


def expect_pii(comprehend):
    comprehend.add_response("detect_pii_entities", {"Entities": NAME}, {"Text": T, "LanguageCode": "en"})


def http_error(status):
    return urllib.error.HTTPError(URL, status, "simulated error", {}, None)


def sent_texts(openai):
    return [json.loads(r.data)["input"] for r in openai.requests]


@pytest.fixture
def cached_key(ssm, monkeypatch):
    """Container quente: a chave já foi lida. O Stubber do SSM fica vazio (nenhuma leitura)."""
    monkeypatch.setattr(app, "_openai_key", KEY)


@pytest.fixture
def comprehend_no_network():
    """Cada tentativa de envio do Comprehend levanta fake.error, dentro do botocore real (com o retry)."""
    fake = types.SimpleNamespace(error=None, attempts=0)

    def before_send(request, **kwargs):
        fake.attempts += 1
        raise fake.error(endpoint_url=request.url)

    events = app._comprehend.meta.events
    events.register("before-send.comprehend.DetectPiiEntities", before_send, unique_id="test-8.4")
    yield fake
    events.unregister("before-send.comprehend.DetectPiiEntities", unique_id="test-8.4")


# Item 1: fail closed com erro do Comprehend (modos 1 e 3)

# Código modelado pelo serviço vira uma subclasse de ClientError com o mesmo nome; o resto fica ClientError.
@pytest.mark.parametrize("mode", [1, 3])
@pytest.mark.parametrize("code, failure", [
    ("TooManyRequestsException", "TooManyRequestsException"),
    ("InternalServerException", "InternalServerException"),
    ("UnsupportedLanguageException", "UnsupportedLanguageException"),
    ("AccessDeniedException", "ClientError"),  # não modelado no Comprehend
])
def test_comprehend_service_error(comprehend, ssm, openai, capsys, mode, code, failure):
    comprehend.add_client_error("detect_pii_entities", code, expected_params={"Text": T, "LanguageCode": "en"})
    response, line = call(T, mode, capsys)
    check_502(response, line, mode, failure, code)
    assert openai.requests == []


@pytest.mark.parametrize("mode", [1, 3])
@pytest.mark.parametrize("error, name", [
    pytest.param(ReadTimeoutError, "ReadTimeoutError", id="read-timeout"),
    pytest.param(ConnectTimeoutError, "ConnectTimeoutError", id="connect-timeout"),
    pytest.param(EndpointConnectionError, "EndpointConnectionError", id="no-connection"),
])
def test_comprehend_timeout(comprehend_no_network, ssm, openai, capsys, mode, error, name):
    comprehend_no_network.error = error
    response, line = call(T, mode, capsys)
    check_502(response, line, mode, name)
    assert comprehend_no_network.attempts == 2  # total_max_attempts: 1 chamada + 1 retry
    assert openai.requests == []


def test_comprehend_malformed_response(comprehend, ssm, openai, capsys):
    # Entidade sem "Score": o filtro do redact levanta KeyError.
    entity = {"Type": "NAME", "BeginOffset": 11, "EndOffset": 21}
    comprehend.add_response("detect_pii_entities", {"Entities": [entity]}, {"Text": T, "LanguageCode": "en"})
    response, line = call(T, 1, capsys)
    check_502(response, line, 1, "KeyError")


def test_comprehend_error_message_does_not_leak(comprehend, ssm, openai, capsys):
    # A mensagem do serviço cita o texto: nem a resposta nem o log podem repeti-la.
    comprehend.add_client_error("detect_pii_entities", "InvalidRequestException", "text rejected: " + T)
    response, line = call(T, 3, capsys)
    check_502(response, line, 3, "InvalidRequestException", "InvalidRequestException")
    for part in ("John", "rejected"):
        assert part not in response["body"]
        assert part not in line


# Item 2: fail closed com erro/timeout da OpenAI (modos 2 e 3)

@pytest.mark.parametrize("mode", [2, 3])
@pytest.mark.parametrize("failure, name, code", [
    pytest.param(lambda: TimeoutError("timed out"), "TimeoutError", None, id="timeout"),
    pytest.param(lambda: urllib.error.URLError("no network"), "URLError", None, id="no-connection"),
    pytest.param(lambda: http.client.RemoteDisconnected("closed"), "RemoteDisconnected", None, id="connection-dropped"),
    pytest.param(lambda: http_error(500), "HTTPError", 500, id="500"),
    pytest.param(lambda: http_error(502), "HTTPError", 502, id="502"),
    pytest.param(lambda: http_error(503), "HTTPError", 503, id="503"),
    pytest.param(lambda: http_error(504), "HTTPError", 504, id="504"),
])
def test_openai_fails_both_attempts(comprehend, cached_key, openai, capsys, mode, failure, name, code):
    if mode == 3:
        expect_pii(comprehend)
    # Uma terceira tentativa cairia na fila vazia, que responde "não sinalizado": daria 200.
    openai.queue = [failure(), failure()]
    response, line = call(T, mode, capsys)
    check_502(response, line, mode, name, code)
    assert len(openai.requests) == 2
    assert openai.queue == []


@pytest.mark.parametrize("mode", [2, 3])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
def test_openai_4xx_without_retry(comprehend, cached_key, openai, capsys, mode, status):
    if mode == 3:
        expect_pii(comprehend)
    openai.queue = [http_error(status)]
    response, line = call(T, mode, capsys)
    check_502(response, line, mode, "HTTPError", status)
    assert len(openai.requests) == 1


@pytest.mark.parametrize("openai_response, name", [
    pytest.param(b"<html>error</html>", "JSONDecodeError", id="html"),
    pytest.param(b"", "JSONDecodeError", id="empty-body"),
    pytest.param(http.client.IncompleteRead(b""), "IncompleteRead", id="truncated-body"),
    pytest.param({"error": {"message": "x"}}, "KeyError", id="no-results"),
    pytest.param({"results": []}, "IndexError", id="empty-results"),
    pytest.param({"results": [{}]}, "KeyError", id="no-flagged"),
    pytest.param({"results": None}, "TypeError", id="null-results"),
    pytest.param(None, "TypeError", id="null"),
    pytest.param([], "TypeError", id="list"),
])
def test_openai_invalid_response_without_retry(comprehend, cached_key, openai, capsys, openai_response, name):
    openai.queue = [openai_response]
    response, line = call(T, 2, capsys)
    check_502(response, line, 2, name)
    assert len(openai.requests) == 1


def test_mode3_openai_failure_does_not_return_redacted(comprehend, cached_key, openai, capsys):
    # O Comprehend redigiu e a OpenAI recebeu o redigido duas vezes; mesmo assim, nada volta.
    expect_pii(comprehend)
    openai.queue = [TimeoutError("1"), TimeoutError("2")]
    response, line = call(T, 3, capsys)
    check_502(response, line, 3, "TimeoutError")
    assert sent_texts(openai) == [REDACTED, REDACTED]
    assert "***" not in response["body"]


@pytest.mark.parametrize("failure", [
    pytest.param(lambda: TimeoutError("timed out"), id="timeout"),
    pytest.param(lambda: urllib.error.URLError("no network"), id="no-connection"),
    pytest.param(lambda: http_error(503), id="503"),
])
def test_openai_retry_succeeds(comprehend, cached_key, openai, capsys, failure):
    openai.queue = [failure(), CLEAN]
    response, line = call(T, 2, capsys)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"censored_text": T}
    assert len(openai.requests) == 2
    assert set(json.loads(line)) == {"status", "mode", "length", "ms"}


def test_openai_retry_uses_second_response(comprehend, cached_key, openai, capsys):
    # A segunda tentativa diz "sinalizado": vale ela, não o texto original.
    openai.queue = [TimeoutError("timed out"), FLAGGED]
    response, line = call(T, 2, capsys)
    assert response["statusCode"] == 200
    assert response["body"] == '{"censored_text": "***"}'
    [first, second] = openai.requests
    assert first.data == second.data


# Complemento: sem a chave do SSM não há OpenAI

@pytest.mark.parametrize("mode", [2, 3])
@pytest.mark.parametrize("code, failure", [
    ("AccessDeniedException", "AccessDeniedException"),  # modelado no botocore 1.44.2 (na Lambda, 7.6: ClientError)
    ("ParameterNotFound", "ParameterNotFound"),
    ("ThrottlingException", "ThrottlingException"),
    ("UnrecognizedClientException", "ClientError"),  # não modelado no SSM
])
def test_ssm_failure(comprehend, ssm, openai, capsys, mode, code, failure):
    if mode == 3:
        expect_pii(comprehend)
    ssm.add_client_error(
        "get_parameter", code,
        expected_params={"Name": "guardrails-openai-api-key", "WithDecryption": True},
    )
    response, line = call(T, mode, capsys)
    check_502(response, line, mode, failure, code)
    assert openai.requests == []
    assert app._openai_key is None


def test_ssm_failure_not_cached(comprehend, ssm, openai, capsys):
    # Depois de uma falha, a requisição seguinte lê o SSM de novo e funciona.
    ssm.add_client_error("get_parameter", "ThrottlingException")
    ssm.add_response(
        "get_parameter",
        {"Parameter": {"Name": "guardrails-openai-api-key", "Type": "SecureString", "Value": KEY}},
        {"Name": "guardrails-openai-api-key", "WithDecryption": True},
    )
    response, line = call(T, 2, capsys)
    check_502(response, line, 2, "ThrottlingException", "ThrottlingException")
    response, line = call(T, 2, capsys)
    assert response["statusCode"] == 200
    assert len(openai.requests) == 1


def test_missing_parameter_variable(comprehend, ssm, openai, capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_KEY_PARAMETER")
    response, line = call(T, 2, capsys)
    check_502(response, line, 2, "KeyError")
    assert openai.requests == []


# Erro fora de FAILURES é defeito de código: sobe (5xx do API Gateway), não vira 502 nem 200

def test_error_outside_list_propagates(comprehend, cached_key, openai, capsys):
    openai.queue = [RuntimeError("simulated defect")]
    with pytest.raises(RuntimeError):
        app.handler(event(T, 2), None)
    assert capsys.readouterr().out == ""
    assert len(openai.requests) == 1
