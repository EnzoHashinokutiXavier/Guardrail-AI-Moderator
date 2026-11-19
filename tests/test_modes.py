# Etapa 8.2: os três modos de moderate. Fixtures comprehend, ssm e openai no conftest.py (8.3).
import json

import pytest

from moderador import app

KEY = "sk-test-fake"


def ent(entity_type, start, end, score=0.99):
    """Uma entidade no formato que o DetectPiiEntities devolve."""
    return {"Type": entity_type, "BeginOffset": start, "EndOffset": end, "Score": score}


def span(text, part):
    """Offsets (início, fim exclusivo) de part dentro de text, em code points."""
    start = text.index(part)
    return start, start + len(part)


def expect_pii(stubber, text, entities):
    """O Comprehend tem de receber exatamente este texto, em inglês."""
    stubber.add_response(
        "detect_pii_entities",
        {"Entities": entities},
        {"Text": text, "LanguageCode": "en"},
    )


def expect_key(stubber):
    """Uma leitura da chave no SSM, com o nome do conftest e descriptografada."""
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "guardrails-openai-api-key", "Type": "SecureString", "Value": KEY}},
        {"Name": "guardrails-openai-api-key", "WithDecryption": True},
    )


def sent_texts(openai):
    """O campo input de cada requisição que chegou à Moderation falsa."""
    return [json.loads(r.data)["input"] for r in openai.requests]


# Modo 1: só Comprehend

def test_mode1_redacts_pii(comprehend, ssm, openai):
    t = "My name is John Smith."
    expect_pii(comprehend, t, [ent("NAME", *span(t, "John Smith"))])
    assert app.moderate(t, 1) == "My name is ***."
    assert openai.requests == []


def test_mode1_does_not_moderate_content(comprehend, ssm, openai):
    # Comportamento esperado do modo 1: ameaça sem PII volta sem censura.
    t = "I will hurt you."
    expect_pii(comprehend, t, [])
    assert app.moderate(t, 1) == "I will hurt you."
    assert openai.requests == []


# Modo 2: só OpenAI

def test_mode2_not_flagged(comprehend, ssm, openai):
    # Comportamento esperado do modo 2: a PII volta, e vai inteira para a OpenAI.
    expect_key(ssm)
    t = "My name is John Smith."
    assert app.moderate(t, 2) == "My name is John Smith."
    assert sent_texts(openai) == ["My name is John Smith."]


def test_mode2_flagged(comprehend, ssm, openai):
    expect_key(ssm)
    openai.flagged = True
    assert app.moderate("I will hurt you.", 2) == "***"
    assert sent_texts(openai) == ["I will hurt you."]


@pytest.mark.parametrize("flagged", [None, 0, "false", "False"])
def test_mode2_only_exact_false_passes(comprehend, ssm, openai, flagged):
    expect_key(ssm)
    openai.flagged = flagged
    assert app.moderate("I will hurt you.", 2) == "***"


def test_mode2_openai_request(comprehend, ssm, openai):
    expect_key(ssm)
    app.moderate("hello", 2)
    [request] = openai.requests
    assert request.full_url == "https://api.openai.com/v1/moderations"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer " + KEY
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"model": "omni-moderation-latest", "input": "hello"}
    assert openai.timeouts == [3]


def test_key_read_once(comprehend, ssm, openai):
    # Uma resposta só no Stubber: uma segunda leitura do SSM levantaria erro.
    expect_key(ssm)
    app.moderate("hello", 2)
    app.moderate("hello again", 2)
    assert sent_texts(openai) == ["hello", "hello again"]


# Modo 3: Comprehend, depois OpenAI com o texto já redigido

def test_mode3_not_flagged(comprehend, ssm, openai):
    t = "My name is John Smith."
    expect_pii(comprehend, t, [ent("NAME", *span(t, "John Smith"))])
    expect_key(ssm)
    assert app.moderate(t, 3) == "My name is ***."
    assert sent_texts(openai) == ["My name is ***."]


def test_mode3_flagged(comprehend, ssm, openai):
    # O *** inteiro vence a redação: nem o texto redigido volta.
    t = "My name is John Smith and I will hurt you."
    expect_pii(comprehend, t, [ent("NAME", *span(t, "John Smith"))])
    expect_key(ssm)
    openai.flagged = True
    assert app.moderate(t, 3) == "***"
    assert sent_texts(openai) == ["My name is *** and I will hurt you."]


def test_mode3_no_pii(comprehend, ssm, openai):
    t = "The weather is nice."
    expect_pii(comprehend, t, [])
    expect_key(ssm)
    assert app.moderate(t, 3) == "The weather is nice."
    assert sent_texts(openai) == ["The weather is nice."]


# Do handler à resposta

def test_handler_returns_censored_text(comprehend, ssm, openai):
    t = "Café ☕ — my name is John Smith"
    expect_pii(comprehend, t, [ent("NAME", *span(t, "John Smith"))])
    expect_key(ssm)
    event = {
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"text": t, "mode": 3}),
    }
    response = app.handler(event, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"censored_text": "Café ☕ — my name is ***"}
    assert "Café ☕" in response["body"]  # ensure_ascii=False: sem é no corpo
