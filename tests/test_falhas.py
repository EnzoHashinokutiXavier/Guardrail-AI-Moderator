# Etapa 8.4: fail closed. Falha ou timeout de Comprehend, SSM ou OpenAI vira 502, sem texto.
import http.client
import json
import types
import urllib.error

import pytest
from botocore.exceptions import ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError

from moderador import app

CHAVE = "sk-teste-falsa"
URL = "https://api.openai.com/v1/moderations"
T = "My name is John Smith."
REDIGIDO = "My name is ***."
NOME = [{"Type": "NAME", "BeginOffset": 11, "EndOffset": 21, "Score": 0.99}]
LIMPO = {"results": [{"flagged": False}]}
SINALIZADO = {"results": [{"flagged": True}]}
CORPO_502 = '{"erro": "serviço de moderação indisponível"}'


def evento(texto, modo):
    return {
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"texto": texto, "modo": modo}),
    }


def chamar(texto, modo, capsys):
    """Chama o handler. Devolve a resposta e a linha de log (tem de ser exatamente uma)."""
    resposta = app.handler(evento(texto, modo), None)
    [linha] = capsys.readouterr().out.splitlines()
    return resposta, linha


def conferir_502(resposta, linha, modo, falha, codigo=None):
    """502 da Lambda com o corpo exato, e o log com a falha certa e só metadados."""
    assert resposta["statusCode"] == 502
    assert resposta["headers"] == {"Content-Type": "application/json"}
    assert resposta["body"] == CORPO_502
    registro = json.loads(linha)
    assert type(registro.pop("ms")) is int
    assert registro == {"status": 502, "modo": modo, "tamanho": 22, "falha": falha, "codigo": codigo}


def esperar_pii(comprehend):
    comprehend.add_response("detect_pii_entities", {"Entities": NOME}, {"Text": T, "LanguageCode": "en"})


def erro_http(status):
    return urllib.error.HTTPError(URL, status, "erro simulado", {}, None)


def textos_enviados(openai):
    return [json.loads(r.data)["input"] for r in openai.requisicoes]


@pytest.fixture
def chave_em_cache(ssm, monkeypatch):
    """Container quente: a chave já foi lida. O Stubber do SSM fica vazio (nenhuma leitura)."""
    monkeypatch.setattr(app, "_chave_openai", CHAVE)


@pytest.fixture
def comprehend_sem_rede():
    """Cada tentativa de envio do Comprehend levanta falso.erro, dentro do botocore real (com o retry)."""
    falso = types.SimpleNamespace(erro=None, tentativas=0)

    def antes_de_enviar(request, **kwargs):
        falso.tentativas += 1
        raise falso.erro(endpoint_url=request.url)

    eventos = app._comprehend.meta.events
    eventos.register("before-send.comprehend.DetectPiiEntities", antes_de_enviar, unique_id="teste-8.4")
    yield falso
    eventos.unregister("before-send.comprehend.DetectPiiEntities", unique_id="teste-8.4")


# Item 1: fail closed com erro do Comprehend (modos 1 e 3)

# Código modelado pelo serviço vira uma subclasse de ClientError com o mesmo nome; o resto fica ClientError.
@pytest.mark.parametrize("modo", [1, 3])
@pytest.mark.parametrize("codigo, falha", [
    ("TooManyRequestsException", "TooManyRequestsException"),
    ("InternalServerException", "InternalServerException"),
    ("UnsupportedLanguageException", "UnsupportedLanguageException"),
    ("AccessDeniedException", "ClientError"),  # não modelado no Comprehend
])
def test_comprehend_erro_do_servico(comprehend, ssm, openai, capsys, modo, codigo, falha):
    comprehend.add_client_error("detect_pii_entities", codigo, expected_params={"Text": T, "LanguageCode": "en"})
    resposta, linha = chamar(T, modo, capsys)
    conferir_502(resposta, linha, modo, falha, codigo)
    assert openai.requisicoes == []


@pytest.mark.parametrize("modo", [1, 3])
@pytest.mark.parametrize("erro, nome", [
    pytest.param(ReadTimeoutError, "ReadTimeoutError", id="timeout-leitura"),
    pytest.param(ConnectTimeoutError, "ConnectTimeoutError", id="timeout-conexao"),
    pytest.param(EndpointConnectionError, "EndpointConnectionError", id="sem-conexao"),
])
def test_comprehend_timeout(comprehend_sem_rede, ssm, openai, capsys, modo, erro, nome):
    comprehend_sem_rede.erro = erro
    resposta, linha = chamar(T, modo, capsys)
    conferir_502(resposta, linha, modo, nome)
    assert comprehend_sem_rede.tentativas == 2  # total_max_attempts: 1 chamada + 1 retry
    assert openai.requisicoes == []


