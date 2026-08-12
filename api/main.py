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


def _executar_auditoria(job_id: str, caminho_boletos: str, caminho_consulta: str, dpi: int, pasta_temp: str):
    """Roda a auditoria em thread separada, publicando cada página na fila do job."""
    auditoria = AUDITORIAS[job_id]
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
            auditoria["fila"].put(evento)
        auditoria["status"] = "concluida"
    except Exception as e:  # noqa: BLE001 - o erro precisa chegar ao front-end
        auditoria["status"] = "erro"
        auditoria["fila"].put({"tipo": "erro", "mensagem": f"{type(e).__name__}: {e}"})
    finally:
        auditoria["fila"].put(_SENTINELA)
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

    job_id = uuid.uuid4().hex[:12]
    AUDITORIAS[job_id] = {
        "fila": queue.Queue(),
        "relatorio": [],
        "status": "processando",
        "total_paginas": None,
        "conformes": 0,
        "divergentes": 0,
        "criada_em": datetime.now().isoformat(timespec="seconds"),
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
