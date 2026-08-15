"""
API REST do PyConfer (execução local).

Expõe o motor de conferência via HTTP e transmite o resultado de cada página em
tempo real por Server-Sent Events (SSE), permitindo que o front-end acompanhe a
auditoria enquanto ela acontece.

Como o OCR é uma operação bloqueante de CPU, cada auditoria roda em uma thread
separada que publica os eventos em uma fila; o endpoint SSE apenas consome essa
fila. Assim o event loop do FastAPI nunca fica travado.

Para subir:  uvicorn api.main:app --reload
"""

import io
import json
import queue
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import engine

app = FastAPI(
    title="PyConfer API",
    version="1.0.0",
    description=(
        "Automatiza a conferência de guias (RLC / fichas financeiras): compara o código "
        "e o valor lidos por OCR nos boletos com os dados do PDF de consulta."
    ),
)

# Registro de auditorias em memória. Suficiente para o uso local previsto;
# a persistência em banco é responsabilidade da frente de Dados do grupo.
AUDITORIAS: dict[str, dict] = {}

_SENTINELA = object()

# Cada evento de página carrega a imagem anotada em base64 (~127 KB), então um lote de
# 250 guias soma mais de 30 MB. Os limites abaixo devolvem essa memória quando o
# navegador vai embora no meio da auditoria ou quando o registro envelhece.
TAMANHO_MAXIMO_FILA = 8           # eventos aguardando entrega ao navegador
SEGUNDOS_SEM_CONSUMO = 60         # fila cheia por esse tempo => ninguém está assistindo
TTL_AUDITORIA_SEGUNDOS = 60 * 60  # auditorias encerradas expiram depois de uma hora
MAX_AUDITORIAS = 20               # ... ou quando o registro passa desse total


class AuditoriaCriada(BaseModel):
    job_id: str
    total_consulta: int | None = None
    mensagem: str


class ResumoAuditoria(BaseModel):
    job_id: str
    status: str
    paginas_processadas: int
    total_paginas: int | None
    conformes: int
    divergentes: int
    criada_em: str


def _esvaziar_fila(fila: queue.Queue) -> None:
    """Descarta os eventos não entregues, liberando as imagens que eles carregam."""
    while True:
        try:
            fila.get_nowait()
        except queue.Empty:
            return


def _publicar_encerramento(fila: queue.Queue, item) -> None:
    """Entrega um aviso de fim de stream mesmo que a fila esteja cheia.

    Sem isso, um `put` bloqueante em fila cheia deixaria a thread da auditoria presa
    para sempre justamente no caminho em que ninguém está consumindo.
    """
    try:
        fila.put_nowait(item)
    except queue.Full:
        _esvaziar_fila(fila)
        fila.put_nowait(item)


def _limpar_auditorias_antigas() -> None:
    """Remove auditorias já encerradas para a memória não crescer a cada execução.

    Auditorias em andamento nunca são descartadas. As demais saem por idade (TTL) ou
    quando o registro ultrapassa `MAX_AUDITORIAS`, começando pelas mais antigas.
    """
    agora = time.monotonic()
    encerradas = sorted(
        (dados["iniciada_em"], job_id)
        for job_id, dados in AUDITORIAS.items()
        if dados["status"] != "processando"
    )

    excedente = max(0, len(AUDITORIAS) - MAX_AUDITORIAS)
    descartar = {job_id for _inicio, job_id in encerradas[:excedente]}
    descartar.update(job_id for inicio, job_id in encerradas if agora - inicio > TTL_AUDITORIA_SEGUNDOS)

    for job_id in descartar:
        AUDITORIAS.pop(job_id, None)


def _executar_auditoria(job_id: str, caminho_boletos: str, caminho_consulta: str, dpi: int, pasta_temp: str):
    """Roda a auditoria em thread separada, publicando cada página na fila do job."""
    auditoria = AUDITORIAS[job_id]
    fila = auditoria["fila"]
    try:
        for evento in engine.realizar_auditoria_stream(caminho_boletos, caminho_consulta, dpi=dpi):
            if evento["tipo"] == "pagina":
                auditoria["relatorio"].append(evento["linha"])
                if evento["linha"]["Status Geral"] == "OK":
                    auditoria["conformes"] += 1
                elif evento["linha"]["Status Geral"] == "ERRO":
                    auditoria["divergentes"] += 1
            elif evento["tipo"] == "inicio":
                auditoria["total_paginas"] = evento["total_paginas"]

            try:
                fila.put(evento, timeout=SEGUNDOS_SEM_CONSUMO)
            except queue.Full:
                # Um navegador conectado esvazia a fila em milissegundos; se ela ficou
                # cheia todo esse tempo, a aba foi fechada. Interrompe a auditoria em vez
                # de seguir acumulando dezenas de MB de imagens que ninguém vai ver.
                auditoria["status"] = "abandonada"
                _esvaziar_fila(fila)
                return
        auditoria["status"] = "concluida"
    except Exception as e:  # noqa: BLE001 - o erro precisa chegar ao front-end
        auditoria["status"] = "erro"
        _publicar_encerramento(fila, {"tipo": "erro", "mensagem": f"{type(e).__name__}: {e}"})
    finally:
        _publicar_encerramento(fila, _SENTINELA)
        shutil.rmtree(pasta_temp, ignore_errors=True)