def test_comprehend_resposta_fora_do_formato(comprehend, ssm, openai, capsys):
    # Entidade sem "Score": o filtro do redigir levanta KeyError.
    entidade = {"Type": "NAME", "BeginOffset": 11, "EndOffset": 21}
    comprehend.add_response("detect_pii_entities", {"Entities": [entidade]}, {"Text": T, "LanguageCode": "en"})
    resposta, linha = chamar(T, 1, capsys)
    conferir_502(resposta, linha, 1, "KeyError")


def test_comprehend_mensagem_do_erro_nao_vaza(comprehend, ssm, openai, capsys):
    # A mensagem do serviço cita o texto: nem a resposta nem o log podem repeti-la.
    comprehend.add_client_error("detect_pii_entities", "InvalidRequestException", "texto recusado: " + T)
    resposta, linha = chamar(T, 3, capsys)
    conferir_502(resposta, linha, 3, "InvalidRequestException", "InvalidRequestException")
    for parte in ("John", "recusado"):
        assert parte not in resposta["body"]
        assert parte not in linha


# Item 2: fail closed com erro/timeout da OpenAI (modos 2 e 3)

@pytest.mark.parametrize("modo", [2, 3])
@pytest.mark.parametrize("falha, nome, codigo", [
    pytest.param(lambda: TimeoutError("timed out"), "TimeoutError", None, id="timeout"),
    pytest.param(lambda: urllib.error.URLError("sem rede"), "URLError", None, id="sem-conexao"),
    pytest.param(lambda: http.client.RemoteDisconnected("fechou"), "RemoteDisconnected", None, id="conexao-caiu"),
    pytest.param(lambda: erro_http(500), "HTTPError", 500, id="500"),
    pytest.param(lambda: erro_http(502), "HTTPError", 502, id="502"),
    pytest.param(lambda: erro_http(503), "HTTPError", 503, id="503"),
    pytest.param(lambda: erro_http(504), "HTTPError", 504, id="504"),
])
def test_openai_falha_nas_duas_tentativas(comprehend, chave_em_cache, openai, capsys, modo, falha, nome, codigo):
    if modo == 3:
        esperar_pii(comprehend)
    # Uma terceira tentativa cairia na fila vazia, que responde "não sinalizado": daria 200.
    openai.fila = [falha(), falha()]
    resposta, linha = chamar(T, modo, capsys)
    conferir_502(resposta, linha, modo, nome, codigo)
    assert len(openai.requisicoes) == 2
    assert openai.fila == []


@pytest.mark.parametrize("modo", [2, 3])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
def test_openai_4xx_sem_retry(comprehend, chave_em_cache, openai, capsys, modo, status):
    if modo == 3:
        esperar_pii(comprehend)
    openai.fila = [erro_http(status)]
    resposta, linha = chamar(T, modo, capsys)
    conferir_502(resposta, linha, modo, "HTTPError", status)
    assert len(openai.requisicoes) == 1


@pytest.mark.parametrize("resposta_openai, nome", [
    pytest.param(b"<html>erro</html>", "JSONDecodeError", id="html"),
    pytest.param(b"", "JSONDecodeError", id="corpo-vazio"),
    pytest.param(http.client.IncompleteRead(b""), "IncompleteRead", id="corpo-cortado"),
    pytest.param({"error": {"message": "x"}}, "KeyError", id="sem-results"),
    pytest.param({"results": []}, "IndexError", id="results-vazio"),
    pytest.param({"results": [{}]}, "KeyError", id="sem-flagged"),
    pytest.param({"results": None}, "TypeError", id="results-null"),
    pytest.param(None, "TypeError", id="null"),
    pytest.param([], "TypeError", id="lista"),
])
def test_openai_resposta_invalida_sem_retry(comprehend, chave_em_cache, openai, capsys, resposta_openai, nome):
    openai.fila = [resposta_openai]
    resposta, linha = chamar(T, 2, capsys)
    conferir_502(resposta, linha, 2, nome)
    assert len(openai.requisicoes) == 1


