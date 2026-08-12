"""
PyConfer - execução via linha de comando.

Toda a lógica de OCR e conferência vive em `core/engine.py`; este arquivo apenas
consome o motor e grava o relatório em CSV, preservando o comportamento original.
"""

import argparse
import os

import pandas as pd

from core import engine


def realizar_auditoria(caminho_boletos, caminho_consulta, caminho_saida=None, dpi=engine.DPI_PADRAO):
    caminho_saida = caminho_saida or os.path.join(engine.BASE_DIR, 'Relatorio_Final_Python.csv')
    relatorio = []

    print(f"Iniciando conversão de PDF para Imagem (DPI {dpi})...")
    for evento in engine.realizar_auditoria_stream(caminho_boletos, caminho_consulta, dpi=dpi, com_imagem=False):
        if evento["tipo"] == "pagina":
            relatorio.append(evento["linha"])

    pd.DataFrame(relatorio, columns=engine.COLUNAS_RELATORIO).to_csv(
        caminho_saida, index=False, sep=';', encoding='latin1'
    )
    print(f"Auditoria concluída! '{caminho_saida}' gerado.")
    return relatorio


def main():
    parser = argparse.ArgumentParser(description="Confere códigos e valores de guias contra o PDF de consulta.")
    parser.add_argument('--boletos', default=engine.CAMINHO_PDF_BOLETOS, help="PDF com as guias digitalizadas.")
    parser.add_argument('--consulta', default=engine.CAMINHO_PDF_CONSULTA, help="PDF de consulta (lista mestre).")
    parser.add_argument('--saida', default=None, help="Caminho do CSV de saída.")
    parser.add_argument('--dpi', type=int, default=engine.DPI_PADRAO, help="Resolução da conversão PDF -> imagem.")
    parser.add_argument('--tesseract', default=engine.CAMINHO_TESSERACT, help="Executável do Tesseract.")
    parser.add_argument('--poppler', default=engine.POPPLER_PATH, help="Pasta bin do Poppler.")
    args = parser.parse_args()

    engine.configure_paths(args.tesseract, args.poppler)
    realizar_auditoria(args.boletos, args.consulta, args.saida, args.dpi)


if __name__ == "__main__":
    main()
