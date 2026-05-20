import pdf2image
import pytesseract
import re
import pandas as pd
import pypdf
import os
from PIL import ImageEnhance, ImageFilter
from collections import Counter

# --- CONFIGURAÇÕES ---
# Caminhos corrigidos para apontar para os arquivos PDF específicos
caminho_pdf_boletos = r'C:\Users\Felipe\Pictures\testee\RLC. 3441 recort.pdf'
caminho_pdf_consulta = r'C:\Users\Felipe\Pictures\testee\consultaRlc.pdf'

caminho_tesseract = r'C:\Program Files\Tesseract-OCR\tesseract.exe' # Local onde o OCR será instalado
pytesseract.pytesseract.tesseract_cmd = caminho_tesseract

# Adiciona o diretório bin do Poppler ao PATH do sistema para esta execução
poppler_path = r"C:\Users\Felipe\AppData\Local\Microsoft\WinGet\Packages\oschwartz10612.Poppler_Microsoft.Winget.Source_8wekyb3d8bbwe\poppler-25.07.0\Library\bin"
os.environ["PATH"] += os.pathsep + poppler_path

def extrair_lista_mestre(caminho_consulta):
    print(f"Lendo códigos e valores do PDF de consulta: {caminho_consulta}...")
    dados_encontrados = []
    
    try:
        reader = pypdf.PdfReader(caminho_consulta)
        for pagina in reader.pages:
            texto = pagina.extract_text()
            if not texto: continue
            
            # --- EXTRAÇÃO DE CÓDIGOS ---
            # Busca códigos de 5 ou 6 dígitos terminados em -0 (ex: 1234-0 ou 12345-0)
            codigos_raw = re.findall(r'\b(\d{4,5})-0\b', texto)
            
            # --- EXTRAÇÃO DE VALORES ---
            # Busca valores: 2 ou 3 dígitos, vírgula, 2 dígitos (ex: 123,45 ou 45,90)
            valores_raw = re.findall(r'\b\d{2,3},\d{2}\b', texto)

            # Vamos assumir que estão na mesma ordem (Linha a Linha)
            # Se a quantidade for diferente, pode dar desalinhamento, mas vamos tentar parear.
            max_len = max(len(codigos_raw), len(valores_raw))
            
            for k in range(max_len):
                cod_val = codigos_raw[k] + "0" if k < len(codigos_raw) else "N/A"
                val_monetario = valores_raw[k] if k < len(valores_raw) else "N/A"
                
                dados_encontrados.append({
                    "codigo": cod_val,
                    "valor": val_monetario
                })
                
    except Exception as e:
        print(f"Erro ao ler PDF de consulta: {e}")
        return []

    print(f"Encontrados {len(dados_encontrados)} registros na lista mestre.")
    return dados_encontrados

