"""
Motor de conferência do PyConfer.

Concentra toda a lógica de extração e comparação que antes vivia em `conferidor.py`:
leitura da lista mestre (PDF de consulta), OCR das guias e o algoritmo de consenso.

O motor é agnóstico de interface: tanto o CLI (`conferidor.py`) quanto a API
(`api/main.py`) consomem o mesmo gerador `realizar_auditoria_stream`.
"""

import base64
import io
import os
import re
from collections import Counter

import pdf2image
import pypdf
import pytesseract
from PIL import ImageDraw, ImageEnhance, ImageFilter

# ==========================================
# FASE 1: DIRETÓRIOS DINÂMICOS
# ==========================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO_PDF_BOLETOS = os.path.join(BASE_DIR, 'docs', 'boletos.pdf')
CAMINHO_PDF_CONSULTA = os.path.join(BASE_DIR, 'docs', 'consulta.pdf')

# Podem ser sobrescritos por variável de ambiente (útil porque cada membro do grupo
# instala o Tesseract/Poppler em um caminho diferente) ou por `configure_paths`.
CAMINHO_TESSERACT = os.environ.get('TESSERACT_CMD', r'C:\Program Files\Tesseract-OCR\tesseract.exe')
POPPLER_PATH = os.environ.get('POPPLER_PATH', r"C:\Program Files\poppler\Library\bin")

DPI_PADRAO = 500
CONFIG_TESSERACT = r'--psm 6 -c tessedit_char_whitelist=0123456789,. -c classify_bln_numeric_mode=1 -c tessedit_char_blacklist=IlOo'


def configure_paths(tess_path: str, poppler_bin: str):
    """Define onde estão o Tesseract e o Poppler.

    Se o caminho informado não existir, recorre ao executável disponível no PATH —
    assim o projeto roda sem edição de código em máquinas onde a instalação ficou
    em outro diretório (ex.: instalação via winget).
    """
    global CAMINHO_TESSERACT, POPPLER_PATH
    CAMINHO_TESSERACT = tess_path
    POPPLER_PATH = poppler_bin
    pytesseract.pytesseract.tesseract_cmd = tess_path if os.path.isfile(tess_path) else 'tesseract'
    if POPPLER_PATH and os.path.isdir(POPPLER_PATH) and POPPLER_PATH not in os.environ.get("PATH", ""):
        os.environ["PATH"] += os.pathsep + POPPLER_PATH


# Aplica os caminhos padrão já na importação, para que a API funcione sem setup extra.
configure_paths(CAMINHO_TESSERACT, POPPLER_PATH)


# ==========================================
# FASE 2: FUNÇÕES ESPECIALISTAS
# ==========================================

def extrair_lista_mestre(caminho_consulta):
    print("Lendo códigos e valores do PDF de consulta...")
    dados_encontrados = []
    try:
        reader = pypdf.PdfReader(caminho_consulta)
        for pagina in reader.pages:
            texto = pagina.extract_text()
            if not texto: continue

            codigos_raw = re.findall(r'\b(\d{4,5})-0\b', texto)
            valores_raw = re.findall(r'\b\d{2,3},\d{2}\b', texto)
            max_len = max(len(codigos_raw), len(valores_raw))

            for k in range(max_len):
                cod_val = codigos_raw[k] + "0" if k < len(codigos_raw) else "N/A"
                val_monetario = valores_raw[k] if k < len(valores_raw) else "N/A"
                dados_encontrados.append({"codigo": cod_val, "valor": val_monetario})
    except Exception as e:
        print(f"Erro ao ler PDF de consulta: {e}")
    return dados_encontrados


def status_codigo(cod_lido: str, cod_esp: str):
    if not cod_lido or not cod_esp or cod_esp == "N/A": return "DIFERENTE"

    suf_lido = cod_lido[-6:] if (len(cod_lido[-6:]) == 6 and cod_lido[-6:].endswith("0")) else (cod_lido[-5:] if (len(cod_lido[-5:]) == 5 and cod_lido[-5:].endswith("0")) else "")
    suf_esp = cod_esp[-6:] if (len(cod_esp[-6:]) == 6 and cod_esp[-6:].endswith("0")) else (cod_esp[-5:] if (len(cod_esp[-5:]) == 5 and cod_esp[-5:].endswith("0")) else "")

    if suf_lido and suf_esp:
        if suf_lido == suf_esp: return "OK"
        pares_confusos = {('8', '6'), ('6', '8'), ('0', '9'), ('9', '0'), ('1', '7'), ('7', '1'), ('5', 'S'), ('S', '5'), ('2', 'Z'), ('Z', '2')}
        if len(suf_lido) == len(suf_esp):
            difs = [(x, y) for x, y in zip(suf_lido, suf_esp) if x != y]
            if len(difs) <= 2 and all(d in pares_confusos for d in difs): return "OK"
    return "DIFERENTE"


