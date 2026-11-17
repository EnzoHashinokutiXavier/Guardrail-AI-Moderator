# Configuração comum dos testes (etapas 8.0 e 8.3). Nenhum teste fala com a AWS nem com a OpenAI.
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
    "PARAMETRO_OPENAI": "guardrails-openai-api-key",
})
for nome in ("AWS_PROFILE", "AWS_SESSION_TOKEN"):
    os.environ.pop(nome, None)

# Só depois do os.environ acima: o import cria os clientes boto3.
from moderador import app


class RedeBloqueada(RuntimeError):
    """Um teste tentou abrir uma conexão de rede."""


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    """Bloqueia DNS e conexão em todo teste. Tentativa de rede reprova o teste no fim."""
    tentativas = []

    def bloqueio(nome):
        def bloqueado(*args, **kwargs):
            tentativas.append(nome)
            raise RedeBloqueada(nome)
        return bloqueado

    monkeypatch.setattr(socket, "getaddrinfo", bloqueio("getaddrinfo"))
    monkeypatch.setattr(socket.socket, "connect", bloqueio("connect"))
    yield tentativas
    if tentativas:
        pytest.fail("o teste tentou acessar a rede: " + ", ".join(tentativas), pytrace=False)


@pytest.fixture
def comprehend():
    """Stubber no cliente do Comprehend. Chamada sem resposta cadastrada levanta erro."""
    with Stubber(app._comprehend) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def ssm(monkeypatch):
    """Stubber no cliente do SSM, com o cache da chave zerado (e restaurado no fim)."""
    monkeypatch.setattr(app, "_chave_openai", None)
    with Stubber(app._ssm) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def openai(monkeypatch):
    """Moderation falsa no lugar do urlopen. falsa.flagged decide a resposta."""
    falsa = types.SimpleNamespace(flagged=False, requisicoes=[], timeouts=[])

    def urlopen(requisicao, timeout):
        falsa.requisicoes.append(requisicao)
        falsa.timeouts.append(timeout)
        corpo = {"results": [{"flagged": falsa.flagged}]}
        return io.BytesIO(json.dumps(corpo).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return falsa
