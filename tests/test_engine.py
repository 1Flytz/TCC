"""Testes do motor de conferência.

Cobrem as duas decisões que sustentam a auditoria — se o código lido bate com o
esperado e como o valor monetário é normalizado — mais o pareamento código/valor
extraído do PDF de consulta.

Alguns testes fixam comportamentos arriscados de propósito (marcados com ALERTA
ou LIMITE CONHECIDO). Eles não estão dizendo que a regra está certa; estão
documentando o que o sistema faz hoje, para que qualquer mudança seja consciente.
"""

import pytest

from core import engine

# ==========================================
# status_codigo
# ==========================================


@pytest.mark.parametrize("lido, esperado", [
    ("116530", "116530"),
    ("113640", "113640"),
    ("59750", "59750"),      # sufixo de 5 dígitos
])
def test_codigo_identico_e_conforme(lido, esperado):
    assert engine.status_codigo(lido, esperado) == "OK"


@pytest.mark.parametrize("lido, esperado", [
    ("113640", "113610"),   # 4 -> 1
    ("61990", "61890"),     # 9 -> 8
    ("116530", "116230"),   # 5 -> 2
    ("60700", "60900"),     # 7 -> 9
])
def test_diferenca_fora_da_lista_de_confusoes_e_apontada(lido, esperado):
    assert engine.status_codigo(lido, esperado) == "DIFERENTE"


@pytest.mark.parametrize("lido, esperado, motivo", [
    ("0", "116530", "OCR não conseguiu ler nada na guia"),
    ("", "116530", "leitura vazia"),
    ("116530", "N/A", "a consulta acabou antes das guias"),
    ("116530", "", "esperado vazio"),
])
def test_sem_par_valido_o_resultado_e_diferente(lido, esperado, motivo):
    assert engine.status_codigo(lido, esperado) == "DIFERENTE", motivo


def test_codigo_que_nao_termina_em_zero_nunca_confere():
    """Só sufixos terminados em 0 entram na comparação.

    Na prática todo código da lista mestre termina em 0 (o `-0` é reposto por
    `extrair_lista_mestre`), então isso não aparece em produção — mas explica por
    que dois códigos idênticos podem sair como DIFERENTE.
    """
    assert engine.status_codigo("12345", "12345") == "DIFERENTE"


@pytest.mark.parametrize("lido, esperado, confusao", [
    ("116110", "116170", "1 lido como 7"),
    ("113640", "113840", "6 lido como 8"),
    ("119000", "119090", "0 lido como 9"),
    ("116110", "116770", "dois 1 lidos como 7"),
])
def test_confusao_de_ocr_e_apontada_em_vez_de_absorvida(lido, esperado, confusao):
    """A comparação é exata de propósito — ver o docstring de `status_codigo`.

    Estes quatro pares são cadastros que existem de verdade no lote de referência.
    Quando havia tolerância a confusões de OCR, todos passavam como conformes: uma
    divergência real sumia sem ninguém ver.
    """
    assert engine.status_codigo(lido, esperado) == "DIFERENTE", confusao


def test_nenhum_par_de_cadastros_distintos_pode_ser_dado_como_igual():
    """Trava de regressão para a tolerância que foi removida.

    Passa por todos os pares de códigos do lote de referência e exige que dois
    cadastros diferentes nunca sejam considerados o mesmo. Antes da mudança havia
    50 pares assim.
    """
    from itertools import combinations

    codigos = [
        "113640", "113840", "116110", "116170", "116770", "116250", "118250",
        "116700", "118700", "116810", "118870", "119000", "119090", "118680",
        "118860", "118880", "117510", "117570",
    ]
    colisoes = [
        (a, b) for a, b in combinations(codigos, 2)
        if engine.status_codigo(a, b) == "OK"
    ]
    assert colisoes == []


# ==========================================
# classificar_divergencia
# ==========================================

CADASTROS = {"115870", "118870", "116110", "116170"}


def _resultado(codigo="115870", valor="76,82", status_codigo="OK", status_valor="OK", status_geral="ERRO"):
    return {
        "codigo": codigo,
        "valor": valor,
        "status_codigo": status_codigo,
        "status_valor": status_valor,
        "status_geral": status_geral,
    }


def test_pagina_conforme_nao_recebe_natureza():
    resultado = _resultado(status_geral="OK")
    assert engine.classificar_divergencia(resultado, CADASTROS) == ""


def test_codigo_lido_de_outra_guia_do_lote_e_o_caso_grave():
    """O OCR leu, na guia de 115870, o numero 118870 — que existe no mesmo lote.

    Aconteceu de verdade, em DPI 300, na pagina 44 do lote de referencia. E o unico
    caso com risco financeiro direto: a guia pode ter sido trocada.
    """
    resultado = _resultado(codigo="118870", status_codigo="DIFERENTE")
    assert engine.classificar_divergencia(resultado, CADASTROS) == engine.NATUREZA_OUTRO_CADASTRO


@pytest.mark.parametrize("codigo", ["0", "", None])
def test_codigo_nao_extraido_e_falha_de_leitura(codigo):
    resultado = _resultado(codigo=codigo, status_codigo="DIFERENTE")
    assert engine.classificar_divergencia(resultado, CADASTROS) == engine.NATUREZA_NAO_LIDO


