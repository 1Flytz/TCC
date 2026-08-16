"""Configuração comum dos testes.

Redireciona o histórico para um banco descartável **antes** de importar a API — que
cria o esquema já na importação. Sem isso, rodar a suíte escreveria no
`pyconfer.db` de trabalho.
"""

import os
import tempfile

os.environ.setdefault(
    "PYCONFER_DB",
    os.path.join(tempfile.mkdtemp(prefix="pyconfer_testes_"), "historico.db"),
)

import pytest  # noqa: E402  (precisa vir depois da variável de ambiente)

from core import armazenamento  # noqa: E402


@pytest.fixture(autouse=True)
def historico_limpo():
    """Cada teste começa com o histórico vazio.

    Esvazia as tabelas em vez de apagar o arquivo: no Windows, remover um banco
    SQLite que ainda tenha handle aberto levanta PermissionError.
    """
    armazenamento.criar_esquema()
    with armazenamento._conexao() as conexao:
        conexao.execute("DELETE FROM pagina")
        conexao.execute("DELETE FROM auditoria")
    yield
