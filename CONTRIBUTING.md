# Contribuindo com o PyConfer

Este documento organiza a divisão de trabalho do grupo para o TCC. O objetivo é que cada
membro consiga trabalhar na sua frente sem depender do andamento dos outros, usando o
contrato de dados (JSON da API) como ponto de integração.

## 👥 Divisão de responsabilidades

### 1. Backend / Core Engine + API — Felipe
- Motor de extração e conferência (`conferidor.py`): OCR, algoritmo de consenso, matching código/valor.
- Encapsulamento em API REST (FastAPI), com documentação automática via Swagger.
- **Entregável para o resto do grupo:** o contrato JSON de request/response (documentado no
  Swagger). É a partir dele que o Front-end e a camada de Dados podem trabalhar em paralelo,
  mesmo antes do motor estar 100% estável.

### 2. Front-end / Dashboard — [nome do colega]
- Tela de upload dos PDFs (boletos + consulta).
- Visualização dos resultados por página (status OK / DIFERENTE / ERRO), com destaque para
  divergências.
- Pode começar com um JSON de exemplo (mock) fornecido pelo backend, sem esperar a API real
  estar pronta.

### 3. Dados / Persistência + QA — [nome do colega]
- Modelagem do banco de dados para histórico de auditorias (por lote, por página, timestamps).
- Testes automatizados do motor (`status_codigo`, `extrair_lista_mestre`, casos de borda).
- Métricas de acurácia (precisão/recall) comparando o resultado automatizado com conferência
  manual — esse dado sustenta os resultados do Capítulo 3 da monografia.
- Pode começar direto em cima do código atual, sem depender da API estar pronta.

### 4. Documentação Acadêmica + Integração — [nome do colega]
- Consolidação do Capítulo 3 (metodologia, diagramas UML de arquitetura, casos de uso e
  sequência).
- Justificativa técnica das escolhas de cada parte (FastAPI, banco de dados, front-end).
- Escreve o trecho técnico das partes; este papel costura tudo em um texto
  coeso e cuida do README final / roteiro de demonstração para a banca.

## 🔗 Ordem de dependência

1. Backend define e publica o contrato JSON (mesmo com o motor ainda instável).
2. Front-end e Dados/QA trabalham em paralelo em cima do contrato.
3. Documentação consolida o trabalho de todos ao final — mas pode começar o esqueleto do
   capítulo desde já.

## 💻 Ambiente de execução

Por enquanto o projeto roda **localmente** (sem deploy). Cada membro clona o repositório e
segue o setup do `README.md`. Como o código é compartilhado apenas entre o grupo, não há
necessidade de autenticação ou hospedagem externa nesta fase.

## 🎯 Nota pessoal

<!-- Felipe: descreva aqui a sua visão de como quer que a demonstração/visualização
     do app funcione, para alinhar com o grupo antes de começarem a construir em cima disso. -->