def test_modo3_openai_falha_nao_devolve_o_redigido(comprehend, chave_em_cache, openai, capsys):
    # O Comprehend redigiu e a OpenAI recebeu o redigido duas vezes; mesmo assim, nada volta.
    esperar_pii(comprehend)
    openai.fila = [TimeoutError("1"), TimeoutError("2")]
    resposta, linha = chamar(T, 3, capsys)
    conferir_502(resposta, linha, 3, "TimeoutError")
    assert textos_enviados(openai) == [REDIGIDO, REDIGIDO]
    assert "***" not in resposta["body"]


@pytest.mark.parametrize("falha", [
    pytest.param(lambda: TimeoutError("timed out"), id="timeout"),
    pytest.param(lambda: urllib.error.URLError("sem rede"), id="sem-conexao"),
    pytest.param(lambda: erro_http(503), id="503"),
])
def test_openai_retry_que_da_certo(comprehend, chave_em_cache, openai, capsys, falha):
    openai.fila = [falha(), LIMPO]
    resposta, linha = chamar(T, 2, capsys)
    assert resposta["statusCode"] == 200
    assert json.loads(resposta["body"]) == {"texto_censurado": T}
    assert len(openai.requisicoes) == 2
    assert set(json.loads(linha)) == {"status", "modo", "tamanho", "ms"}


def test_openai_retry_usa_a_segunda_resposta(comprehend, chave_em_cache, openai, capsys):
    # A segunda tentativa diz "sinalizado": vale ela, não o texto original.
    openai.fila = [TimeoutError("timed out"), SINALIZADO]
    resposta, linha = chamar(T, 2, capsys)
    assert resposta["statusCode"] == 200
    assert resposta["body"] == '{"texto_censurado": "***"}'
    [primeira, segunda] = openai.requisicoes
    assert primeira.data == segunda.data


# Complemento: sem a chave do SSM não há OpenAI

@pytest.mark.parametrize("modo", [2, 3])
@pytest.mark.parametrize("codigo, falha", [
    ("AccessDeniedException", "AccessDeniedException"),  # modelado no botocore 1.44.2 (na Lambda, 7.6: ClientError)
    ("ParameterNotFound", "ParameterNotFound"),
    ("ThrottlingException", "ThrottlingException"),
    ("UnrecognizedClientException", "ClientError"),  # não modelado no SSM
])
def test_ssm_falha(comprehend, ssm, openai, capsys, modo, codigo, falha):
    if modo == 3:
        esperar_pii(comprehend)
    ssm.add_client_error(
        "get_parameter", codigo,
        expected_params={"Name": "guardrails-openai-api-key", "WithDecryption": True},
    )
    resposta, linha = chamar(T, modo, capsys)
    conferir_502(resposta, linha, modo, falha, codigo)
    assert openai.requisicoes == []
    assert app._chave_openai is None


def test_ssm_falha_nao_fica_no_cache(comprehend, ssm, openai, capsys):
    # Depois de uma falha, a requisição seguinte lê o SSM de novo e funciona.
    ssm.add_client_error("get_parameter", "ThrottlingException")
    ssm.add_response(
        "get_parameter",
        {"Parameter": {"Name": "guardrails-openai-api-key", "Type": "SecureString", "Value": CHAVE}},
        {"Name": "guardrails-openai-api-key", "WithDecryption": True},
    )
    resposta, linha = chamar(T, 2, capsys)
    conferir_502(resposta, linha, 2, "ThrottlingException", "ThrottlingException")
    resposta, linha = chamar(T, 2, capsys)
    assert resposta["statusCode"] == 200
    assert len(openai.requisicoes) == 1


def test_sem_variavel_do_parametro(comprehend, ssm, openai, capsys, monkeypatch):
    monkeypatch.delenv("PARAMETRO_OPENAI")
    resposta, linha = chamar(T, 2, capsys)
    conferir_502(resposta, linha, 2, "KeyError")
    assert openai.requisicoes == []


# Erro fora de FALHAS é defeito de código: sobe (5xx do API Gateway), não vira 502 nem 200

def test_erro_fora_da_lista_sobe(comprehend, chave_em_cache, openai, capsys):
    openai.fila = [RuntimeError("defeito simulado")]
    with pytest.raises(RuntimeError):
        app.handler(evento(T, 2), None)
    assert capsys.readouterr().out == ""
    assert len(openai.requisicoes) == 1