def test_codigo_que_nao_existe_no_lote_fica_para_verificacao():
    """716110 nao e cadastro de ninguem: provavel sujeira no scan (DPI 200, pag. 47)."""
    resultado = _resultado(codigo="716110", status_codigo="DIFERENTE")
    assert engine.classificar_divergencia(resultado, CADASTROS) == engine.NATUREZA_VERIFICAR


def test_valor_nao_lido_com_codigo_correto():
    resultado = _resultado(valor="0,00", status_valor="DIFERENTE")
    assert engine.classificar_divergencia(resultado, CADASTROS) == engine.NATUREZA_NAO_LIDO


def test_valor_diferente_com_codigo_correto_fica_para_verificacao():
    resultado = _resultado(valor="99,99", status_valor="DIFERENTE")
    assert engine.classificar_divergencia(resultado, CADASTROS) == engine.NATUREZA_VERIFICAR


def test_codigo_grave_tem_prioridade_sobre_valor_nao_lido():
    """Havendo problema nos dois campos, o do codigo e o que importa relatar."""
    resultado = _resultado(
        codigo="118870", valor="0,00", status_codigo="DIFERENTE", status_valor="DIFERENTE"
    )
    assert engine.classificar_divergencia(resultado, CADASTROS) == engine.NATUREZA_OUTRO_CADASTRO


# ==========================================
# normalizar_valor
# ==========================================


@pytest.mark.parametrize("texto, esperado", [
    ("82,30", "82,30"),
    ("76.82", "76,82"),                 # ponto no lugar da vírgula
    ("Total a pagar 82,30 ate 21/11", "82,30"),  # extrai de dentro da linha
    ("82‚30", "82,30"),            # vírgula baixa
    ("82’30", "82,30"),            # aspa simples
    ("82`30", "82,30"),                 # crase
    ("82´30", "82,30"),            # acento agudo
    ("999,99", "999,99"),               # teto aceito
])
def test_valor_normalizado_para_o_formato_do_relatorio(texto, esperado):
    assert engine.normalizar_valor(texto) == esperado


@pytest.mark.parametrize("texto", [
    "1000,00",   # acima do teto de 999,99
    "82,3",      # uma casa decimal só
    "abc",
    "",
    None,
])
def test_valor_invalido_vira_none(texto):
    assert engine.normalizar_valor(texto) is None


def test_valor_abaixo_de_dez_reais_nao_e_reconhecido():
    """LIMITE CONHECIDO: a regex exige 2 ou 3 dígitos antes da vírgula.

    Qualquer guia abaixo de R$ 10,00 sai como não lida (vira "0,00" e é marcada
    como divergente). Não afeta o lote atual, cujos valores vão de 54,87 a 160,71.
    """
    assert engine.normalizar_valor("5,30") is None


# ==========================================
# extrair_lista_mestre
# ==========================================


class _PaginaFalsa:
    def __init__(self, texto):
        self._texto = texto

    def extract_text(self):
        return self._texto


class _LeitorFalso:
    def __init__(self, paginas):
        self.pages = [_PaginaFalsa(texto) for texto in paginas]


@pytest.fixture
def consulta(monkeypatch):
    """Substitui o pypdf por um leitor de texto fixo.

    Deixa o teste focado no que é do projeto (a regex e o pareamento) e dispensa
    um PDF de apoio no repositório — os PDFs reais são ignorados pelo git.
    """
    def _ler(*paginas):
        monkeypatch.setattr(engine.pypdf, "PdfReader", lambda _caminho: _LeitorFalso(paginas))
        return engine.extrair_lista_mestre("consulta_ficticia.pdf")
    return _ler


def test_lista_mestre_repoe_o_zero_do_sufixo(consulta):
    """No PDF o código aparece como `11364-0`; no relatório ele vira `113640`."""
    assert consulta("11364-0 76,82\n5975-0 82,30") == [
        {"codigo": "113640", "valor": "76,82"},
        {"codigo": "59750", "valor": "82,30"},
    ]


def test_codigo_sem_valor_correspondente_fica_na(consulta):
    assert consulta("11364-0 5975-0 76,82") == [
        {"codigo": "113640", "valor": "76,82"},
        {"codigo": "59750", "valor": "N/A"},
    ]


def test_valor_sem_codigo_correspondente_fica_na(consulta):
    assert consulta("11364-0 76,82 82,30") == [
        {"codigo": "113640", "valor": "76,82"},
        {"codigo": "N/A", "valor": "82,30"},
    ]


def test_pagina_sem_texto_e_ignorada(consulta):
    assert consulta("", "11364-0 76,82") == [{"codigo": "113640", "valor": "76,82"}]


def test_pdf_ilegivel_devolve_lista_vazia(monkeypatch):
    """Um erro de leitura não pode derrubar a auditoria inteira."""
    def explodir(_caminho):
        raise OSError("PDF corrompido")

    monkeypatch.setattr(engine.pypdf, "PdfReader", explodir)
    assert engine.extrair_lista_mestre("qualquer.pdf") == []


def test_pareamento_e_posicional_e_desloca_com_valor_extra(consulta):
    """ALERTA: código e valor são pareados por posição na página, não por linha.

    Basta um valor a mais em qualquer lugar (um total, uma taxa, um cabeçalho) para
    tudo deslocar: aqui o código 113640 recebe 99,99, que é o total da página, e o
    valor correto dele vai parar numa linha órfã. A partir desse ponto a página
    inteira sai errada.
    """
    assert consulta("Total 99,99\n11364-0 76,82") == [
        {"codigo": "113640", "valor": "99,99"},
        {"codigo": "N/A", "valor": "76,82"},
    ]
