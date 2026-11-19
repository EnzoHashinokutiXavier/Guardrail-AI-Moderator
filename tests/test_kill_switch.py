# Etapa 8.5: kill switch. Zera a concorrência da Lambda principal; qualquer falha sobe (conta em Errors).
import logging
import types

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError
from botocore.stub import Stubber

from kill_switch import app as kill_switch

TARGET = "test-target-function"  # diferente do nome real: prova que vem do TARGET_FUNCTION
CALL = {"FunctionName": TARGET, "ReservedConcurrentExecutions": 0}
MSG_OK = "kill switch: concurrency of test-target-function set to 0"
ENDPOINT = "https://lambda.us-east-1.amazonaws.com/"

# Formato do evento que o SNS entrega à Lambda (o handler não lê; serve para provar isso).
SNS_EVENT = {
    "Records": [{
        "EventSource": "aws:sns",
        "EventVersion": "1.0",
        "EventSubscriptionArn": "arn:aws:sns:us-east-1:123456789012:guardrails-alertas:abc",
        "Sns": {
            "Type": "Notification",
            "TopicArn": "arn:aws:sns:us-east-1:123456789012:guardrails-alertas",
            "Subject": 'ALARM: "guardrails-moderador-invocacoes" in US East (N. Virginia)',
            "Message": '{"AlarmName": "guardrails-moderador-invocacoes", "NewStateValue": "ALARM"}',
        },
    }],
}


def messages(caplog):
    """Só o que o app.py loga (logger raiz); o botocore tem loggers próprios."""
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "root"]


@pytest.fixture
def aws_lambda(monkeypatch):
    """Cliente Lambda real com Stubber, entregue ao handler no lugar do boto3.client(...)."""
    monkeypatch.setenv("TARGET_FUNCTION", TARGET)
    client_ = boto3.client("lambda")  # região e credenciais falsas do conftest.py
    calls = []

    def client(*args, **kwargs):
        calls.append((args, kwargs))
        return client_

    monkeypatch.setattr(kill_switch, "boto3", types.SimpleNamespace(client=client))
    with Stubber(client_) as stubber:
        yield types.SimpleNamespace(stubber=stubber, calls=calls)
        stubber.assert_no_pending_responses()


@pytest.fixture
def aws_lambda_no_network(monkeypatch):
    """Sem Stubber: o handler cria o cliente real (com os argumentos dele) e cada envio levanta fake.error."""
    monkeypatch.setenv("TARGET_FUNCTION", TARGET)
    fake = types.SimpleNamespace(error=None, urls=[])

    def before_send(request, **kwargs):
        fake.urls.append(request.url)
        raise fake.error(endpoint_url=request.url)

    def client(*args, **kwargs):
        client_ = boto3.client(*args, **kwargs)
        client_.meta.events.register("before-send.lambda.PutFunctionConcurrency", before_send)
        return client_

    monkeypatch.setattr(kill_switch, "boto3", types.SimpleNamespace(client=client))
    return fake


def expect_success(aws_lambda):
    aws_lambda.stubber.add_response("put_function_concurrency", {"ReservedConcurrentExecutions": 0}, CALL)


# Bloco A - zera a concorrência

@pytest.mark.parametrize("event", [
    pytest.param(SNS_EVENT, id="sns"),
    pytest.param({}, id="empty"),
    pytest.param(None, id="none"),
])
def test_sets_concurrency_to_zero(aws_lambda, caplog, event):
    expect_success(aws_lambda)
    assert kill_switch.handler(event, None) is None
    assert aws_lambda.calls == [(("lambda",), {})]
    assert messages(caplog) == [(logging.INFO, MSG_OK)]


def test_second_run_sets_zero_again(aws_lambda, caplog):
    # Nova tentativa do SNS/Lambda ou alarme de novo: PutFunctionConcurrency com 0 é idempotente.
    expect_success(aws_lambda)
    expect_success(aws_lambda)
    kill_switch.handler(SNS_EVENT, None)
    kill_switch.handler(SNS_EVENT, None)
    assert len(aws_lambda.calls) == 2
    assert messages(caplog) == [(logging.INFO, MSG_OK)] * 2


# Bloco B - falha sobe: sem try no handler, a exceção conta em Errors e dispara o alarme kill-switch-erros

@pytest.mark.parametrize("code, cls", [
    ("ResourceNotFoundException", "ResourceNotFoundException"),
    ("TooManyRequestsException", "TooManyRequestsException"),
    ("ServiceException", "ServiceException"),
    ("InvalidParameterValueException", "InvalidParameterValueException"),
    ("AccessDeniedException", "ClientError"),  # não modelado no Lambda
])
def test_api_error_propagates(aws_lambda, caplog, code, cls):
    aws_lambda.stubber.add_client_error("put_function_concurrency", code, expected_params=CALL)
    with pytest.raises(ClientError) as error:
        kill_switch.handler(SNS_EVENT, None)
    assert type(error.value).__name__ == cls
    assert error.value.response["Error"]["Code"] == code
    assert messages(caplog) == []  # nada de "set to 0" se não zerou


@pytest.mark.parametrize("error", [
    pytest.param(ReadTimeoutError, id="read-timeout"),
    pytest.param(EndpointConnectionError, id="no-connection"),
])
def test_no_network_propagates(aws_lambda_no_network, caplog, error):
    aws_lambda_no_network.error = error
    with pytest.raises(error):
        kill_switch.handler(SNS_EVENT, None)
    # Retry padrão do botocore 1.44.2 (modo "standard"): 1 chamada + 2 retries, na região do ambiente.
    assert len(aws_lambda_no_network.urls) == 3
    assert all(url.startswith(ENDPOINT) for url in aws_lambda_no_network.urls)
    assert messages(caplog) == []


def test_missing_variable_fails_before_call(aws_lambda, monkeypatch, caplog):
    monkeypatch.delenv("TARGET_FUNCTION")
    with pytest.raises(KeyError):
        kill_switch.handler(SNS_EVENT, None)
    assert aws_lambda.calls == []
    assert messages(caplog) == []
