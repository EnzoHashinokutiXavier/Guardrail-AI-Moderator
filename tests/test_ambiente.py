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


def test_importa_as_duas_lambdas():
    assert callable(app.handler)
    assert callable(kill_switch.handler)


def test_clientes_na_regiao_do_projeto():
    assert app._comprehend.meta.region_name == "us-east-1"
    assert app._ssm.meta.region_name == "us-east-1"


def test_erros_reais_do_botocore_viram_502():
    # Fecha o "NÃO validado" da 7.6: a hierarquia real do botocore.
    for classe in (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError):
        assert issubclass(classe, BotoCoreError)
        assert issubclass(classe, app.FALHAS)
    assert not issubclass(ClientError, BotoCoreError)
    assert issubclass(ClientError, app.FALHAS)


def test_rede_bloqueada(sem_rede):
    with pytest.raises(RuntimeError):
        urllib.request.urlopen(app.URL_OPENAI, timeout=1)
    assert sem_rede == ["getaddrinfo"]
    # A tentativa foi de propósito: limpa para o sem_rede não reprovar este teste.
    sem_rede.clear()
