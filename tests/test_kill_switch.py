# Etapa 8.5: kill switch. Zera a concorrência da Lambda principal; qualquer falha sobe (conta em Errors).
import logging
import types

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError
from botocore.stub import Stubber

from kill_switch import app as kill_switch

ALVO = "funcao-alvo-de-teste"  # diferente do nome real: prova que vem do FUNCAO_ALVO
CHAMADA = {"FunctionName": ALVO, "ReservedConcurrentExecutions": 0}
MSG_OK = "kill switch: concorrência de funcao-alvo-de-teste zerada"
ENDPOINT = "https://lambda.us-east-1.amazonaws.com/"

# Formato do evento que o SNS entrega à Lambda (o handler não lê; serve para provar isso).
EVENTO_SNS = {
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


def mensagens(caplog):
    """Só o que o app.py loga (logger raiz); o botocore tem loggers próprios."""
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "root"]


@pytest.fixture
def aws_lambda(monkeypatch):
    """Cliente Lambda real com Stubber, entregue ao handler no lugar do boto3.client(...)."""
    monkeypatch.setenv("FUNCAO_ALVO", ALVO)
    cliente = boto3.client("lambda")  # região e credenciais falsas do conftest.py
    pedidos = []

    def client(*args, **kwargs):
        pedidos.append((args, kwargs))
        return cliente

    monkeypatch.setattr(kill_switch, "boto3", types.SimpleNamespace(client=client))
    with Stubber(cliente) as stubber:
        yield types.SimpleNamespace(stubber=stubber, pedidos=pedidos)
        stubber.assert_no_pending_responses()


@pytest.fixture
def aws_lambda_sem_rede(monkeypatch):
    """Sem Stubber: o handler cria o cliente real (com os argumentos dele) e cada envio levanta falso.erro."""
    monkeypatch.setenv("FUNCAO_ALVO", ALVO)
    falso = types.SimpleNamespace(erro=None, urls=[])

    def antes_de_enviar(request, **kwargs):
        falso.urls.append(request.url)
        raise falso.erro(endpoint_url=request.url)

    def client(*args, **kwargs):
        cliente = boto3.client(*args, **kwargs)
        cliente.meta.events.register("before-send.lambda.PutFunctionConcurrency", antes_de_enviar)
        return cliente

    monkeypatch.setattr(kill_switch, "boto3", types.SimpleNamespace(client=client))
    return falso


def esperar_sucesso(aws_lambda):
    aws_lambda.stubber.add_response("put_function_concurrency", {"ReservedConcurrentExecutions": 0}, CHAMADA)


# Bloco A - zera a concorrência

@pytest.mark.parametrize("evento", [
    pytest.param(EVENTO_SNS, id="sns"),
    pytest.param({}, id="vazio"),
    pytest.param(None, id="none"),
])
def test_zera_a_concorrencia(aws_lambda, caplog, evento):
    esperar_sucesso(aws_lambda)
    assert kill_switch.handler(evento, None) is None
    assert aws_lambda.pedidos == [(("lambda",), {})]
    assert mensagens(caplog) == [(logging.INFO, MSG_OK)]


def test_segunda_execucao_zera_de_novo(aws_lambda, caplog):
    # Nova tentativa do SNS/Lambda ou alarme de novo: PutFunctionConcurrency com 0 é idempotente.
    esperar_sucesso(aws_lambda)
    esperar_sucesso(aws_lambda)
    kill_switch.handler(EVENTO_SNS, None)
    kill_switch.handler(EVENTO_SNS, None)
    assert len(aws_lambda.pedidos) == 2
    assert mensagens(caplog) == [(logging.INFO, MSG_OK)] * 2


# Bloco B - falha sobe: sem try no handler, a exceção conta em Errors e dispara o alarme kill-switch-erros

@pytest.mark.parametrize("codigo, classe", [
    ("ResourceNotFoundException", "ResourceNotFoundException"),
    ("TooManyRequestsException", "TooManyRequestsException"),
    ("ServiceException", "ServiceException"),
    ("InvalidParameterValueException", "InvalidParameterValueException"),
    ("AccessDeniedException", "ClientError"),  # não modelado no Lambda
])
def test_erro_da_api_sobe(aws_lambda, caplog, codigo, classe):
    aws_lambda.stubber.add_client_error("put_function_concurrency", codigo, expected_params=CHAMADA)
    with pytest.raises(ClientError) as erro:
        kill_switch.handler(EVENTO_SNS, None)
    assert type(erro.value).__name__ == classe
    assert erro.value.response["Error"]["Code"] == codigo
    assert mensagens(caplog) == []  # nada de "zerada" se não zerou


@pytest.mark.parametrize("erro", [
    pytest.param(ReadTimeoutError, id="timeout-leitura"),
    pytest.param(EndpointConnectionError, id="sem-conexao"),
])
def test_sem_rede_sobe(aws_lambda_sem_rede, caplog, erro):
    aws_lambda_sem_rede.erro = erro
    with pytest.raises(erro):
        kill_switch.handler(EVENTO_SNS, None)
    # Retry padrão do botocore 1.44.2 (modo "standard"): 1 chamada + 2 retries, na região do ambiente.
    assert len(aws_lambda_sem_rede.urls) == 3
    assert all(url.startswith(ENDPOINT) for url in aws_lambda_sem_rede.urls)
    assert mensagens(caplog) == []


def test_sem_variavel_sobe_antes_de_chamar(aws_lambda, monkeypatch, caplog):
    monkeypatch.delenv("FUNCAO_ALVO")
    with pytest.raises(KeyError):
        kill_switch.handler(EVENTO_SNS, None)
    assert aws_lambda.pedidos == []
    assert mensagens(caplog) == []
