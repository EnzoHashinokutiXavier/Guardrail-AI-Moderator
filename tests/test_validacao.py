# Etapa 8.3: validação da requisição (validar e o 400 do handler). Nenhum serviço é chamado.
import json

import pytest

from moderador import app

JSON = {"Content-Type": "application/json"}


def evento(corpo, headers=JSON):
    """Evento no formato do AWS_PROXY. dict vira JSON; str e None vão como estão."""
    if isinstance(corpo, dict):
        corpo = json.dumps(corpo)
    return {"headers": headers, "body": corpo}


# Requisições válidas: a validação não pode recusar o que o contrato aceita

@pytest.mark.parametrize("modo", [1, 2, 3])
def test_aceita_os_tres_modos(modo):
    assert app.validar(evento({"texto": "hi", "modo": modo})) == ("hi", modo)


@pytest.mark.parametrize("texto", [
    pytest.param("a", id="1-caractere"),
    pytest.param("a" * 1000, id="1000-caracteres"),
    pytest.param("   ", id="so-espacos"),
    pytest.param("é" * 1000, id="1000-acentos"),
    pytest.param("\U0001F600" * 1000, id="1000-emojis"),
    pytest.param("Café ☕ — my name is John Smith", id="acento-e-emoji"),
])
def test_texto_aceito(texto):
    # len conta code points, não bytes; o texto volta sem strip nem normalização.
    assert app.validar(evento({"texto": texto, "modo": 1})) == (texto, 1)


def test_campo_extra_ignorado():
    # Igual ao validator do API Gateway (sem additionalProperties: false).
    assert app.validar(evento({"texto": "hi", "modo": 1, "x": 2})) == ("hi", 1)


# Item 1: texto vazio, ausente e acima de 1.000 caracteres

@pytest.mark.parametrize("corpo", [
    pytest.param({"modo": 1}, id="ausente"),
    pytest.param({"texto": "", "modo": 1}, id="vazio"),
    pytest.param({"texto": "a" * 1001, "modo": 1}, id="1001-caracteres"),
    pytest.param({"texto": "é" * 1001, "modo": 1}, id="1001-acentos"),
    pytest.param({"texto": None, "modo": 1}, id="null"),
    pytest.param({"texto": 123, "modo": 1}, id="numero"),
    pytest.param({"texto": ["hi"], "modo": 1}, id="lista"),
    pytest.param({"texto": "\ud800", "modo": 1}, id="surrogate-solto"),
])
def test_texto_invalido(corpo):
    assert app.validar(evento(corpo)) is None


# Item 2: modo como "1", 1.0 e true

@pytest.mark.parametrize("modo", [
    pytest.param("1", id="string"),
    pytest.param(1.0, id="float"),       # 1.0 == 1 em Python
    pytest.param(True, id="true"),       # True == 1, e bool é subclasse de int
    pytest.param(False, id="false"),
    pytest.param(0, id="zero"),
    pytest.param(4, id="quatro"),
    pytest.param(-1, id="negativo"),
    pytest.param(None, id="null"),
    pytest.param([1], id="lista"),
    pytest.param(float("nan"), id="nan"),  # o json.loads aceita NaN
])
def test_modo_invalido(modo):
    assert app.validar(evento({"texto": "hi", "modo": modo})) is None


def test_modo_ausente():
    assert app.validar(evento({"texto": "hi"})) is None


@pytest.mark.parametrize("corpo", [
    pytest.param('{"texto": "hi", "modo": 1e0}', id="1e0"),          # notação científica: float
    pytest.param('{"texto": "hi", "modo": 1e400}', id="1e400"),      # vira inf
    pytest.param('{"texto": "hi", "modo": Infinity}', id="Infinity"),
])
def test_modo_invalido_no_json_cru(corpo):
    assert app.validar(evento(corpo)) is None


# Item 3: JSON malformado e Content-Type errado