def realizar_auditoria():
    # 1. Carrega a Lista Mestre do PDF de Consulta
    lista_mestre = extrair_lista_mestre(caminho_pdf_consulta)
    
    print("Iniciando conversão de PDF para Imagem (OCR) com DPI 400 (Alta Resolução)...")
    
    # Aumentando DPI para 500 (Máxima Resolução Viável) para diferenciar 6/8 e 0/9
    print("   -> Configurando DPI 500 para evitar confusões 6-8 e 0-9...")
    paginas = pdf2image.convert_from_path(caminho_pdf_boletos, dpi=500)
    
    relatorio = []

    # Configuração: PSM 6 (bloco único) + Whitelist apenas números e vírgula
    # Ativa modo numérico para reduzir trocas 0/9 e 6/8
    config_tesseract = (
        r'--psm 6 '
        r'-c tessedit_char_whitelist=0123456789,. '
        r'-c classify_bln_numeric_mode=1 '
        r'-c tessedit_char_blacklist=IlOo'
    )

    def extrair_dados_da_imagem(img_param):
        """Função auxiliar para tentar extrair código e valor da imagem"""
        resultado = {"codigo": None, "valor": None}

        def normalizar_valor(texto_extraido: str):
            if not texto_extraido:
                return None
            # Normaliza caracteres parecidos com vírgula
            texto_n = texto_extraido.replace('‚', ',').replace('’', ',').replace('`', ',').replace('´', ',')
            texto_n = texto_n.replace('·', '.').replace('•', '.').replace('·', '.')
            # Primeiro tenta valor no formato real: 2-3 inteiros + vírgula + 2 decimais
            m1 = re.search(r'\b\d{2,3}[.,]\d{2}\b', texto_n)
            if m1:
                val = m1.group(0).replace('.', ',')
                # Valida faixa razoável (até 999,99)
                int_part = int(val.split(',')[0])
                if int_part <= 999:
                    return val
            # Relaxado: aceita 1-7 inteiros (caso OCR junte algo), ainda com 2 decimais
            m2 = re.search(r'\b\d{1,7}[.,]\d{2}\b', texto_n)
            if m2:
                val2 = m2.group(0).replace('.', ',')
                try:
                    int_part2 = int(val2.split(',')[0])
                    # Rejeita valores absurdos (>999)
                    if int_part2 <= 999:
                        return val2
                except:
                    pass
            return None

        try:
            texto = pytesseract.image_to_string(img_param, lang='eng', config=config_tesseract)
            
            # --- Código: aceita qualquer 16 dígitos para não mascarar erros reais ---
            match_cod = re.search(r'\b\d{16}\b', texto)
            if match_cod:
                raw_val = match_cod.group(0)
                cod_extraido = str(int(raw_val))  # remove zeros à esquerda
                # Valida: código deve ter 4-6 dígitos e terminar em 0
                if 4 <= len(cod_extraido) <= 6 and cod_extraido.endswith('0'):
                    resultado["codigo"] = cod_extraido
                # Se não passar na validação, deixa None para acionar votação
            
            # Valor (primeira tentativa)
            val1 = normalizar_valor(texto)
            if val1:
                resultado["valor"] = val1

            # Fallback específico para valor: se não achou, roda OCR focado em valor (psm 7, binário)
            if not resultado["valor"]:
                img_val = img_param.convert('L').point(lambda x: 0 if x < 170 else 255, '1')
                cfg_val = (
                    r'--psm 7 '
                    r'-c tessedit_char_whitelist=0123456789,. '
                    r'-c classify_bln_numeric_mode=1 '
                    r'-c tessedit_char_blacklist=IlOo'
                )
                texto_val = pytesseract.image_to_string(img_val, lang='eng', config=cfg_val)
                val2 = normalizar_valor(texto_val)
                if val2:
                    resultado["valor"] = val2
                else:
                    # Mais um fallback: threshold mais leve
                    img_val2 = img_param.convert('L').point(lambda x: 0 if x < 140 else 255, '1')
                    texto_val2 = pytesseract.image_to_string(img_val2, lang='eng', config=cfg_val)
                    val3 = normalizar_valor(texto_val2)
                    if val3:
                        resultado["valor"] = val3

        except:
            pass
        return resultado

    def status_codigo(cod_lido: str, cod_esp: str):
        """Compara apenas sufixo (5 ou 6 dígitos, terminando em 0). Tolera confusões específicas de OCR (8↔6, 0↔9)."""
        if not cod_lido or not cod_esp or cod_esp == "N/A":
            return "DIFERENTE"

        def pick_suf(cod: str):
            suf6 = cod[-6:]
            suf5 = cod[-5:]
            if len(suf6) == 6 and suf6.endswith("0"):
                return suf6
            if len(suf5) == 5 and suf5.endswith("0"):
                return suf5
            return ""

        def confusao_aceitavel(a: str, b: str):
            """Verifica se as diferenças são apenas confusões comuns de OCR (máx 2)."""
            pares_confusos = {('8', '6'), ('6', '8'), ('0', '9'), ('9', '0'), ('1', '7'), ('7', '1'), ('5', 'S'), ('S', '5'), ('2', 'Z'), ('Z', '2')}
            if len(a) != len(b) or not a or not b:
                return False
            difs = [(x, y) for x, y in zip(a, b) if x != y]
            # Aceita até 2 confusões específicas de OCR
            if len(difs) > 2:
                return False
            return all(d in pares_confusos for d in difs)

        suf_lido = pick_suf(cod_lido)
        suf_esp = pick_suf(cod_esp)

        if suf_lido and suf_esp:
            if suf_lido == suf_esp:
                return "OK"
            # Aceita se for apenas uma confusão específica de OCR
            if confusao_aceitavel(suf_lido, suf_esp):
                return "OK"

        return "DIFERENTE"

    for i, pagina_img in enumerate(paginas):
        pagina_num = i + 1
        print(f"Processando página {pagina_num}...")

        item_esperado = lista_mestre[i] if (lista_mestre and i < len(lista_mestre)) else {"codigo": "N/A", "valor": "N/A"}
        cod_esperado = item_esperado["codigo"]
        val_esperado = item_esperado["valor"]
        
        img_gray = pagina_img.convert('L') # Base em cinza
        
        # --- ESTRATÉGIA DE CONSENSO (VOTING SYSTEM) ---
        # 1. Tenta a leitura padrão (Rápida e geralmente correta)
        # 2. Se a leitura padrão não bater perfeitamente com o esperado, aciona o "Conselho de Filtros"
        # 3. O "Conselho" roda vários filtros e faz uma votação. O valor mais frequente ganha.
        #    Isso evita o viés de confirmar o esperado a qualquer custo (falso negativo)
        #    e reduz erros de leitura aleatórios (falso positivo).

        estrategias = [
            ("1. Padrão (Thresh 140)", lambda img: img.point(lambda x: 0 if x < 140 else 255, '1')),
            ("2. Escuro (Thresh 180)", lambda img: img.point(lambda x: 0 if x < 180 else 255, '1')),
            ("3. Nitidez + Thresh 160", lambda img: ImageEnhance.Sharpness(img).enhance(2.5).point(lambda x: 0 if x < 160 else 255, '1')),
            ("4. Original Cinza", lambda img: img),
            ("5. Zoom 2x (Suave)", lambda img: img.resize((img.width * 2, img.height * 2)).point(lambda x: 0 if x < 160 else 255, '1')),
            ("6. Muito Escuro (Thresh 210)", lambda img: img.point(lambda x: 0 if x < 210 else 255, '1')),
            ("7. Alto Contraste", lambda img: ImageEnhance.Contrast(img).enhance(3.0).convert('1')),
            ("8. Engrossar Traços", lambda img: img.filter(ImageFilter.MinFilter(3)).point(lambda x: 0 if x < 140 else 255, '1'))
        ]
        
        # Passo 1: Leitura Inicial (Estratégia 1)
        img_inicial = estrategias[0][1](img_gray)
        leitura_inicial = extrair_dados_da_imagem(img_inicial)
        
        cod_final = leitura_inicial["codigo"]
        val_final = leitura_inicial["valor"]
        
        # Verificação Rápida
        match_cod = (cod_final == cod_esperado)
        match_val = (val_final == val_esperado)
        
        # Se bater tudo perfeitamente na primeira, confiamos (Low Risk)
        # Se NÃO bater, ou se não leu nada, chamamos o Conselho (Full Scan)
        if not (match_cod and match_val):
            # print(f"   -> Leitura inicial divergente ou falha. Acionando verificação profunda...")
            
            candidatos_cod = []
            candidatos_val = []
            
            # Adiciona a leitura inicial (se existir) à urna
            if cod_final: candidatos_cod.append(cod_final)
            if val_final: candidatos_val.append(val_final)
            
            # Executa as outras estratégias
            for nome, func_filtro in estrategias[1:]:  # Pula a primeira que já foi
                try:
                    img_proc = func_filtro(img_gray)
                    lidos = extrair_dados_da_imagem(img_proc)
                    if lidos["codigo"]: candidatos_cod.append(lidos["codigo"])
                    if lidos["valor"]: candidatos_val.append(lidos["valor"])
                except: pass
                
            # Apuração dos Votos - CÓDIGO (maioria, sem forçar para o esperado)
            if candidatos_cod:
                eleito_cod, votos_cod = Counter(candidatos_cod).most_common(1)[0]
                cod_final = eleito_cod
            else:
                cod_final = "0"
                
            # Apuração dos Votos - VALOR
            if candidatos_val:
                eleito_val, votos_val = Counter(candidatos_val).most_common(1)[0]
                val_final = eleito_val
            else:
                val_final = "0,00"

        # Define status final com base na decisão do sistema (Simples ou Consenso)
        status_cod = status_codigo(cod_final, cod_esperado)
        status_val = "OK" if val_final == val_esperado else "DIFERENTE"
        
        status_geral = "OK"
        if status_cod != "OK" or status_val != "OK": status_geral = "ERRO"
        
        if cod_esperado == "N/A": status_geral = "FIM DA LISTA"

        if status_geral == "ERRO":
            print(f"   [DIFERENÇA CONFIRMADA] Pag {pagina_num} | Esperado: {cod_esperado} - {val_esperado} | Lido: {cod_final} - {val_final}")

        relatorio.append({
            "Pagina": pagina_num,
            "Codigo (PDF Consulta)": cod_esperado,
            "Codigo (OCR Boletos)": cod_final,
            "Status Codigo": status_cod,
            "Valor (PDF Consulta)": val_esperado,
            "Valor (OCR Boletos)": val_final,
            "Status Valor": status_val,
            "Status Geral": status_geral
        })

    # Salva o resultado final no Excel/CSV
    nome_arquivo = 'Relatorio_Final_Python.csv'
    try:
        df = pd.DataFrame(relatorio)
        df.to_csv(nome_arquivo, index=False, sep=';', encoding='latin1')
        print(f"Auditoria concluída! Arquivo '{nome_arquivo}' gerado.")
    except PermissionError:
        print(f"ERRO: O arquivo '{nome_arquivo}' está aberto. Salvando como 'Relatorio_Final_Python_Alternativo.csv'...")
        df.to_csv('Relatorio_Final_Python_Alternativo.csv', index=False, sep=';', encoding='latin1')
        print("Auditoria concluída! Arquivo 'Relatorio_Final_Python_Alternativo.csv' gerado.")

if __name__ == "__main__":
    realizar_auditoria()