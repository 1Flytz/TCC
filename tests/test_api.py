"""Testes da API: validação dos endpoints e ciclo de vida das auditorias.

O foco é o que protege a memória do servidor. Cada evento de página carrega a
imagem anotada em base64 (~127 KB), então um lote de 250 guias passa de 30 MB —
sem os limites testados aqui, essa memória só voltaria ao reiniciar o processo.
"""

import queue
import time

import pytest
from fastapi.testclient import TestClient

from api import main
from core import armazenamento


@pytest.fixture(autouse=True)
def registro_limpo():
    """Isola os testes: o registro de auditorias é global ao módulo."""
    main.AUDITORIAS.clear()
    yield
    main.AUDITORIAS.clear()


@pytest.fixture
def cliente():
    return TestClient(main.app)


def _auditoria(status, idade_segundos=0.0, **extras):
    return {
        "status": status,
        "iniciada_em": time.monotonic() - idade_segundos,
        **extras,
    }


# ==========================================
# Limpeza da fila
# ==========================================


def test_esvaziar_fila_libera_os_eventos_pendentes():
    fila = queue.Queue(maxsize=4)
    for numero in range(4):
        fila.put_nowait({"pagina": numero, "imagem": "x" * 130_000})

    main._esvaziar_fila(fila)

    assert fila.empty()


def test_encerramento_nao_bloqueia_com_a_fila_cheia():
    """Com fila limitada, um `put` bloqueante travaria a thread para sempre —
    justamente no caso em que ninguém está consumindo."""
    fila = queue.Queue(maxsize=3)
    for numero in range(3):
        fila.put_nowait(numero)

    inicio = time.monotonic()
    main._publicar_encerramento(fila, main._SENTINELA)
    assert time.monotonic() - inicio < 1, "não pode bloquear esperando espaço"

    assert fila.get_nowait() is main._SENTINELA
    assert fila.empty()


# ==========================================
# Expiração do registro
# ==========================================


def test_auditoria_encerrada_expira_por_idade():
    main.AUDITORIAS.update({
        "antiga": _auditoria("concluida", main.TTL_AUDITORIA_SEGUNDOS + 60),
        "recente": _auditoria("concluida"),
    })

    main._limpar_auditorias_antigas()

    assert set(main.AUDITORIAS) == {"recente"}


@pytest.mark.parametrize("status", ["concluida", "erro", "abandonada"])
def test_qualquer_status_encerrado_expira(status):
    main.AUDITORIAS["velha"] = _auditoria(status, main.TTL_AUDITORIA_SEGUNDOS + 60)

    main._limpar_auditorias_antigas()

    assert main.AUDITORIAS == {}


def test_auditoria_em_andamento_nunca_e_descartada():
    """Descartar uma auditoria viva mataria o acompanhamento de quem está assistindo."""
    main.AUDITORIAS["rodando"] = _auditoria("processando", idade_segundos=999_999)

    main._limpar_auditorias_antigas()

    assert "rodando" in main.AUDITORIAS


def test_registro_e_cortado_no_maximo_mantendo_as_mais_recentes():
    # idade negativa => criadas "no futuro", nenhuma expira por TTL; só o corte age.
    for numero in range(main.MAX_AUDITORIAS + 10):
        main.AUDITORIAS[f"job{numero:03d}"] = _auditoria("concluida", idade_segundos=-numero)

    main._limpar_auditorias_antigas()

    assert len(main.AUDITORIAS) == main.MAX_AUDITORIAS
    assert "job000" not in main.AUDITORIAS, "a mais antiga deveria ter saído"
    assert f"job{main.MAX_AUDITORIAS + 9:03d}" in main.AUDITORIAS, "a mais nova deveria ficar"


def test_corte_por_maximo_nao_derruba_auditorias_em_andamento():
    for numero in range(main.MAX_AUDITORIAS + 10):
        main.AUDITORIAS[f"vivo{numero:03d}"] = _auditoria("processando", idade_segundos=-numero)

    main._limpar_auditorias_antigas()

    assert len(main.AUDITORIAS) == main.MAX_AUDITORIAS + 10


# ==========================================
# Auditoria abandonada pelo navegador
# ==========================================


def test_auditoria_sem_consumidor_e_interrompida(monkeypatch, tmp_path):
    """Aba fechada no meio: o worker precisa desistir em vez de encher a fila.

    Antes desta proteção ele seguiria até a última página empurrando ~127 KB por
    evento numa fila que ninguém leria, e a pasta temporária ficaria para trás.
    """
    monkeypatch.setattr(main, "SEGUNDOS_SEM_CONSUMO", 0.2)

    def motor_falso(*_args, **_kwargs):
        yield {"tipo": "inicio", "total_paginas": 500, "total_consulta": 500}
        for numero in range(1, 501):
            yield {
                "tipo": "pagina",
                "pagina": numero,
                "linha": {"Status Geral": "OK"},
                "imagem": "x" * 130_000,
            }

    monkeypatch.setattr(main.engine, "realizar_auditoria_stream", motor_falso)

    main.AUDITORIAS["orfa"] = _auditoria(
        "processando",
        fila=queue.Queue(maxsize=main.TAMANHO_MAXIMO_FILA),
        relatorio=[],
        total_paginas=None,
        conformes=0,
        divergentes=0,
        criada_em="",
    )
    pasta_temp = tmp_path / "job"
    pasta_temp.mkdir()

    main._executar_auditoria("orfa", "boletos.pdf", "consulta.pdf", 200, str(pasta_temp))

    auditoria = main.AUDITORIAS["orfa"]
    assert auditoria["status"] == "abandonada"
    assert len(auditoria["relatorio"]) < 500, "deveria ter parado bem antes do fim"
    assert auditoria["fila"].get_nowait() is main._SENTINELA
    assert auditoria["fila"].empty(), "as imagens pendentes deveriam ter sido liberadas"
    assert not pasta_temp.exists(), "a pasta temporária deveria ter sido removida"


