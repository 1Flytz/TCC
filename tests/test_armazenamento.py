"""Testes do histórico em SQLite."""

import pytest

from core import armazenamento, engine


def _linha(pagina, status_geral="OK", codigo="113640", valor="76,82"):
    """Monta uma linha no mesmo formato que o motor entrega."""
    return {
        "Pagina": pagina,
        "Codigo (PDF Consulta)": codigo,
        "Codigo (OCR Boletos)": codigo,
        "Status Codigo": "OK",
        "Valor (PDF Consulta)": valor,
        "Valor (OCR Boletos)": valor,
        "Status Valor": "OK",
        "Status Geral": status_geral,
    }


@pytest.fixture
def auditoria_registrada():
    armazenamento.registrar_auditoria("job1", "2026-08-15T18:00:00", 500, "boletos.pdf", "consulta.pdf")
    return "job1"


def test_auditoria_recem_registrada_aparece_sem_paginas(auditoria_registrada):
    resumo = armazenamento.carregar_resumo(auditoria_registrada)

    assert resumo["status"] == "processando"
    assert resumo["paginas_processadas"] == 0
    assert resumo["conformes"] == 0
    assert resumo["divergentes"] == 0
    assert resumo["total_paginas"] is None


def test_relatorio_volta_no_mesmo_formato_do_motor(auditoria_registrada):
    """O que sai do banco tem que servir direto para o CSV, sem tradução."""
    armazenamento.salvar_pagina(auditoria_registrada, _linha(1))

    relatorio = armazenamento.carregar_relatorio(auditoria_registrada)

    assert len(relatorio) == 1
    assert set(relatorio[0]) == set(engine.COLUNAS_RELATORIO)
    assert relatorio[0] == _linha(1)


def test_paginas_voltam_em_ordem(auditoria_registrada):
    for pagina in (3, 1, 2):
        armazenamento.salvar_pagina(auditoria_registrada, _linha(pagina))

    relatorio = armazenamento.carregar_relatorio(auditoria_registrada)

    assert [linha["Pagina"] for linha in relatorio] == [1, 2, 3]


def test_regravar_a_mesma_pagina_nao_duplica(auditoria_registrada):
    """Reprocessar uma página deve corrigir o registro, não criar um segundo."""
    armazenamento.salvar_pagina(auditoria_registrada, _linha(1, status_geral="ERRO"))
    armazenamento.salvar_pagina(auditoria_registrada, _linha(1, status_geral="OK"))

    relatorio = armazenamento.carregar_relatorio(auditoria_registrada)

    assert len(relatorio) == 1
    assert relatorio[0]["Status Geral"] == "OK"


def test_resumo_conta_conformes_e_divergentes(auditoria_registrada):
    armazenamento.salvar_pagina(auditoria_registrada, _linha(1, "OK"))
    armazenamento.salvar_pagina(auditoria_registrada, _linha(2, "OK"))
    armazenamento.salvar_pagina(auditoria_registrada, _linha(3, "ERRO"))
    armazenamento.salvar_pagina(auditoria_registrada, _linha(4, "FIM DA LISTA"))

    resumo = armazenamento.carregar_resumo(auditoria_registrada)

    assert resumo["paginas_processadas"] == 4
    assert resumo["conformes"] == 2
    assert resumo["divergentes"] == 1


def test_atualizar_status_registra_o_desfecho(auditoria_registrada):
    armazenamento.atualizar_status(auditoria_registrada, "processando", 50)
    armazenamento.atualizar_status(auditoria_registrada, "concluida")

    resumo = armazenamento.carregar_resumo(auditoria_registrada)

    assert resumo["status"] == "concluida"
    assert resumo["total_paginas"] == 50, "o total não pode se perder na atualização seguinte"


def test_auditoria_desconhecida_devolve_none():
    assert armazenamento.carregar_resumo("naoexiste") is None
    assert armazenamento.carregar_relatorio("naoexiste") == []


def test_historico_vem_da_mais_recente_para_a_mais_antiga():
    for numero, momento in enumerate(["2026-08-10T09:00:00", "2026-08-15T09:00:00", "2026-08-12T09:00:00"]):
        armazenamento.registrar_auditoria(f"job{numero}", momento, 500, "b.pdf", "c.pdf")

    historico = armazenamento.listar_auditorias()

    assert [item["job_id"] for item in historico] == ["job1", "job2", "job0"]


def test_historico_respeita_o_limite():
    for numero in range(10):
        armazenamento.registrar_auditoria(f"job{numero}", f"2026-08-{numero + 1:02d}T09:00:00", 500, "b.pdf", "c.pdf")

    assert len(armazenamento.listar_auditorias(limite=3)) == 3


def test_apagar_auditoria_leva_as_paginas_junto(auditoria_registrada):
    """A FK com ON DELETE CASCADE evita páginas órfãs no histórico."""
    armazenamento.salvar_pagina(auditoria_registrada, _linha(1))

    with armazenamento._conexao() as conexao:
        conexao.execute("DELETE FROM auditoria WHERE job_id = ?", (auditoria_registrada,))

    assert armazenamento.carregar_relatorio(auditoria_registrada) == []
