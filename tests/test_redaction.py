# Etapa 8.1: substituição da PII por *** (redact). Função pura: sem Stubber nem monkeypatch.
import pytest

from moderador import app


def ent(entity_type, start, end, score=0.99):
    """Uma entidade no formato que o DetectPiiEntities devolve."""
    return {"Type": entity_type, "BeginOffset": start, "EndOffset": end, "Score": score}


def span(text, part):
    """Offsets (início, fim exclusivo) de part dentro de text, em code points."""
    start = text.index(part)
    return start, start + len(part)


# Substituição simples

def test_one_entity():
    t = "My name is John Smith."
    assert app.redact(t, [ent("NAME", *span(t, "John Smith"))]) == "My name is ***."


def test_two_entities_out_of_order():
    # O Comprehend não promete ordem: a EMAIL vem antes da NAME de propósito.
    t = "My name is John and my email is j@x.com."
    entities = [ent("EMAIL", *span(t, "j@x.com")), ent("NAME", *span(t, "John"))]
    assert app.redact(t, entities) == "My name is *** and my email is ***."


def test_whole_text():
    assert app.redact("abcdefghij", [ent("NAME", 0, 10)]) == "***"


def test_no_entities():
    assert app.redact("abcdefghij", []) == "abcdefghij"


@pytest.mark.parametrize("prefix", [
    "Café ☕ — ",  # acento, emoji e travessão (BMP)
    "\U0001F600 ",  # emoji fora do BMP: 1 code point em Python, 2 unidades em UTF-16
])
def test_offsets_in_code_points(prefix):
    # Medido na 7.3: o Comprehend devolve offsets em code points, iguais aos índices de str.
    t = prefix + "my name is John Smith"
    assert app.redact(t, [ent("NAME", *span(t, "John Smith"))]) == prefix + "my name is ***"


# Entidades sobrepostas

def test_overlapping():
    assert app.redact("abcdefghij", [ent("NAME", 1, 5), ent("ADDRESS", 3, 8)]) == "a***ij"


def test_one_inside_the_other():
    # O max() da fusão: a de dentro não encolhe o trecho da de fora.
    assert app.redact("abcdefghij", [ent("ADDRESS", 0, 9), ent("NAME", 2, 4)]) == "***j"


def test_three_chained():
    entities = [ent("NAME", 4, 7), ent("NAME", 0, 3), ent("PHONE", 2, 5)]
    assert app.redact("abcdefghij", entities) == "***hij"


# Entidades adjacentes

def test_adjacent_become_one():
    result = app.redact("abcdefghij", [ent("NAME", 0, 4), ent("PHONE", 4, 8)])
    assert result == "***ij"
    assert "******" not in result


def test_separated_by_one_character():
    assert app.redact("abcdefghij", [ent("NAME", 0, 3), ent("NAME", 4, 7)]) == "***d***hij"


# Entidades ignoradas (fora da lista ou abaixo do score)

@pytest.mark.parametrize("entity_type", ["DATE_TIME", "AGE", "URL", "USERNAME"])
def test_type_not_in_list(entity_type):
    assert app.redact("abcdefghij", [ent(entity_type, 0, 4)]) == "abcdefghij"


def test_score_below_minimum():
    assert app.redact("abcdefghij", [ent("NAME", 0, 4, score=0.49)]) == "abcdefghij"


def test_score_at_minimum():
    assert app.redact("abcdefghij", [ent("NAME", 0, 4, score=0.5)]) == "***efghij"


def test_ignored_does_not_extend_censorship():
    # O filtro vem antes da fusão: DATE_TIME colada na NAME não aumenta o trecho censurado.
    entities = [ent("NAME", 0, 4), ent("DATE_TIME", 4, 8), ent("EMAIL", 2, 9, score=0.3)]
    assert app.redact("abcdefghij", entities) == "***efghij"


def test_project_closed_list():
    # Caso 1 do PROJETO.md. Mudar a lista é decisão de projeto, não ajuste de teste.
    assert app.PII_ENTITIES == {
        "NAME", "EMAIL", "PHONE", "ADDRESS", "SSN",
        "CREDIT_DEBIT_NUMBER", "CREDIT_DEBIT_CVV", "CREDIT_DEBIT_EXPIRY",
        "PIN", "BANK_ACCOUNT_NUMBER", "BANK_ROUTING", "PASSPORT_NUMBER",
        "DRIVER_ID", "IP_ADDRESS", "PASSWORD",
    }
    assert app.MIN_SCORE == 0.5