def normalizar_valor(texto_extraido: str):
    """Converte variações de separador decimal do OCR para o formato `123,45`."""
    if not texto_extraido: return None
    texto_n = texto_extraido.replace('‚', ',').replace('’', ',').replace('`', ',').replace('´', ',').replace('·', '.')
    m1 = re.search(r'\b\d{2,3}[.,]\d{2}\b', texto_n)
    if m1:
        val = m1.group(0).replace('.', ',')
        if int(val.split(',')[0]) <= 999: return val
    return None


def extrair_dados_da_imagem(img_param):
    """Função isolada para extrair dados via Tesseract (Single Responsibility)"""
    resultado = {"codigo": None, "valor": None}

    try:
        texto = pytesseract.image_to_string(img_param, lang='eng', config=CONFIG_TESSERACT)
        match_cod = re.search(r'\b\d{16}\b', texto)
        if match_cod:
            cod_extraido = str(int(match_cod.group(0)))
            if 4 <= len(cod_extraido) <= 6 and cod_extraido.endswith('0'):
                resultado["codigo"] = cod_extraido

        val1 = normalizar_valor(texto)
        if val1: resultado["valor"] = val1
    except Exception:
        pass
    return resultado


# ==========================================
# O CORAÇÃO DA IA: ALGORITMO DE CONSENSO
# ==========================================

# As 8 estratégias de pré-processamento. Ficam em nível de módulo para que a
# estratégia vencedora possa ser reaplicada depois (ao desenhar os destaques).
ESTRATEGIAS = [
    ("1. Padrão", lambda img: img.point(lambda x: 0 if x < 140 else 255, '1')),
    ("2. Escuro", lambda img: img.point(lambda x: 0 if x < 180 else 255, '1')),
    ("3. Nitidez", lambda img: ImageEnhance.Sharpness(img).enhance(2.5).point(lambda x: 0 if x < 160 else 255, '1')),
    ("4. Original", lambda img: img),
    ("5. Zoom 2x", lambda img: img.resize((img.width * 2, img.height * 2)).point(lambda x: 0 if x < 160 else 255, '1')),
    ("6. Alto Thresh", lambda img: img.point(lambda x: 0 if x < 210 else 255, '1')),
    ("7. Contraste", lambda img: ImageEnhance.Contrast(img).enhance(3.0).convert('1')),
    ("8. Engrossar", lambda img: img.filter(ImageFilter.MinFilter(3)).point(lambda x: 0 if x < 140 else 255, '1')),
]