@app.post("/api/v1/auditorias", response_model=AuditoriaCriada, tags=["Auditoria"])
async def criar_auditoria(
    boletos: UploadFile = File(..., description="PDF com as guias digitalizadas."),
    consulta: UploadFile = File(..., description="PDF de consulta com a lista mestre."),
    dpi: int = 500,
):
    """Recebe os dois PDFs e inicia a auditoria em segundo plano.

    Retorna imediatamente um `job_id`; o acompanhamento é feito pelo endpoint de eventos.
    """
    for arquivo in (boletos, consulta):
        if not (arquivo.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"'{arquivo.filename}' não é um PDF.")

    pasta_temp = tempfile.mkdtemp(prefix="pyconfer_")
    caminho_boletos = f"{pasta_temp}/boletos.pdf"
    caminho_consulta = f"{pasta_temp}/consulta.pdf"

    for arquivo, destino in ((boletos, caminho_boletos), (consulta, caminho_consulta)):
        with open(destino, "wb") as saida:
            shutil.copyfileobj(arquivo.file, saida)

    _limpar_auditorias_antigas()

    job_id = uuid.uuid4().hex[:12]
    AUDITORIAS[job_id] = {
        "fila": queue.Queue(maxsize=TAMANHO_MAXIMO_FILA),
        "relatorio": [],
        "status": "processando",
        "total_paginas": None,
        "conformes": 0,
        "divergentes": 0,
        "criada_em": datetime.now().isoformat(timespec="seconds"),
        # Relógio monotônico: usado só para medir idade, imune a ajuste de horário.
        "iniciada_em": time.monotonic(),
    }

    threading.Thread(
        target=_executar_auditoria,
        args=(job_id, caminho_boletos, caminho_consulta, dpi, pasta_temp),
        daemon=True,
    ).start()

    return AuditoriaCriada(job_id=job_id, mensagem="Auditoria iniciada. Acompanhe pelo endpoint de eventos.")


@app.get("/api/v1/auditorias/{job_id}/eventos", tags=["Auditoria"])
def acompanhar_auditoria(job_id: str):
    """Transmite o resultado de cada página em tempo real (Server-Sent Events).

    Cada mensagem é um JSON com `tipo` igual a `inicio`, `pagina`, `erro` ou `fim`.
    """
    auditoria = AUDITORIAS.get(job_id)
    if not auditoria:
        raise HTTPException(status_code=404, detail="Auditoria não encontrada.")

    def gerar_eventos():
        while True:
            evento = auditoria["fila"].get()
            if evento is _SENTINELA:
                break
            yield f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gerar_eventos(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/auditorias/{job_id}", response_model=ResumoAuditoria, tags=["Auditoria"])
def consultar_auditoria(job_id: str):
    """Situação atual de uma auditoria (útil para reconectar sem reprocessar)."""
    auditoria = AUDITORIAS.get(job_id)
    if not auditoria:
        raise HTTPException(status_code=404, detail="Auditoria não encontrada.")
    return ResumoAuditoria(
        job_id=job_id,
        status=auditoria["status"],
        paginas_processadas=len(auditoria["relatorio"]),
        total_paginas=auditoria["total_paginas"],
        conformes=auditoria["conformes"],
        divergentes=auditoria["divergentes"],
        criada_em=auditoria["criada_em"],
    )


@app.get("/api/v1/auditorias/{job_id}/relatorio.csv", tags=["Auditoria"])
def baixar_relatorio(job_id: str):
    """Baixa o relatório no mesmo formato gerado pelo script de linha de comando."""
    auditoria = AUDITORIAS.get(job_id)
    if not auditoria:
        raise HTTPException(status_code=404, detail="Auditoria não encontrada.")
    if not auditoria["relatorio"]:
        raise HTTPException(status_code=409, detail="Nenhuma página processada ainda.")

    buffer = io.StringIO()
    pd.DataFrame(auditoria["relatorio"], columns=engine.COLUNAS_RELATORIO).to_csv(buffer, index=False, sep=";")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="Relatorio_{job_id}.csv"'},
    )


# O front-end é servido pelo próprio FastAPI (sem build step / sem Node).
app.mount("/", StaticFiles(directory=f"{engine.BASE_DIR}/static", html=True), name="static")
