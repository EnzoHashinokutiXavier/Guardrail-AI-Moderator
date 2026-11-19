# Etapa 8.3: validação da requisição (validate e o 400 do handler). Nenhum serviço é chamado.
import json

import pytest

from moderador import app

JSON = {"Content-Type": "application/json"}


def event(body, headers=JSON):
    """Evento no formato do AWS_PROXY. dict vira JSON; str e None vão como estão."""
    if isinstance(body, dict):
        body = json.dumps(body)
    return {"headers": headers, "body": body}


# Requisições válidas: a validação não pode recusar o que o contrato aceita

@pytest.mark.parametrize("mode", [1, 2, 3])
def test_accepts_the_three_modes(mode):
    assert app.validate(event({"text": "hi", "mode": mode})) == ("hi", mode)


@pytest.mark.parametrize("text", [
    pytest.param("a", id="1-character"),
    pytest.param("a" * 1000, id="1000-characters"),
    pytest.param("   ", id="only-spaces"),
    pytest.param("é" * 1000, id="1000-accents"),
    pytest.param("\U0001F600" * 1000, id="1000-emojis"),
    pytest.param("Café ☕ — my name is John Smith", id="accent-and-emoji"),
])
def test_text_accepted(text):
    # len conta code points, não bytes; o texto volta sem strip nem normalização.
    assert app.validate(event({"text": text, "mode": 1})) == (text, 1)


def test_extra_field_ignored():
    # Igual ao validator do API Gateway (sem additionalProperties: false).
    assert app.validate(event({"text": "hi", "mode": 1, "x": 2})) == ("hi", 1)


# Item 1: texto vazio, ausente e acima de 1.000 caracteres

@pytest.mark.parametrize("body", [
    pytest.param({"mode": 1}, id="missing"),
    pytest.param({"text": "", "mode": 1}, id="empty"),
    pytest.param({"text": "a" * 1001, "mode": 1}, id="1001-characters"),
    pytest.param({"text": "é" * 1001, "mode": 1}, id="1001-accents"),
    pytest.param({"text": None, "mode": 1}, id="null"),
    pytest.param({"text": 123, "mode": 1}, id="number"),
    pytest.param({"text": ["hi"], "mode": 1}, id="list"),
    pytest.param({"text": "\ud800", "mode": 1}, id="lone-surrogate"),
])
def test_invalid_text(body):
    assert app.validate(event(body)) is None


# Item 2: mode como "1", 1.0 e true

@pytest.mark.parametrize("mode", [
    pytest.param("1", id="string"),
    pytest.param(1.0, id="float"),       # 1.0 == 1 em Python
    pytest.param(True, id="true"),       # True == 1, e bool é subclasse de int
    pytest.param(False, id="false"),
    pytest.param(0, id="zero"),
    pytest.param(4, id="four"),
    pytest.param(-1, id="negative"),
    pytest.param(None, id="null"),
    pytest.param([1], id="list"),
    pytest.param(float("nan"), id="nan"),  # o json.loads aceita NaN
])
def test_invalid_mode(mode):
    assert app.validate(event({"text": "hi", "mode": mode})) is None


def test_missing_mode():
    assert app.validate(event({"text": "hi"})) is None


@pytest.mark.parametrize("body", [
    pytest.param('{"text": "hi", "mode": 1e0}', id="1e0"),          # notação científica: float
    pytest.param('{"text": "hi", "mode": 1e400}', id="1e400"),      # vira inf
    pytest.param('{"text": "hi", "mode": Infinity}', id="Infinity"),
])
def test_invalid_mode_in_raw_json(body):
    assert app.validate(event(body)) is None


def test_old_portuguese_fields_rejected():
    # Os nomes anteriores à troca para inglês não são mais aceitos.
    assert app.validate(event({"texto": "hi", "modo": 1})) is None


# Item 3: JSON malformado e Content-Type errado