def processar_pagina_com_consenso(img_gray, cod_esperado, val_esperado):
    """Aplica as 8 estratégias de imagem e realiza a votação em caso de divergência.

    Retorna um dicionário com o resultado da conferência e, adicionalmente, qual
    estratégia produziu cada leitura vencedora (usado para destacar o trecho lido).
    """

    # Guarda qual estratégia produziu cada leitura, para reconstruir a imagem vencedora.
    origem_cod, origem_val = {}, {}

    # 1ª Tentativa Rápida
    img_inicial = ESTRATEGIAS[0][1](img_gray)
    leitura_inicial = extrair_dados_da_imagem(img_inicial)

    cod_final = leitura_inicial["codigo"]
    val_final = leitura_inicial["valor"]
    if cod_final: origem_cod.setdefault(cod_final, 0)
    if val_final: origem_val.setdefault(val_final, 0)

    # Verifica necessidade de Consenso
    if not (cod_final == cod_esperado and val_final == val_esperado):
        candidatos_cod, candidatos_val = [], []
        if cod_final: candidatos_cod.append(cod_final)
        if val_final: candidatos_val.append(val_final)

        for indice, (nome, func_filtro) in enumerate(ESTRATEGIAS[1:], start=1):
            try:
                img_proc = func_filtro(img_gray)
                lidos = extrair_dados_da_imagem(img_proc)
                if lidos["codigo"]:
                    candidatos_cod.append(lidos["codigo"])
                    origem_cod.setdefault(lidos["codigo"], indice)
                if lidos["valor"]:
                    candidatos_val.append(lidos["valor"])
                    origem_val.setdefault(lidos["valor"], indice)
            except Exception:
                pass

        cod_final = Counter(candidatos_cod).most_common(1)[0][0] if candidatos_cod else "0"
        val_final = Counter(candidatos_val).most_common(1)[0][0] if candidatos_val else "0,00"

    status_cod = status_codigo(cod_final, cod_esperado)
    status_val = "OK" if val_final == val_esperado else "DIFERENTE"
    status_geral = "ERRO" if (status_cod != "OK" or status_val != "OK") else "OK"
    if cod_esperado == "N/A": status_geral = "FIM DA LISTA"

    return {
        "codigo": cod_final,
        "valor": val_final,
        "status_codigo": status_cod,
        "status_valor": status_val,
        "status_geral": status_geral,
        "estrategia_codigo": origem_cod.get(cod_final),
        "estrategia_valor": origem_val.get(val_final),
    }


# ==========================================
# LOCALIZAÇÃO VISUAL DO TRECHO LIDO
# ==========================================

def _corresponde(palavra: str, alvo: str, tipo: str) -> bool:
    """Diz se uma palavra devolvida pelo Tesseract corresponde ao valor conferido."""
    if tipo == "codigo":
        # O OCR lê a linha digitável completa (16 dígitos); o código é o número
        # sem os zeros à esquerda, então comparamos pelo valor inteiro.
        digitos = re.sub(r'\D', '', palavra)
        if not digitos: return False
        try:
            return str(int(digitos)) == alvo
        except ValueError:
            return False
    return normalizar_valor(palavra) == alvo


def localizar_bbox(img, alvo: str, tipo: str):
    """Devolve (x0, y0, x1, y1) do trecho `alvo` dentro da imagem, ou None."""
    if not alvo or alvo in ("0", "0,00", "N/A"):
        return None
    try:
        dados = pytesseract.image_to_data(
            img, lang='eng', config=CONFIG_TESSERACT, output_type=pytesseract.Output.DICT
        )
    except Exception:
        return None

    for k, palavra in enumerate(dados.get("text", [])):
        palavra = (palavra or "").strip()
        if not palavra:
            continue
        if _corresponde(palavra, alvo, tipo):
            x, y = dados["left"][k], dados["top"][k]
            return (x, y, x + dados["width"][k], y + dados["height"][k])
    return None


def _reduzir(box, escala):
    return tuple(int(v * escala) for v in box)


def _folga(box, fator=0.45, minimo=4.0):
    """Afasta o retângulo do texto, para o destaque emoldurar em vez de encobrir."""
    x0, y0, x1, y1 = box
    margem = max(minimo, (y1 - y0) * fator)
    return (x0 - margem, y0 - margem, x1 + margem, y1 + margem)


