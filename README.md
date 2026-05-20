# Conferidor - Extração de Dados de PDFs

Projeto TCC para extração e processamento de dados de PDFs de boletos e consultas.

## 📋 Pré-requisitos

Antes de executar o projeto, certifique-se que você tem instalado:

- **Python 3.8+** - [Download aqui](https://www.python.org/downloads/)
- **Git** - [Download aqui](https://git-scm.com/)
- **Tesseract-OCR** - [Download aqui](https://github.com/UB-Mannheim/tesseract/wiki)
- **Poppler** - [Download aqui](https://github.com/oschwartz10612/poppler-windows/releases/)

### ⚠️ Instalação de Dependências Externas (Windows)

1. **Tesseract-OCR**: Baixe e execute o instalador, anote o caminho de instalação (ex: `C:\Program Files\Tesseract-OCR`)

2. **Poppler**: Extraia o ZIP e anote o caminho da pasta `bin`

3. Atualize os caminhos em `conferidor.py`:
```python
caminho_tesseract = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
poppler_path = r"C:\caminho\para\poppler\bin"
```

## 🚀 Como Executar

### 1. Clone o repositório
```bash
git clone https://github.com/seu-usuario/Conf.git
cd Conf
```

### 2. Crie um ambiente virtual
```bash
python -m venv .venv
```

### 3. Ative o ambiente virtual

**Windows (PowerShell):**
```bash
.\.venv\Scripts\Activate.ps1
```

**Windows (CMD):**
```bash
.venv\Scripts\activate.bat
```

**macOS/Linux:**
```bash
source .venv/bin/activate
```

### 4. Instale as dependências
```bash
pip install -r requirements.txt
```

### 5. Execute o projeto
```bash
python conferidor.py
```

## 📁 Estrutura do Projeto

```
Conf/
├── conferidor.py              # Script principal
├── Relatorio_Final_Python.csv # Relatório gerado
├── requirements.txt           # Dependências Python
├── .gitignore                # Arquivos ignorados pelo Git
└── README.md                 # Este arquivo
```

## 👥 Grupo TCC

- Membro 1
- Membro 2
- Membro 3

## 📝 Notas Importantes

- Os caminhos dos PDFs devem ser atualizados em `conferidor.py` conforme necessário
- Certifique-se de instalar o Tesseract e Poppler corretamente
- O ambiente virtual deve ser ativado antes de executar o projeto

## 🤝 Contribuindo

Faça um fork do repositório, crie uma branch para sua feature e envie um pull request.

---
**Última atualização:** Maio/2026
