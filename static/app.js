/**
 * PyConfer - front-end de acompanhamento.
 *
 * Envia os PDFs para a API, abre uma conexão SSE e vai desenhando o resultado de
 * cada página conforme o motor de OCR termina de conferi-la.
 */

const $ = (id) => document.getElementById(id);

const formulario = $("formulario");
const botaoIniciar = $("botao-iniciar");
const aviso = $("aviso");
const painel = $("painel");
const corpoTabela = $("corpo-tabela");
const visor = $("visor");
const barra = $("barra");
const situacao = $("situacao");

// Guarda a imagem anotada de cada página para permitir revisitar linhas da tabela.
const paginas = new Map();
let contadores = { processadas: 0, conformes: 0, divergentes: 0, total: null };
let fonteEventos = null;

formulario.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  aviso.textContent = "";

  const dados = new FormData(formulario);
  const dpi = dados.get("dpi");
  dados.delete("dpi");

  botaoIniciar.disabled = true;
  botaoIniciar.textContent = "Enviando documentos…";

  try {
    const resposta = await fetch(`/api/v1/auditorias?dpi=${dpi}`, { method: "POST", body: dados });
    if (!resposta.ok) {
      const erro = await resposta.json().catch(() => ({}));
      throw new Error(erro.detail || `Falha ao iniciar (HTTP ${resposta.status}).`);
    }
    const { job_id } = await resposta.json();
    reiniciarPainel();
    acompanhar(job_id);
  } catch (erro) {
    aviso.textContent = erro.message;
    botaoIniciar.disabled = false;
    botaoIniciar.textContent = "Iniciar auditoria";
  }
});