# ==========================================
# Endpoints
# ==========================================


@pytest.mark.parametrize("caminho", [
    "/api/v1/auditorias/naoexiste",
    "/api/v1/auditorias/naoexiste/eventos",
    "/api/v1/auditorias/naoexiste/relatorio.csv",
])
def test_job_desconhecido_responde_404(cliente, caminho):
    resposta = cliente.get(caminho)
    assert resposta.status_code == 404
    assert resposta.json()["detail"] == "Auditoria não encontrada."


def test_upload_que_nao_e_pdf_e_recusado(cliente):
    resposta = cliente.post(
        "/api/v1/auditorias",
        files={
            "boletos": ("planilha.xlsx", b"nao sou um pdf", "application/vnd.ms-excel"),
            "consulta": ("consulta.pdf", b"%PDF-1.4", "application/pdf"),
        },
    )

    assert resposta.status_code == 400
    assert "planilha.xlsx" in resposta.json()["detail"]


def test_relatorio_antes_da_primeira_pagina_responde_409(cliente):
    main.AUDITORIAS["recem_criada"] = _auditoria("processando", relatorio=[])

    resposta = cliente.get("/api/v1/auditorias/recem_criada/relatorio.csv")

    assert resposta.status_code == 409


def test_auditoria_fora_da_memoria_ainda_responde_pelo_historico(cliente):
    """Expirar da memória não pode significar sumir: o histórico assume.

    Antes da persistência, uma auditoria descartada por idade ou volume passava a
    devolver 404 e o CSV se perdia junto.
    """
    armazenamento.registrar_auditoria("antiga", "2026-08-15T18:00:00", 500, "b.pdf", "c.pdf")
    armazenamento.atualizar_status("antiga", "concluida", 2)
    for pagina, situacao in ((1, "OK"), (2, "ERRO")):
        armazenamento.salvar_pagina("antiga", {
            "Pagina": pagina,
            "Codigo (PDF Consulta)": "113640",
            "Codigo (OCR Boletos)": "113640",
            "Status Codigo": "OK",
            "Valor (PDF Consulta)": "76,82",
            "Valor (OCR Boletos)": "76,82",
            "Status Valor": "OK",
            "Status Geral": situacao,
        })

    assert "antiga" not in main.AUDITORIAS, "o teste só vale com a auditoria fora da memória"

    resumo = cliente.get("/api/v1/auditorias/antiga").json()
    assert resumo["status"] == "concluida"
    assert resumo["paginas_processadas"] == 2
    assert resumo["conformes"] == 1
    assert resumo["divergentes"] == 1

    csv = cliente.get("/api/v1/auditorias/antiga/relatorio.csv")
    assert csv.status_code == 200
    assert "113640" in csv.text


def test_historico_lista_as_auditorias(cliente):
    armazenamento.registrar_auditoria("j1", "2026-08-10T09:00:00", 500, "b.pdf", "c.pdf")
    armazenamento.registrar_auditoria("j2", "2026-08-15T09:00:00", 300, "b.pdf", "c.pdf")

    historico = cliente.get("/api/v1/auditorias").json()

    assert [item["job_id"] for item in historico] == ["j2", "j1"]


def test_memoria_tem_prioridade_sobre_o_historico(cliente):
    """Enquanto a auditoria está viva, o número em tempo real é o que vale."""
    armazenamento.registrar_auditoria("viva", "2026-08-15T18:00:00", 500, "b.pdf", "c.pdf")
    main.AUDITORIAS["viva"] = _auditoria(
        "processando",
        relatorio=[{"Pagina": 1}],
        total_paginas=50,
        conformes=1,
        divergentes=0,
        criada_em="2026-08-15T18:00:00",
    )

    resumo = cliente.get("/api/v1/auditorias/viva").json()

    assert resumo["paginas_processadas"] == 1, "veio do banco, que ainda está sem páginas"
    assert resumo["total_paginas"] == 50


def test_resumo_reflete_o_andamento(cliente):
    main.AUDITORIAS["andando"] = _auditoria(
        "processando",
        relatorio=[{"Pagina": 1}, {"Pagina": 2}],
        total_paginas=50,
        conformes=2,
        divergentes=0,
        criada_em="2026-08-15T18:43:51",
    )

    corpo = cliente.get("/api/v1/auditorias/andando").json()

    assert corpo["paginas_processadas"] == 2
    assert corpo["total_paginas"] == 50
    assert corpo["conformes"] == 2
    assert corpo["status"] == "processando"