def gerar_imagem_anotada(img_gray, resultado, largura_max=1400):
    """Desenha na página original os retângulos de onde o código e o valor foram lidos.

    Os retângulos são localizados na imagem *filtrada* que venceu o consenso (é onde
    o OCR de fato enxergou o texto) e depois convertidos para as coordenadas da página
    original, de modo que o destaque aparece sobre a guia legível, não sobre a versão
    binarizada.
    """
    destaques = []
    for tipo, chave_estrategia, cor in (
        ("codigo", "estrategia_codigo", "#2563eb"),
        ("valor", "estrategia_valor", "#16a34a"),
    ):
        alvo = resultado.get(tipo)
        indice = resultado.get(chave_estrategia)
        if not alvo or indice is None:
            continue
        try:
            img_estrategia = ESTRATEGIAS[indice][1](img_gray)
        except Exception:
            continue
        box = localizar_bbox(img_estrategia, alvo, tipo)
        if not box:
            continue
        # Converte da imagem da estratégia (que pode ter sido redimensionada) para a original.
        fator = img_gray.width / img_estrategia.width
        destaques.append((_folga(tuple(v * fator for v in box)), cor, ESTRATEGIAS[indice][0]))

    escala = min(1.0, largura_max / img_gray.width)
    preview = img_gray.convert("RGB")
    if escala < 1.0:
        preview = preview.resize((int(img_gray.width * escala), int(img_gray.height * escala)))

    desenho = ImageDraw.Draw(preview)
    espessura = max(2, preview.width // 500)
    for box, cor, _nome in destaques:
        desenho.rectangle(_reduzir(box, escala), outline=cor, width=espessura)

    buffer = io.BytesIO()
    preview.save(buffer, format="JPEG", quality=70, optimize=True)
    return {
        "imagem": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
        "achou_codigo": any(c == "#2563eb" for _b, c, _n in destaques),
        "achou_valor": any(c == "#16a34a" for _b, c, _n in destaques),
    }


# ==========================================
# AUDITORIA (STREAM)
# ==========================================

COLUNAS_RELATORIO = [
    "Pagina", "Codigo (PDF Consulta)", "Codigo (OCR Boletos)", "Status Codigo",
    "Valor (PDF Consulta)", "Valor (OCR Boletos)", "Status Valor", "Status Geral",
]


def _poppler_kwargs():
    # Sem o diretório configurado, o pdf2image procura o Poppler no PATH.
    return {"poppler_path": POPPLER_PATH} if (POPPLER_PATH and os.path.isdir(POPPLER_PATH)) else {}


def realizar_auditoria_stream(caminho_boletos, caminho_consulta, dpi=DPI_PADRAO, com_imagem=True):
    """Gera um evento por página conferida, permitindo acompanhamento em tempo real.

    Converte o PDF página a página (em vez de carregar tudo de uma vez) para que o
    primeiro resultado apareça em segundos e o uso de memória não cresça com o
    tamanho do lote.
    """
    lista_mestre = extrair_lista_mestre(caminho_consulta)

    info = pdf2image.pdfinfo_from_path(caminho_boletos, **_poppler_kwargs())
    total_paginas = info["Pages"]

    yield {
        "tipo": "inicio",
        "total_paginas": total_paginas,
        "total_consulta": len(lista_mestre),
        "dpi": dpi,
    }

    for numero in range(1, total_paginas + 1):
        print(f"Processando página {numero}...")
        paginas = pdf2image.convert_from_path(
            caminho_boletos, dpi=dpi, first_page=numero, last_page=numero, **_poppler_kwargs()
        )
        if not paginas:
            continue

        indice = numero - 1
        item_esperado = lista_mestre[indice] if (lista_mestre and indice < len(lista_mestre)) else {"codigo": "N/A", "valor": "N/A"}

        img_gray = paginas[0].convert('L')
        resultado = processar_pagina_com_consenso(img_gray, item_esperado["codigo"], item_esperado["valor"])

        if resultado["status_geral"] == "ERRO":
            print(f"   [DIFERENÇA] Pag {numero} | Esp: {item_esperado['codigo']} - {item_esperado['valor']} | Lido: {resultado['codigo']} - {resultado['valor']}")

        linha = {
            "Pagina": numero,
            "Codigo (PDF Consulta)": item_esperado["codigo"],
            "Codigo (OCR Boletos)": resultado["codigo"],
            "Status Codigo": resultado["status_codigo"],
            "Valor (PDF Consulta)": item_esperado["valor"],
            "Valor (OCR Boletos)": resultado["valor"],
            "Status Valor": resultado["status_valor"],
            "Status Geral": resultado["status_geral"],
        }

        evento = {"tipo": "pagina", "pagina": numero, "total_paginas": total_paginas, "linha": linha}

        if com_imagem:
            indice_estrategia = resultado.get("estrategia_codigo")
            if indice_estrategia is None:
                indice_estrategia = resultado.get("estrategia_valor")
            evento["estrategia"] = ESTRATEGIAS[indice_estrategia][0] if indice_estrategia is not None else None
            try:
                evento.update(gerar_imagem_anotada(img_gray, resultado))
            except Exception as e:
                evento["erro_imagem"] = str(e)

        yield evento

    yield {"tipo": "fim", "total_paginas": total_paginas}