function reiniciarPainel() {
  paginas.clear();
  corpoTabela.innerHTML = "";
  contadores = { processadas: 0, conformes: 0, divergentes: 0, total: null };
  atualizarIndicadores();
  barra.style.width = "0%";
  visor.innerHTML = '<p class="vazio">Aguardando a primeira página…</p>';
  $("comparativo").hidden = true;
  $("etiqueta-estrategia").hidden = true;
  $("legenda-visor").hidden = true;
  $("link-csv").hidden = true;
  painel.hidden = false;
  painel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function acompanhar(jobId) {
  situacao.textContent = "Convertendo o PDF e lendo a lista mestre…";
  botaoIniciar.textContent = "Auditoria em andamento…";

  fonteEventos = new EventSource(`/api/v1/auditorias/${jobId}/eventos`);

  fonteEventos.onmessage = (mensagem) => {
    const evento = JSON.parse(mensagem.data);

    if (evento.tipo === "inicio") {
      contadores.total = evento.total_paginas;
      situacao.textContent = `${evento.total_paginas} guias encontradas · ${evento.total_consulta} lançamentos na consulta · ${evento.dpi} DPI`;
      atualizarIndicadores();
    } else if (evento.tipo === "pagina") {
      registrarPagina(evento);
    } else if (evento.tipo === "erro") {
      situacao.textContent = "";
      aviso.textContent = evento.mensagem;
      encerrar(jobId, false);
    } else if (evento.tipo === "fim") {
      situacao.textContent = `Auditoria concluída — ${contadores.processadas} guias conferidas.`;
      encerrar(jobId, true);
    }
  };

  fonteEventos.onerror = () => {
    // O navegador tentaria reconectar sozinho e reprocessar; encerramos explicitamente.
    if (fonteEventos && fonteEventos.readyState === EventSource.CLOSED) {
      encerrar(jobId, contadores.processadas > 0);
    }
  };
}

function encerrar(jobId, houveResultado) {
  if (fonteEventos) { fonteEventos.close(); fonteEventos = null; }
  botaoIniciar.disabled = false;
  botaoIniciar.textContent = "Iniciar nova auditoria";
  if (houveResultado) {
    const link = $("link-csv");
    link.href = `/api/v1/auditorias/${jobId}/relatorio.csv`;
    link.hidden = false;
  }
}

function registrarPagina(evento) {
  const linha = evento.linha;
  const divergente = linha["Status Geral"] === "ERRO";

  contadores.processadas += 1;
  if (linha["Status Geral"] === "OK") contadores.conformes += 1;
  if (divergente) contadores.divergentes += 1;
  atualizarIndicadores();

  if (contadores.total) {
    barra.style.width = `${(contadores.processadas / contadores.total) * 100}%`;
    situacao.textContent = `Conferindo… página ${evento.pagina} de ${contadores.total}`;
  }

  paginas.set(evento.pagina, evento);
  adicionarLinha(evento, divergente);
  mostrarPagina(evento.pagina);
}

function adicionarLinha(evento, divergente) {
  const linha = evento.linha;
  const tr = document.createElement("tr");
  tr.dataset.pagina = evento.pagina;
  tr.dataset.divergente = divergente ? "1" : "0";
  if (divergente) tr.classList.add("divergente");
  if ($("filtro-divergencias").checked && !divergente) tr.hidden = true;

  const codigoDiverge = linha["Status Codigo"] !== "OK";
  const valorDiverge = linha["Status Valor"] !== "OK";

  tr.innerHTML = `
    <td>${evento.pagina}</td>
    <td>${linha["Codigo (PDF Consulta)"]}</td>
    <td class="${codigoDiverge ? "divergencia" : ""}">${linha["Codigo (OCR Boletos)"]}</td>
    <td>${linha["Valor (PDF Consulta)"]}</td>
    <td class="${valorDiverge ? "divergencia" : ""}">${linha["Valor (OCR Boletos)"]}</td>
    <td>${selo(linha["Status Geral"])}</td>
  `;

  tr.addEventListener("click", () => mostrarPagina(evento.pagina));
  corpoTabela.appendChild(tr);
}

function selo(status) {
  const classe = status === "OK" ? "ok" : status === "ERRO" ? "erro" : "";
  const texto = status === "ERRO" ? "Divergente" : status === "OK" ? "Conforme" : status;
  return `<span class="selo ${classe}">${texto}</span>`;
}

function mostrarPagina(numero) {
  const evento = paginas.get(numero);
  if (!evento) return;
  const linha = evento.linha;

  document.querySelectorAll("#corpo-tabela tr").forEach((tr) => {
    tr.classList.toggle("selecionada", Number(tr.dataset.pagina) === numero);
  });

  $("comparativo").hidden = false;
  $("cmp-cod-esperado").textContent = linha["Codigo (PDF Consulta)"];
  $("cmp-cod-lido").textContent = linha["Codigo (OCR Boletos)"];
  $("cmp-cod-selo").outerHTML = selo(linha["Status Codigo"]).replace('class="selo', 'id="cmp-cod-selo" class="selo');
  $("cmp-val-esperado").textContent = linha["Valor (PDF Consulta)"];
  $("cmp-val-lido").textContent = linha["Valor (OCR Boletos)"];
  $("cmp-val-selo").outerHTML = selo(linha["Status Valor"]).replace('class="selo', 'id="cmp-val-selo" class="selo');

  const etiqueta = $("etiqueta-estrategia");
  if (evento.estrategia) {
    etiqueta.textContent = `Estratégia vencedora: ${evento.estrategia}`;
    etiqueta.hidden = false;
  } else {
    etiqueta.hidden = true;
  }

  if (evento.imagem) {
    visor.innerHTML = `<img src="${evento.imagem}" alt="Guia da página ${numero} com os trechos lidos destacados">`;
  } else {
    visor.innerHTML = '<p class="vazio">Imagem indisponível para esta página.</p>';
  }

  const legenda = $("legenda-visor");
  const naoLocalizados = [];
  if (!evento.achou_codigo) naoLocalizados.push("código");
  if (!evento.achou_valor) naoLocalizados.push("valor");
  if (naoLocalizados.length) {
    legenda.textContent = `Não foi possível marcar a posição de: ${naoLocalizados.join(" e ")} — o texto foi lido, mas o OCR não devolveu coordenadas confiáveis.`;
    legenda.hidden = false;
  } else {
    legenda.hidden = true;
  }
}

function atualizarIndicadores() {
  $("ind-processadas").textContent = contadores.total
    ? `${contadores.processadas}/${contadores.total}`
    : contadores.processadas;
  $("ind-conformes").textContent = contadores.conformes;
  $("ind-divergentes").textContent = contadores.divergentes;
  const avaliadas = contadores.conformes + contadores.divergentes;
  $("ind-precisao").textContent = avaliadas
    ? `${((contadores.conformes / avaliadas) * 100).toFixed(1)}%`
    : "—";
}

$("filtro-divergencias").addEventListener("change", (evento) => {
  const somenteDivergencias = evento.target.checked;
  document.querySelectorAll("#corpo-tabela tr").forEach((tr) => {
    tr.hidden = somenteDivergencias && tr.dataset.divergente !== "1";
  });
});
