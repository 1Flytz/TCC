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
    """Cada teste começa com o banco recriado do zero."""
    for sufixo in ("", "-wal", "-shm"):
        arquivo = armazenamento.CAMINHO_BANCO + sufixo
        if os.path.exists(arquivo):
            os.remove(arquivo)
    armazenamento.criar_esquema()
    yield