@pytest.mark.parametrize("corpo", [
    pytest.param('{"texto": ', id="cortado"),
    pytest.param("", id="corpo-vazio"),
    pytest.param("not json", id="texto-solto"),
    pytest.param("{'texto': 'hi', 'modo': 1}", id="aspas-simples"),
    pytest.param('{"texto": "hi", "modo": 1,}', id="virgula-sobrando"),
    pytest.param(None, id="sem-corpo"),
    pytest.param("[1, 2]", id="lista"),             # JSON válido, mas não é objeto
    pytest.param('"hi"', id="string"),
    pytest.param("5", id="numero"),
    pytest.param("null", id="null"),
    pytest.param("[" * 100000 + "]" * 100000, id="aninhado"),  # RecursionError, não ValueError
])
def test_json_invalido(corpo):
    assert app.validar(evento(corpo)) is None


@pytest.mark.parametrize("headers", [
    pytest.param({"Content-Type": "application/json"}, id="padrao"),
    pytest.param({"content-type": "application/json"}, id="nome-minusculo"),
    pytest.param({"CONTENT-TYPE": "Application/JSON"}, id="maiusculas"),
    pytest.param({"Content-Type": "application/json; charset=utf-8"}, id="charset"),
    pytest.param({"Content-Type": " application/json "}, id="espacos"),
    pytest.param({"Content-Type": "application/json", "X-Api-Key": "qualquer"}, id="outros-headers"),
])
def test_content_type_aceito(headers):
    assert app.validar(evento({"texto": "hi", "modo": 1}, headers)) == ("hi", 1)


@pytest.mark.parametrize("headers", [
    pytest.param({"Content-Type": "text/plain"}, id="text-plain"),
    pytest.param({"Content-Type": "application/jsonx"}, id="prefixo"),       # comparação exata
    pytest.param({"Content-Type": "text/plain; x=application/json"}, id="contem"),
    pytest.param({"Content-Type": "application/x-www-form-urlencoded"}, id="formulario"),
    pytest.param({"Content-Type": ""}, id="valor-vazio"),
    pytest.param({"Content-Type": None}, id="valor-null"),
    pytest.param({"X-Api-Key": "qualquer"}, id="ausente"),
    pytest.param({}, id="sem-headers"),
    pytest.param(None, id="headers-null"),  # botão Test do Console da Lambda
])
def test_content_type_recusado(headers):
    # O corpo é válido: quem recusa é o Content-Type.
    assert app.validar(evento({"texto": "hi", "modo": 1}, headers)) is None


# Pelo handler: o 400, o log e nenhum serviço chamado

@pytest.mark.parametrize("ev", [
    pytest.param(evento({"texto": "", "modo": 1}), id="texto-vazio"),
    pytest.param(evento({"texto": "a" * 1001, "modo": 3}), id="1001-caracteres"),
    pytest.param(evento({"texto": "hi", "modo": "1"}), id="modo-string"),
    pytest.param(evento({"texto": "hi", "modo": True}), id="modo-true"),
    pytest.param(evento('{"texto": '), id="json-malformado"),
    pytest.param(evento({"texto": "hi", "modo": 2}, {"Content-Type": "text/plain"}), id="text-plain"),
])
def test_handler_devolve_400(comprehend, ssm, openai, ev):
    resposta = app.handler(ev, None)
    assert resposta["statusCode"] == 400
    assert resposta["headers"] == {"Content-Type": "application/json"}
    # Exato: espaço depois dos dois-pontos (formato da Lambda) e acento como caractere.
    assert resposta["body"] == '{"erro": "requisição inválida"}'
    assert openai.requisicoes == []


def test_handler_400_loga_so_o_status(comprehend, ssm, openai, capsys):
    # O que veio na requisição inválida não vai para o log.
    app.handler(evento({"texto": "My name is John Smith", "modo": "1"}), None)
    [linha] = capsys.readouterr().out.splitlines()
    registro = json.loads(linha)
    assert set(registro) == {"status", "modo", "tamanho", "ms"}
    assert registro["status"] == 400
    assert registro["modo"] is None
    assert registro["tamanho"] is None
    assert "John" not in linha
