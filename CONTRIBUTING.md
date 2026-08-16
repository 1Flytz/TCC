# Contribuindo com o PyConfer

Este documento organiza a divisão de trabalho do grupo. O objetivo é que cada membro
trabalhe na sua frente sem depender do andamento dos outros, usando o contrato de dados
(o JSON da API) como ponto de integração.

## 📍 Onde o projeto está

A **primeira fase está concluída**: o motor de conferência, a API REST, a persistência do
histórico e uma interface Web funcional. A aplicação confere um lote completo de guias,
mostra o resultado em tempo real, destaca na imagem de onde cada dado foi lido, classifica
as divergências e exporta o laudo. Há 80 testes automatizados cobrindo o motor, o ciclo de
vida das auditorias e o banco.

O que existe hoje é um protótipo **local e monousuário**. Foi decisão consciente: sem
certeza de que a conferência está correta, distribuir o sistema para uma equipe apenas
multiplicaria o erro.

## 🎯 A meta da próxima fase

**Tornar o PyConfer colaborativo.** É a maior lacuna entre o que o Capítulo 1 promete —
uma plataforma para equipes — e o que a aplicação faz hoje.

Essa meta foi escolhida porque exige as três frentes ao mesmo tempo: banco de dados
compartilhado, autenticação na API e telas por usuário. Cada frente abaixo tem trabalho
próprio e um ponto de partida que **não depende de ninguém**.

## 👥 Divisão de responsabilidades

### 1. Motor e API — Felipe

Dono do `core/` e do `api/`, e do contrato de dados que as outras frentes consomem.

- Validação do **dígito verificador** da linha digitável
- **Autenticação** na API (o front-end depende disso)
- **Endpoints de métricas** (o painel depende disso)
- Medição comparativa com a **conferência manual** — o número que falta para fechar o
  objetivo geral do trabalho

**Regra de ouro:** quando outra frente precisar de um dado novo, ela pede. Ninguém altera
o motor ou a API por conta própria.

### 2. Banco de dados — [nome do colega]

Hoje o projeto usa SQLite, com duas tabelas, guardando apenas o histórico das conferências.

- Migrar para **PostgreSQL ou MySQL** — requisito para múltiplos usuários e o que o
  Capítulo 2 originalmente previa
- Modelar **usuários e permissões**: quem executou cada auditoria, quem enxerga o quê
- **Persistir a lista mestre**, que hoje é lida do PDF a cada execução e descartada
- Consultas de **métricas**: acurácia ao longo do tempo, por lote, por operador
- Política de **retenção e backup** — são dados de contribuinte, e isso pesa na avaliação

**Comece por:** desenhar o modelo de dados novo e comparar com o atual (`core/armazenamento.py`).
Não depende de ninguém.

### 3. Front-end — [nome do colega]

A interface atual é JavaScript puro, sem framework nem etapa de compilação. Foi construída
como **base**, não como produto final.

- Tela de **login** e sessão
- **Painel de métricas**: conformidade ao longo do tempo, lotes com mais divergência
- Fluxo de **revisão**: marcar uma divergência como conferida, registrar observação — hoje
  o operador confere e nada fica registrado
- Decidir entre migrar para **React ou Vue** ou manter JavaScript puro — e **justificar a
  escolha por escrito**, porque essa justificativa entra na monografia

**Comece por:** desenhar as telas novas e listar de quais dados cada uma precisa. Os
endpoints vêm depois; o desenho não espera.

### 4. Documentação — [nome do colega]

- As **sete figuras** marcadas em amarelo no documento: quatro diagramas (casos de uso,
  sequência, classes, arquitetura) e três capturas de tela
- **Conferir as catorze referências**, uma a uma, abrindo cada link
- Limpeza no Word: atualizar o sumário, aplicar legendas de verdade na Tabela 1 e no
  Quadro 1, corrigir o título órfão da seção 1.3
- Escrever as seções técnicas **do trabalho das outras frentes**, conforme cada parte fica
  pronta
- Manter o Capítulo 3 em dia

**Comece por:** conferir a bibliografia. Se alguma referência não se confirmar, o texto
muda — melhor descobrir antes de diagramar.

## 🔒 Duas regras para não quebrar o que funciona

Três pessoas mexendo num sistema que já roda é risco real. Duas práticas resolvem:

**1. Cada frente na sua branch, integrada por pull request.** Ninguém envia direto para a
`main`.

**2. Os testes têm que passar antes de qualquer merge:**

```bash
python -m pytest
```

Se quebrou, não entra. A suíte roda em cerca de dois segundos e já pegou erro real de
lógica neste projeto — não é formalidade.

## 🖼️ O princípio da interface

Uma decisão de projeto que orienta tudo na camada visual e **não deve ser abandonada**:

> O auditor precisa enxergar **de onde** o sistema tirou cada informação, não apenas qual
> foi o resultado.

É por isso que a tela mostra a imagem da própria guia com retângulos marcando as regiões
lidas. Diante de uma divergência, o operador confirma em segundos sem reabrir o documento
original. Qualquer tela nova deve orbitar esse princípio, não competir com ele.

## 💻 Ambiente de execução

O projeto roda **localmente**, sem deploy. Cada membro clona o repositório e segue o setup
do `README.md`. Os documentos contêm dados sigilosos de contribuintes e, por isso, não são
versionados nem enviados a serviços externos — restrição que vale também para a fase
colaborativa, quando o banco passar a rodar em servidor próprio.
