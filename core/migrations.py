"""Upgrade legacy database identifiers without discarding audit history.

Portuguese literals are retained only to recognize data written by earlier releases.
"""

import sqlite3


LEGACY_TABLES = {
    "auditoria": ("audit", {
        "criada_em": "created_at",
        "arquivo_boletos": "payment_slips_file",
        "arquivo_consulta": "reference_file",
        "total_paginas": "total_pages",
    }),
    "pagina": ("page", {
        "pagina": "page",
        "cod_consulta": "reference_code",
        "cod_ocr": "ocr_code",
        "status_codigo": "code_status",
        "val_consulta": "reference_amount",
        "val_ocr": "ocr_amount",
        "status_valor": "amount_status",
        "status_geral": "overall_status",
        "natureza": "category",
    }),
}


def migrate_legacy_schema(connection: sqlite3.Connection) -> None:
    """Rename legacy tables and columns inside the caller's transaction."""
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )}
    for old_table, (new_table, columns) in LEGACY_TABLES.items():
        if old_table not in tables:
            continue
        if new_table in tables:
            raise sqlite3.DatabaseError(
                f"Both legacy and current tables exist: {old_table}, {new_table}. "
                "Resolve the conflicting schemas before starting the application."
            )
        connection.execute(f'ALTER TABLE "{old_table}" RENAME TO "{new_table}"')
        existing = {row[1] for row in connection.execute(f'PRAGMA table_info("{new_table}")')}
        for old_column, new_column in columns.items():
            if old_column in existing:
                connection.execute(
                    f'ALTER TABLE "{new_table}" RENAME COLUMN "{old_column}" TO "{new_column}"'
                )


def migrate_legacy_values(connection: sqlite3.Connection) -> None:
    """Translate persisted states and categories, leaving document data untouched."""
    mappings = {
        ("audit", "status"): {
            "processando": "processing", "concluida": "completed",
            "erro": "error", "abandonada": "abandoned",
        },
        ("page", "code_status"): {"DIFERENTE": "MISMATCH"},
        ("page", "amount_status"): {"DIFERENTE": "MISMATCH"},
        ("page", "overall_status"): {"ERRO": "ERROR", "FIM DA LISTA": "END OF LIST"},
        ("page", "category"): {
            "OUTRO CADASTRO": "OTHER REGISTRATION", "NAO LIDO": "UNREAD", "VERIFICAR": "REVIEW",
        },
    }
    for (table, column), translations in mappings.items():
        for old_value, new_value in translations.items():
            connection.execute(
                f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?',
                (new_value, old_value),
            )
