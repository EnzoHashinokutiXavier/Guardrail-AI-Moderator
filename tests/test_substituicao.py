# Etapa 8.1: substituição da PII por *** (redigir). Função pura: sem Stubber nem monkeypatch.
import pytest

from moderador import app


def ent(tipo, inicio, fim, score=0.99):
    """Uma entidade no formato que o DetectPiiEntities devolve."""
    return {"Type": tipo, "BeginOffset": inicio, "EndOffset": fim, "Score": score}


def trecho(texto, parte):
    """Offsets (início, fim exclusivo) de parte dentro de texto, em code points."""
    inicio = texto.index(parte)
    return inicio, inicio + len(parte)


# Substituição simples

def test_uma_entidade():
    t = "My name is John Smith."
    assert app.redigir(t, [ent("NAME", *trecho(t, "John Smith"))]) == "My name is ***."


def test_duas_entidades_fora_de_ordem():
    # O Comprehend não promete ordem: a EMAIL vem antes da NAME de propósito.
    t = "My name is John and my email is j@x.com."
    entidades = [ent("EMAIL", *trecho(t, "j@x.com")), ent("NAME", *trecho(t, "John"))]
    assert app.redigir(t, entidades) == "My name is *** and my email is ***."


def test_texto_inteiro():
    assert app.redigir("abcdefghij", [ent("NAME", 0, 10)]) == "***"


def test_sem_entidades():
    assert app.redigir("abcdefghij", []) == "abcdefghij"


@pytest.mark.parametrize("prefixo", [
    "Café ☕ — ",  # acento, emoji e travessão (BMP)
    "\U0001F600 ",  # emoji fora do BMP: 1 code point em Python, 2 unidades em UTF-16
])
def test_offsets_em_code_points(prefixo):
    # Medido na 7.3: o Comprehend devolve offsets em code points, iguais aos índices de str.
    t = prefixo + "my name is John Smith"
    assert app.redigir(t, [ent("NAME", *trecho(t, "John Smith"))]) == prefixo + "my name is ***"


# Entidades sobrepostas

def test_sobrepostas():
    assert app.redigir("abcdefghij", [ent("NAME", 1, 5), ent("ADDRESS", 3, 8)]) == "a***ij"


def test_uma_dentro_da_outra():
    # O max() da fusão: a de dentro não encolhe o trecho da de fora.
    assert app.redigir("abcdefghij", [ent("ADDRESS", 0, 9), ent("NAME", 2, 4)]) == "***j"


def test_tres_em_cadeia():
    entidades = [ent("NAME", 4, 7), ent("NAME", 0, 3), ent("PHONE", 2, 5)]
    assert app.redigir("abcdefghij", entidades) == "***hij"


# Entidades adjacentes

def test_adjacentes_viram_um_so():
    resultado = app.redigir("abcdefghij", [ent("NAME", 0, 4), ent("PHONE", 4, 8)])
    assert resultado == "***ij"
    assert "******" not in resultado


def test_separadas_por_um_caractere():
    assert app.redigir("abcdefghij", [ent("NAME", 0, 3), ent("NAME", 4, 7)]) == "***d***hij"


# Entidades ignoradas (fora da lista ou abaixo do score)

@pytest.mark.parametrize("tipo", ["DATE_TIME", "AGE", "URL", "USERNAME"])
def test_tipo_fora_da_lista(tipo):
    assert app.redigir("abcdefghij", [ent(tipo, 0, 4)]) == "abcdefghij"


def test_score_abaixo_do_minimo():
    assert app.redigir("abcdefghij", [ent("NAME", 0, 4, score=0.49)]) == "abcdefghij"


def test_score_no_minimo():
    assert app.redigir("abcdefghij", [ent("NAME", 0, 4, score=0.5)]) == "***efghij"


def test_ignorada_nao_estende_a_censura():
    # O filtro vem antes da fusão: DATE_TIME colada na NAME não aumenta o trecho censurado.
    entidades = [ent("NAME", 0, 4), ent("DATE_TIME", 4, 8), ent("EMAIL", 2, 9, score=0.3)]
    assert app.redigir("abcdefghij", entidades) == "***efghij"


def test_lista_fechada_do_projeto():
    # Caso 1 do PROJETO.md. Mudar a lista é decisão de projeto, não ajuste de teste.
    assert app.ENTIDADES_PII == {
        "NAME", "EMAIL", "PHONE", "ADDRESS", "SSN",
        "CREDIT_DEBIT_NUMBER", "CREDIT_DEBIT_CVV", "CREDIT_DEBIT_EXPIRY",
        "PIN", "BANK_ACCOUNT_NUMBER", "BANK_ROUTING", "PASSPORT_NUMBER",
        "DRIVER_ID", "IP_ADDRESS", "PASSWORD",
    }
    assert app.SCORE_MIN == 0.5
