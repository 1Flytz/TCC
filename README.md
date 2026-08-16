# PyConfer — Conferência automatizada de guias

Projeto de TCC. Automatiza a conferência de guias financeiras (RLC / fichas): compara o
**código de cadastro** e o **valor** lidos por OCR nas guias digitalizadas com os dados do
PDF de consulta, e aponta as divergências.

O que antes era conferência manual, folha a folha, vira um relatório por lote — com a
imagem de cada guia destacando exatamente de onde o código e o valor foram lidos.

## 🧠 Como funciona

1. **Lista mestre** — o PDF de consulta é lido como texto (`pypdf`), extraindo os pares
   código/valor esperados.
2. **OCR das guias** — cada página do PDF de boletos vira imagem (`pdf2image` + Poppler) e
   passa pelo Tesseract.
3. **Consenso** — se a primeira leitura não bate com o esperado, a página é reprocessada
   com **8 estratégias diferentes** de tratamento de imagem (limiares, nitidez, contraste,
   zoom, engrossamento de traço) e o resultado sai por **votação por maioria**. Isso reduz
   o erro aleatório de leitura sem cair no viés de confirmar o esperado a qualquer custo.
4. **Comparação** — código e valor são conferidos contra a lista mestre. A comparação de
   código é **exata**: não há tolerância a confusões de OCR, porque no lote de referência
   existem 50 pares de cadastros distintos separados por exatamente essas trocas
   (`113640`/`113840`, `119000`/`119090`). Tolerá-las faria uma divergência real ser
   reportada como conforme, em silêncio.

## 📋 Pré-requisitos

- **Python 3.10+** — [download](https://www.python.org/downloads/)
- **Git** — [download](https://git-scm.com/)
- **Tesseract-OCR** — [download](https://github.com/UB-Mannheim/tesseract/wiki)
- **Poppler** — [download](https://github.com/oschwartz10612/poppler-windows/releases/)

Tesseract e Poppler são programas externos, não pacotes Python: precisam ser instalados à
parte. No Windows, o Poppler é um ZIP — basta extrair e guardar o caminho da pasta `bin`.

## 🚀 Instalação

```bash
git clone https://github.com/1Flytz/TCC.git
cd TCC
python -m venv .venv
```

Ative o ambiente virtual:

| Sistema | Comando |
|---|---|
| Windows (PowerShell) | `.\.venv\Scripts\Activate.ps1` |
| Windows (CMD) | `.venv\Scripts\activate.bat` |
| macOS / Linux | `source .venv/bin/activate` |

E instale as dependências:

```bash
pip install -r requirements.txt
```

## ⚙️ Configuração do Tesseract e do Poppler

Por padrão o projeto procura em `C:\Program Files\Tesseract-OCR\tesseract.exe` e
`C:\Program Files\poppler\Library\bin`. **Se você instalou em outro lugar, não precisa
editar código** — defina as variáveis de ambiente:

```bash
$env:TESSERACT_CMD = "C:\caminho\para\tesseract.exe"
$env:POPPLER_PATH  = "C:\caminho\para\poppler\Library\bin"
```

Se o caminho configurado não existir, o projeto ainda tenta o `tesseract` disponível no
`PATH` do sistema. Pela linha de comando também dá para passar direto, com `--tesseract` e
`--poppler`.

## 💻 Como usar

### Aplicação web (recomendado)

```bash
python -m uvicorn api.main:app --reload
```

Abra **http://localhost:8000**. A tela permite enviar os dois PDFs, escolher a resolução e
acompanhar a conferência **página a página, em tempo real**, com o trecho lido destacado na
imagem da guia. Ao final, o relatório sai em CSV.

### Linha de comando

Para rodar um lote sem interface:

```bash
python conferidor.py --boletos docs/boletos.pdf --consulta docs/consulta.pdf
```

| Opção | Para que serve |
|---|---|
| `--boletos` | PDF com as guias digitalizadas |
| `--consulta` | PDF de consulta (lista mestre) |
| `--saida` | Caminho do CSV de saída |
| `--dpi` | Resolução da conversão (padrão: 500) |
| `--tesseract` | Executável do Tesseract |
| `--poppler` | Pasta `bin` do Poppler |

### Sobre a resolução (DPI)

O padrão é **500**, que é o que garante a distinção entre dígitos parecidos (6/8, 0/9,
1/7). Resoluções menores são mais rápidas e servem para demonstração, mas erram mais: em
DPI 200 o lote de referência produz uma leitura incorreta que em DPI 500 sai correta.

## 📄 Os PDFs de entrada

Arquivos `.pdf` **não são versionados** (estão no `.gitignore`), porque contêm dados reais
de contribuintes. Depois de clonar, o repositório vem sem eles.

- Pela **aplicação web** isso não importa: os arquivos são enviados pela tela.
- Pela **linha de comando**, os caminhos padrão são `docs/boletos.pdf` e
  `docs/consulta.pdf` — coloque os seus ali, ou aponte outro caminho com `--boletos` e
  `--consulta`.

## 🔌 API

Documentação interativa (Swagger) em **http://localhost:8000/docs**.

| Método | Rota | O que faz |
|---|---|---|
| `POST` | `/api/v1/auditorias` | Envia os dois PDFs e inicia a conferência; devolve um `job_id` |
| `GET` | `/api/v1/auditorias/{job_id}/eventos` | Acompanhamento em tempo real (Server-Sent Events) |
| `GET` | `/api/v1/auditorias/{job_id}` | Situação atual de uma auditoria |
| `GET` | `/api/v1/auditorias/{job_id}/relatorio.csv` | Baixa o relatório |
| `GET` | `/api/v1/auditorias` | Histórico das auditorias já realizadas |

Cada mensagem do stream é um JSON com `tipo` igual a `inicio`, `pagina`, `erro` ou `fim`.
É esse formato que serve de contrato para o front-end.

## 🗄️ Histórico

O resultado de cada auditoria é gravado em SQLite (`pyconfer.db`, na raiz do projeto),
guia a guia. Assim o histórico sobrevive ao reinício do servidor, e uma auditoria
interrompida no meio mantém registrado o que já havia sido conferido. Para usar outro
arquivo de banco, defina `PYCONFER_DB`.

A imagem anotada de cada página **não** é guardada: pesa ~127 KB e serve apenas para a
conferência visual do momento.

## 🧪 Testes

```bash
python -m pytest
```

A suíte roda em cerca de um segundo e não depende de Tesseract instalado nem de PDF de
apoio — nos testes o leitor de PDF é substituído por texto fixo. Cobre o motor de
conferência, o ciclo de vida das auditorias em memória e a persistência.

## 📁 Estrutura do projeto

```
TCC/
├── core/
│   ├── engine.py          # Motor: OCR, consenso, comparação
│   └── armazenamento.py   # Histórico em SQLite
├── api/
│   └── main.py            # API REST (FastAPI) + streaming SSE
├── static/                # Front-end (sem build, sem Node)
├── tests/                 # Suíte de testes
├── docs/                  # PDFs de entrada (não versionados)
├── conferidor.py          # Execução por linha de comando
├── requirements.txt       # Dependências Python
└── README.md              # Este arquivo
```

O motor não conhece a API, e a API não conhece o front-end: as camadas se comunicam pelo
contrato JSON, o que permite que o grupo trabalhe em paralelo.

## 🤝 Contribuindo

A divisão de responsabilidades do grupo está no [CONTRIBUTING.md](CONTRIBUTING.md).

Crie uma branch para sua alteração e abra um pull request. Rode `python -m pytest` antes
de enviar.

---
**Última atualização:** Agosto/2026
