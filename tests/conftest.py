# Configuração comum dos testes (etapas 8.0, 8.3 e 8.4). Nenhum teste fala com a AWS nem com a OpenAI.
import io
import json
import os
import socket
import types
import urllib.request

import pytest
from botocore.stub import Stubber

# Roda antes do import do app.py, que cria os clientes boto3 no import e exige uma região.
# Credenciais falsas por cima das reais da máquina, se houver: um teste nunca assina com elas.
os.environ.update({
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_EC2_METADATA_DISABLED": "true",
    "OPENAI_KEY_PARAMETER": "guardrails-openai-api-key",
})
for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN"):
    os.environ.pop(name, None)

# Só depois do os.environ acima: o import cria os clientes boto3.
from moderador import app


class NetworkBlocked(RuntimeError):
    """Um teste tentou abrir uma conexão de rede."""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Bloqueia DNS e conexão em todo teste. Tentativa de rede reprova o teste no fim."""
    attempts = []

    def block(name):
        def blocked(*args, **kwargs):
            attempts.append(name)
            raise NetworkBlocked(name)
        return blocked

    monkeypatch.setattr(socket, "getaddrinfo", block("getaddrinfo"))
    monkeypatch.setattr(socket.socket, "connect", block("connect"))
    yield attempts
    if attempts:
        pytest.fail("the test tried to access the network: " + ", ".join(attempts), pytrace=False)


@pytest.fixture
def comprehend():
    """Stubber no cliente do Comprehend. Chamada sem resposta cadastrada levanta erro."""
    with Stubber(app._comprehend) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def ssm(monkeypatch):
    """Stubber no cliente do SSM, com o cache da chave zerado (e restaurado no fim)."""
    monkeypatch.setattr(app, "_openai_key", None)
    with Stubber(app._ssm) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def openai(monkeypatch):
    """Moderation falsa no lugar do urlopen.

    Cada tentativa consome o primeiro item de fake.queue: exceção é levantada, bytes é o corpo
    cru, outro valor é o JSON da resposta. Fila vazia: {"results": [{"flagged": fake.flagged}]}.
    """
    fake = types.SimpleNamespace(flagged=False, queue=[], requests=[], timeouts=[])

    def urlopen(request, timeout):
        fake.requests.append(request)
        fake.timeouts.append(timeout)
        if not fake.queue:
            body = {"results": [{"flagged": fake.flagged}]}
            return io.BytesIO(json.dumps(body).encode("utf-8"))
        item = fake.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, bytes):
            return io.BytesIO(item)
        return io.BytesIO(json.dumps(item).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return fake
