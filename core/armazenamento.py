"""Persistência das auditorias em SQLite.

Guarda o histórico das conferências — uma linha por auditoria e uma por guia — para
que o resultado sobreviva ao reinício do servidor e possa alimentar as métricas de
acurácia do trabalho.

O que **não** é guardado: a imagem anotada de cada página. Ela pesa ~127 KB e serve
só para a conferência visual no momento; um lote de 250 guias colocaria 30 MB de
pré-visualização no banco sem ganho nenhum.

O motor (`core/engine.py`) não conhece este módulo: quem grava é a camada de API,
depois de receber cada página do stream.
"""

import contextlib
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO_BANCO = os.environ.get("PYCONFER_DB", os.path.join(BASE_DIR, "pyconfer.db"))

ESQUEMA = """
CREATE TABLE IF NOT EXISTS auditoria (
    job_id           TEXT PRIMARY KEY,
    criada_em        TEXT NOT NULL,
    status           TEXT NOT NULL,
    dpi              INTEGER,
    arquivo_boletos  TEXT,
    arquivo_consulta TEXT,
    total_paginas    INTEGER
);

CREATE TABLE IF NOT EXISTS pagina (
    job_id        TEXT NOT NULL REFERENCES auditoria(job_id) ON DELETE CASCADE,
    pagina        INTEGER NOT NULL,
    cod_consulta  TEXT,
    cod_ocr       TEXT,
    status_codigo TEXT,
    val_consulta  TEXT,
    val_ocr       TEXT,
    status_valor  TEXT,
    status_geral  TEXT,
    natureza      TEXT,
    PRIMARY KEY (job_id, pagina)
);
"""

# Colunas acrescentadas depois que já havia bancos em uso. `CREATE TABLE IF NOT
# EXISTS` não altera tabela existente, então elas entram por ALTER TABLE.
COLUNAS_ACRESCENTADAS = (
    ("pagina", "natureza", "TEXT"),
)

# Ponte entre as colunas do relatório (nomes de exibição, usados no CSV e no JSON)
# e as colunas da tabela. Manter os dois lados aqui evita espalhar strings soltas.
_COLUNAS = (
    ("Pagina", "pagina"),
    ("Codigo (PDF Consulta)", "cod_consulta"),
    ("Codigo (OCR Boletos)", "cod_ocr"),
    ("Status Codigo", "status_codigo"),
    ("Valor (PDF Consulta)", "val_consulta"),
    ("Valor (OCR Boletos)", "val_ocr"),
    ("Status Valor", "status_valor"),
    ("Status Geral", "status_geral"),
    ("Natureza", "natureza"),
)


def configurar(caminho: str) -> None:
    """Aponta para outro arquivo de banco (usado pelos testes)."""
    global CAMINHO_BANCO
    CAMINHO_BANCO = caminho


@contextlib.contextmanager
def _conexao():
    """Abre uma conexão por operação.

    A auditoria roda em uma thread e as requisições em outras; conexão por operação
    é a forma mais simples de manter isso seguro, e com WAL a leitura não trava
    enquanto a auditoria grava.
    """
    conexao = sqlite3.connect(CAMINHO_BANCO, timeout=10)
    conexao.row_factory = sqlite3.Row
    try:
        conexao.execute("PRAGMA foreign_keys = ON")
        yield conexao
        conexao.commit()
    finally:
        conexao.close()


def criar_esquema() -> None:
    """Cria o banco se não existir e aplica as colunas acrescentadas depois.

    Bancos criados por versões anteriores continuam servindo: a coluna nova entra
    vazia nas auditorias antigas, em vez de exigir apagar o histórico.
    """
    with _conexao() as conexao:
        conexao.execute("PRAGMA journal_mode = WAL")
        conexao.executescript(ESQUEMA)

        for tabela, coluna, tipo in COLUNAS_ACRESCENTADAS:
            existentes = {linha["name"] for linha in conexao.execute(f"PRAGMA table_info({tabela})")}
            if coluna not in existentes:
                conexao.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")


def registrar_auditoria(job_id, criada_em, dpi, arquivo_boletos, arquivo_consulta) -> None:
    with _conexao() as conexao:
        conexao.execute(
            "INSERT OR REPLACE INTO auditoria "
            "(job_id, criada_em, status, dpi, arquivo_boletos, arquivo_consulta, total_paginas) "
            "VALUES (?, ?, 'processando', ?, ?, ?, NULL)",
            (job_id, criada_em, dpi, arquivo_boletos, arquivo_consulta),
        )


def atualizar_status(job_id, status, total_paginas=None) -> None:
    """Atualiza o status e, quando informado, o total de páginas do lote."""
    with _conexao() as conexao:
        if total_paginas is None:
            conexao.execute("UPDATE auditoria SET status = ? WHERE job_id = ?", (status, job_id))
        else:
            conexao.execute(
                "UPDATE auditoria SET status = ?, total_paginas = ? WHERE job_id = ?",
                (status, total_paginas, job_id),
            )


def salvar_pagina(job_id, linha: dict) -> None:
    """Grava uma guia conferida. Chamado a cada página, não só no fim.

    Assim uma auditoria interrompida no meio (queda, aba fechada) deixa o que já
    havia sido conferido registrado, em vez de perder tudo.
    """
    valores = [job_id] + [linha.get(exibicao) for exibicao, _coluna in _COLUNAS]
    colunas = ", ".join(coluna for _exibicao, coluna in _COLUNAS)
    marcadores = ", ".join("?" for _ in _COLUNAS)
    with _conexao() as conexao:
        conexao.execute(
            f"INSERT OR REPLACE INTO pagina (job_id, {colunas}) VALUES (?, {marcadores})",
            valores,
        )


def carregar_relatorio(job_id) -> list[dict]:
    """Devolve as páginas no mesmo formato que o stream e o CSV usam."""
    colunas = ", ".join(coluna for _exibicao, coluna in _COLUNAS)
    with _conexao() as conexao:
        linhas = conexao.execute(
            f"SELECT {colunas} FROM pagina WHERE job_id = ? ORDER BY pagina", (job_id,)
        ).fetchall()

    return [
        {exibicao: linha[coluna] for exibicao, coluna in _COLUNAS}
        for linha in linhas
    ]


_SELECT_RESUMO = """
SELECT a.job_id,
       a.criada_em,
       a.status,
       a.total_paginas,
       COUNT(p.pagina)                                              AS paginas_processadas,
       COALESCE(SUM(p.status_geral = 'OK'), 0)                      AS conformes,
       COALESCE(SUM(p.status_geral = 'ERRO'), 0)                    AS divergentes
  FROM auditoria a
  LEFT JOIN pagina p ON p.job_id = a.job_id
"""


def carregar_resumo(job_id) -> dict | None:
    with _conexao() as conexao:
        linha = conexao.execute(
            _SELECT_RESUMO + " WHERE a.job_id = ? GROUP BY a.job_id", (job_id,)
        ).fetchone()
    return dict(linha) if linha else None


def listar_auditorias(limite: int = 50) -> list[dict]:
    """Histórico, da mais recente para a mais antiga."""
    with _conexao() as conexao:
        linhas = conexao.execute(
            _SELECT_RESUMO + " GROUP BY a.job_id ORDER BY a.criada_em DESC, a.rowid DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [dict(linha) for linha in linhas]
