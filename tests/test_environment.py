# Etapa 8.0: confere a configuração dos testes. A lógica é testada da 8.1 à 8.5.
import urllib.request

import pytest
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from kill_switch import app as kill_switch
from moderador import app


def test_imports_both_lambdas():
    assert callable(app.handler)
    assert callable(kill_switch.handler)


def test_clients_in_project_region():
    assert app._comprehend.meta.region_name == "us-east-1"
    assert app._ssm.meta.region_name == "us-east-1"


def test_real_botocore_errors_become_502():
    # Fecha o "NÃO validado" da 7.6: a hierarquia real do botocore.
    for cls in (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError):
        assert issubclass(cls, BotoCoreError)
        assert issubclass(cls, app.FAILURES)
    assert not issubclass(ClientError, BotoCoreError)
    assert issubclass(ClientError, app.FAILURES)


def test_network_blocked(no_network):
    with pytest.raises(RuntimeError):
        urllib.request.urlopen(app.OPENAI_URL, timeout=1)
    assert no_network == ["getaddrinfo"]
    # A tentativa foi de propósito: limpa para o no_network não reprovar este teste.
    no_network.clear()