@pytest.mark.parametrize("body", [
    pytest.param('{"text": ', id="truncated"),
    pytest.param("", id="empty-body"),
    pytest.param("not json", id="plain-text"),
    pytest.param("{'text': 'hi', 'mode': 1}", id="single-quotes"),
    pytest.param('{"text": "hi", "mode": 1,}', id="trailing-comma"),
    pytest.param(None, id="no-body"),
    pytest.param("[1, 2]", id="list"),             # JSON válido, mas não é objeto
    pytest.param('"hi"', id="string"),
    pytest.param("5", id="number"),
    pytest.param("null", id="null"),
    pytest.param("[" * 100000 + "]" * 100000, id="nested"),  # RecursionError, não ValueError
])
def test_invalid_json(body):
    assert app.validate(event(body)) is None


@pytest.mark.parametrize("headers", [
    pytest.param({"Content-Type": "application/json"}, id="default"),
    pytest.param({"content-type": "application/json"}, id="lowercase-name"),
    pytest.param({"CONTENT-TYPE": "Application/JSON"}, id="uppercase"),
    pytest.param({"Content-Type": "application/json; charset=utf-8"}, id="charset"),
    pytest.param({"Content-Type": " application/json "}, id="spaces"),
    pytest.param({"Content-Type": "application/json", "X-Api-Key": "anything"}, id="other-headers"),
])
def test_content_type_accepted(headers):
    assert app.validate(event({"text": "hi", "mode": 1}, headers)) == ("hi", 1)


@pytest.mark.parametrize("headers", [
    pytest.param({"Content-Type": "text/plain"}, id="text-plain"),
    pytest.param({"Content-Type": "application/jsonx"}, id="prefix"),       # comparação exata
    pytest.param({"Content-Type": "text/plain; x=application/json"}, id="contains"),
    pytest.param({"Content-Type": "application/x-www-form-urlencoded"}, id="form"),
    pytest.param({"Content-Type": ""}, id="empty-value"),
    pytest.param({"Content-Type": None}, id="null-value"),
    pytest.param({"X-Api-Key": "anything"}, id="missing"),
    pytest.param({}, id="no-headers"),
    pytest.param(None, id="null-headers"),  # botão Test do Console da Lambda
])
def test_content_type_rejected(headers):
    # O corpo é válido: quem recusa é o Content-Type.
    assert app.validate(event({"text": "hi", "mode": 1}, headers)) is None


# Pelo handler: o 400, o log e nenhum serviço chamado

@pytest.mark.parametrize("ev", [
    pytest.param(event({"text": "", "mode": 1}), id="empty-text"),
    pytest.param(event({"text": "a" * 1001, "mode": 3}), id="1001-characters"),
    pytest.param(event({"text": "hi", "mode": "1"}), id="mode-string"),
    pytest.param(event({"text": "hi", "mode": True}), id="mode-true"),
    pytest.param(event('{"text": '), id="malformed-json"),
    pytest.param(event({"text": "hi", "mode": 2}, {"Content-Type": "text/plain"}), id="text-plain"),
])
def test_handler_returns_400(comprehend, ssm, openai, ev):
    response = app.handler(ev, None)
    assert response["statusCode"] == 400
    assert response["headers"] == {"Content-Type": "application/json"}
    # Exato: espaço depois dos dois-pontos (formato da Lambda).
    assert response["body"] == '{"error": "invalid request"}'
    assert openai.requests == []


def test_handler_400_logs_only_status(comprehend, ssm, openai, capsys):
    # O que veio na requisição inválida não vai para o log.
    app.handler(event({"text": "My name is John Smith", "mode": "1"}), None)
    [line] = capsys.readouterr().out.splitlines()
    record = json.loads(line)
    assert set(record) == {"status", "mode", "length", "ms"}
    assert record["status"] == 400
    assert record["mode"] is None
    assert record["length"] is None
    assert "John" not in line
