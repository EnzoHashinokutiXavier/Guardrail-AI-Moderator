# Etapa 8.2: os três modos de moderar. Fixtures comprehend, ssm e openai no conftest.py (8.3).
import json

import pytest

from moderador import app

CHAVE = "sk-teste-falsa"


def ent(tipo, inicio, fim, score=0.99):
    """Uma entidade no formato que o DetectPiiEntities devolve."""
    return {"Type": tipo, "BeginOffset": inicio, "EndOffset": fim, "Score": score}


def trecho(texto, parte):
    """Offsets (início, fim exclusivo) de parte dentro de texto, em code points."""
    inicio = texto.index(parte)
    return inicio, inicio + len(parte)


def esperar_pii(stubber, texto, entidades):
    """O Comprehend tem de receber exatamente este texto, em inglês."""
    stubber.add_response(
        "detect_pii_entities",
        {"Entities": entidades},
        {"Text": texto, "LanguageCode": "en"},
    )


def esperar_chave(stubber):
    """Uma leitura da chave no SSM, com o nome do conftest e descriptografada."""
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "guardrails-openai-api-key", "Type": "SecureString", "Value": CHAVE}},
        {"Name": "guardrails-openai-api-key", "WithDecryption": True},
    )


def textos_enviados(openai):
    """O campo input de cada requisição que chegou à Moderation falsa."""
    return [json.loads(r.data)["input"] for r in openai.requisicoes]


# Modo 1: só Comprehend

def test_modo1_redige_pii(comprehend, ssm, openai):
    t = "My name is John Smith."
    esperar_pii(comprehend, t, [ent("NAME", *trecho(t, "John Smith"))])
    assert app.moderar(t, 1) == "My name is ***."
    assert openai.requisicoes == []


def test_modo1_nao_modera_conteudo(comprehend, ssm, openai):
    # Comportamento esperado do modo 1: ameaça sem PII volta sem censura.
    t = "I will hurt you."
    esperar_pii(comprehend, t, [])
    assert app.moderar(t, 1) == "I will hurt you."
    assert openai.requisicoes == []


# Modo 2: só OpenAI

def test_modo2_nao_sinalizado(comprehend, ssm, openai):
    # Comportamento esperado do modo 2: a PII volta, e vai inteira para a OpenAI.
    esperar_chave(ssm)
    t = "My name is John Smith."
    assert app.moderar(t, 2) == "My name is John Smith."
    assert textos_enviados(openai) == ["My name is John Smith."]


def test_modo2_sinalizado(comprehend, ssm, openai):
    esperar_chave(ssm)
    openai.flagged = True
    assert app.moderar("I will hurt you.", 2) == "***"
    assert textos_enviados(openai) == ["I will hurt you."]


@pytest.mark.parametrize("flagged", [None, 0, "false", "False"])
def test_modo2_so_false_exato_libera(comprehend, ssm, openai, flagged):
    esperar_chave(ssm)
    openai.flagged = flagged
    assert app.moderar("I will hurt you.", 2) == "***"


def test_modo2_requisicao_a_openai(comprehend, ssm, openai):
    esperar_chave(ssm)
    app.moderar("hello", 2)
    [requisicao] = openai.requisicoes
    assert requisicao.full_url == "https://api.openai.com/v1/moderations"
    assert requisicao.get_method() == "POST"
    assert requisicao.get_header("Authorization") == "Bearer " + CHAVE
    assert requisicao.get_header("Content-type") == "application/json"
    assert json.loads(requisicao.data) == {"model": "omni-moderation-latest", "input": "hello"}
    assert openai.timeouts == [3]


def test_chave_lida_uma_vez(comprehend, ssm, openai):
    # Uma resposta só no Stubber: uma segunda leitura do SSM levantaria erro.
    esperar_chave(ssm)
    app.moderar("hello", 2)
    app.moderar("hello again", 2)
    assert textos_enviados(openai) == ["hello", "hello again"]


# Modo 3: Comprehend, depois OpenAI com o texto já redigido

def test_modo3_nao_sinalizado(comprehend, ssm, openai):
    t = "My name is John Smith."
    esperar_pii(comprehend, t, [ent("NAME", *trecho(t, "John Smith"))])
    esperar_chave(ssm)
    assert app.moderar(t, 3) == "My name is ***."
    assert textos_enviados(openai) == ["My name is ***."]


def test_modo3_sinalizado(comprehend, ssm, openai):
    # O *** inteiro vence a redação: nem o texto redigido volta.
    t = "My name is John Smith and I will hurt you."
    esperar_pii(comprehend, t, [ent("NAME", *trecho(t, "John Smith"))])
    esperar_chave(ssm)
    openai.flagged = True
    assert app.moderar(t, 3) == "***"
    assert textos_enviados(openai) == ["My name is *** and I will hurt you."]


def test_modo3_sem_pii(comprehend, ssm, openai):
    t = "The weather is nice."
    esperar_pii(comprehend, t, [])
    esperar_chave(ssm)
    assert app.moderar(t, 3) == "The weather is nice."
    assert textos_enviados(openai) == ["The weather is nice."]


# Do handler à resposta

def test_handler_devolve_texto_censurado(comprehend, ssm, openai):
    t = "Caf\u00e9 \u2615 \u2014 my name is John Smith"
    esperar_pii(comprehend, t, [ent("NAME", *trecho(t, "John Smith"))])
    esperar_chave(ssm)
    evento = {
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"texto": t, "modo": 3}),
    }
    resposta = app.handler(evento, None)
    assert resposta["statusCode"] == 200
    assert json.loads(resposta["body"]) == {"texto_censurado": "Caf\u00e9 \u2615 \u2014 my name is ***"}
    assert "Caf\u00e9 \u2615" in resposta["body"]  # ensure_ascii=False: sem \u00e9 no corpo
