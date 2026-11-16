# Configuração comum dos testes (etapa 8.0). Nenhum teste fala com a AWS nem com a OpenAI.
import os
import socket

import pytest

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
