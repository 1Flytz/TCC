/**
 * PyConfer live audit interface.
 *
 * Upload PDFs, follow the SSE stream, and display each completed page.
 */

const $ = (id) => document.getElementById(id);

const auditForm = $("auditForm");
const startButton = $("start-button");
const notice = $("notice");
const panel = $("panel");
const tableBody = $("body-table");
const viewer = $("viewer");
const progressBar = $("progressBar");
const statusText = $("statusText");

// Keep annotated page images available when revisiting report rows.
const pages = new Map();
let counters = { processed: 0, matched: 0, mismatched: 0, total: null };
let eventSource = null;

let timerInterval = null;
let elapsedSeconds = 0;

auditForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  notice.textContent = "";

  const data = new FormData(auditForm);
  const dpi = data.get("dpi") || 500;
  data.delete("dpi");

  startButton.disabled = true;
  startButton.textContent = "Uploading documents…";

  try {
    const response = await fetch(`/api/v1/audits?dpi=${dpi}`, { method: "POST", body: data });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `Failed to start (HTTP ${response.status}).`);
    }
    const { job_id } = await response.json();
    resetPanel();
    followAudit(job_id);
  } catch (error) {
    notice.textContent = error.message;
    startButton.disabled = false;
    startButton.textContent = "Start audit";
  }
});

function resetPanel() {
  clearInterval(timerInterval);
  elapsedSeconds = 0;
  if ($("ind-time")) $("ind-time").textContent = "00:00";
  pages.clear();
  tableBody.innerHTML = "";
  counters = { processed: 0, matched: 0, mismatched: 0, total: null };
  updateIndicators();
  progressBar.style.width = "0%";
  viewer.innerHTML = '<p class="empty">Waiting for the first page…</p>';
  $("comparison").hidden = true;
  $("tag-strategy").hidden = true;
  $("caption-viewer").hidden = true;
  $("link-csv").hidden = true;

  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function followAudit(jobId) {
  statusText.textContent = "Rendering the PDF and reading the reference list…";
  startButton.textContent = "Audit in progress…";

  timerInterval = setInterval(() => {
    elapsedSeconds++;
    const m = String(Math.floor(elapsedSeconds / 60)).padStart(2, '0');
    const s = String(elapsedSeconds % 60).padStart(2, '0');
    if ($("ind-time")) $("ind-time").textContent = `${m}:${s}`;
  }, 1000);

  eventSource = new EventSource(`/api/v1/audits/${jobId}/events`);

  eventSource.onmessage = (message) => {
    const event = JSON.parse(message.data);

    if (event.type === "start") {
      counters.total = event.total_pages;
      statusText.textContent = `${event.total_pages} slips found · ${event.reference_count} reference entries · ${event.dpi} DPI`;
      if ($("active-resolution")) $("active-resolution").textContent = `${event.dpi} DPI`;
      updateIndicators();
    } else if (event.type === "page") {
      registerPage(event);
    } else if (event.type === "error") {
      statusText.textContent = "";
      notice.textContent = event.message;
      finishAudit(jobId, false);
    } else if (event.type === "end") {
      statusText.textContent = `Audit completed — ${counters.processed} slips checked.`;
      finishAudit(jobId, true);
    }
  };

  eventSource.onerror = () => {
    // Close the session explicitly when the event stream has closed.
    if (eventSource && eventSource.readyState === EventSource.CLOSED) {
      finishAudit(jobId, counters.processed > 0);
    }
  };
}

function finishAudit(jobId, hasResults) {
  clearInterval(timerInterval);
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  startButton.disabled = false;
  startButton.textContent = "Start new audit";
  if (hasResults) {
    showCsvLink(jobId);
  }
  loadHistory();
}

function showCsvLink(jobId) {
  const link = $("link-csv");
  link.href = `/api/v1/audits/${jobId}/report.csv`;
  link.hidden = false;
}

/* =========================================================
   History — stored audit results
   ========================================================= */

const STATUS_LABELS = {
  completed: ["Completed", "ok"],
  processing: ["In progress", ""],
  abandoned: ["Interrupted", ""],
  error: ["Failed", "error"],
};

function formatTimestamp(iso) {
  const data = new Date(iso);
  if (isNaN(data)) return iso;
  const twoDigits = (n) => String(n).padStart(2, "0");
  return `${twoDigits(data.getDate())}/${twoDigits(data.getMonth() + 1)}/${data.getFullYear()}` +
    ` ${twoDigits(data.getHours())}:${twoDigits(data.getMinutes())}`;
}

async function loadHistory() {
  let audits;
  try {
    const response = await fetch("/api/v1/audits");
    if (!response.ok) return;
    audits = await response.json();
  } catch {
    return; // history failure must not block live verification
  }

  const body = $("body-history");
  body.innerHTML = "";
  $("history-card").hidden = audits.length === 0;

  for (const audit of audits) {
    const evaluated = audit.matched + audit.mismatched;
    const rate = evaluated ? `${((audit.matched / evaluated) * 100).toFixed(1)}%` : "—";
    const [label, className] = STATUS_LABELS[audit.status] || [audit.status, ""];

    const tr = document.createElement("tr");
    tr.className = "row-history";
    tr.innerHTML = `
      <td>${formatTimestamp(audit.created_at)}</td>
      <td><span class="badge ${className}">${label}</span></td>
      <td>${audit.processed_pages}${audit.total_pages ? ` / ${audit.total_pages}` : ""}</td>
      <td>${audit.matched}</td>
      <td class="${audit.mismatched ? "discrepancy" : ""}">${audit.mismatched}</td>
      <td>${rate}</td>
    `;
    tr.addEventListener("click", () => openAudit(audit));
    body.appendChild(tr);
  }
}

async function openAudit(audit) {
  if (eventSource) return; // preserve the active audit session

  let rows;
  try {
    const response = await fetch(`/api/v1/audits/${audit.job_id}/pages`);
    if (!response.ok) throw new Error("Could not load this audit.");
    rows = await response.json();
  } catch (error) {
    notice.textContent = error.message;
    return;
  }

  resetPanel();
  counters.total = audit.total_pages;

  for (const row of rows) {
    // Historical rows have no images; previews are generated only during a live audit.
    const event = { page: row["Page"], row };
    const mismatch = row["Overall Status"] === "ERROR";

    counters.processed += 1;
    if (row["Overall Status"] === "OK") counters.matched += 1;
    if (mismatch) counters.mismatched += 1;

    pages.set(event.page, event);
    addRow(event, mismatch);
  }

  updateIndicators();
  progressBar.style.width = "100%";
  statusText.textContent = `Audit from ${formatTimestamp(audit.created_at)} — ` +
    `${counters.processed} slips, reopened from history.`;

  if (rows.length) {
    showCsvLink(audit.job_id);
    showPage(rows[0]["Page"]);
  }

  // Replace the generic preview message after displaying the first historical row.
  viewer.innerHTML = '<p class="empty">Annotated OCR images are not stored:<br>' +
    'they are available only during live verification.</p>';
  $("caption-viewer").hidden = true;
}

$("refresh-history-button").addEventListener("click", loadHistory);
loadHistory();

function registerPage(event) {
  const row = event.row;
  const mismatch = row["Overall Status"] === "ERROR";

  counters.processed += 1;
  if (row["Overall Status"] === "OK") counters.matched += 1;
  if (mismatch) counters.mismatched += 1;
  updateIndicators();

  if (counters.total) {
    progressBar.style.width = `${(counters.processed / counters.total) * 100}%`;
    statusText.textContent = `Checking… page ${event.page} of ${counters.total}`;
  }

  pages.set(event.page, event);
  addRow(event, mismatch);
  showPage(event.page);
}

function addRow(event, mismatch) {
  const row = event.row;
  const tr = document.createElement("tr");
  tr.dataset.page = event.page;
  tr.dataset.mismatch = mismatch ? "1" : "0";
  if (mismatch) tr.classList.add("mismatch");
  if ($("mismatch-filter").checked && !mismatch) tr.hidden = true;

  const codeMismatch = row["Code Status"] !== "OK";
  const amountMismatch = row["Amount Status"] !== "OK";

  tr.innerHTML = `
    <td>${event.page}</td>
    <td>${row["Code (Reference PDF)"]}</td>
    <td class="${codeMismatch ? "discrepancy" : ""}">${row["Code (OCR Slips)"]}</td>
    <td>${row["Amount (Reference PDF)"]}</td>
    <td class="${amountMismatch ? "discrepancy" : ""}">${row["Amount (OCR Slips)"]}</td>
    <td>${badge(row["Overall Status"])}${categoryBadge(row["Category"])}</td>
  `;

  tr.addEventListener("click", () => showPage(event.page));
  tableBody.appendChild(tr);
}

function badge(status) {
  const className = status === "OK" ? "ok" : status === "ERROR" ? "error" : "";
  const text = status === "ERROR" ? "Mismatch" : status === "OK" ? "Match" : status;
  return `<span class="badge ${className}">${text}</span>`;
}

// Discrepancy categories and suggested operator actions.
const CATEGORIES = {
  "OTHER REGISTRATION": ["Other registration", "severe", "The code belongs to another slip in this batch — check for a swapped slip."],
  "UNREAD": ["Unread", "", "OCR did not extract this field. Inspect scan quality or try a higher DPI."],
  "REVIEW": ["Review", "", "The reading matches neither the expected value nor another registration in this batch."],
};

function categoryBadge(category) {
  const info = CATEGORIES[category];
  if (!info) return "";
  const [text, className, explanation] = info;
  return `<span class="category ${className}" title="${explanation}">${text}</span>`;
}

function showPage(number) {
  const event = pages.get(number);
  if (!event) return;
  const row = event.row;

  document.querySelectorAll("#body-table tr").forEach((tr) => {
    tr.classList.toggle("selected", Number(tr.dataset.page) === number);
  });

  $("comparison").hidden = false;
  $("cmp-code-expected").textContent = row["Code (Reference PDF)"];
  $("cmp-code-read").textContent = row["Code (OCR Slips)"];
  $("cmp-code-badge").outerHTML = badge(row["Code Status"]).replace('class="badge', 'id="cmp-code-badge" class="badge');
  $("cmp-amount-expected").textContent = row["Amount (Reference PDF)"];
  $("cmp-amount-read").textContent = row["Amount (OCR Slips)"];
  $("cmp-amount-badge").outerHTML = badge(row["Amount Status"]).replace('class="badge', 'id="cmp-amount-badge" class="badge');

  const explanation = $("explanation-category");
  const categoryInfo = CATEGORIES[row["Category"]];
  if (categoryInfo) {
    const [text, className, detail] = categoryInfo;
    explanation.className = `explanation-category ${className}`;
    explanation.innerHTML = `<strong>${text}:</strong> ${detail}`;
    explanation.hidden = false;
  } else {
    explanation.hidden = true;
  }

  const tag = $("tag-strategy");
  if (event.strategy) {
    tag.textContent = `Winning strategy: ${event.strategy}`;
    tag.hidden = false;
  } else {
    tag.hidden = true;
  }

  if (event.image) {
    viewer.innerHTML = `<img src="${event.image}" alt="Slip on page ${number} with extracted fields highlighted">`;
  } else {
    viewer.innerHTML = '<p class="empty">Image unavailable for this page.</p>';
  }

  const caption = $("caption-viewer");
  const notLocated = [];
  if (!event.code_found) notLocated.push("code");
  if (!event.amount_found) notLocated.push("amount");
  if (notLocated.length) {
    caption.textContent = `Could not locate: ${notLocated.join(" and ")} — the text was read, but OCR did not return reliable coordinates.`;
    caption.hidden = false;
  } else {
    caption.hidden = true;
  }
}

function updateIndicators() {
  $("ind-processed").textContent = counters.total
    ? `${counters.processed}/${counters.total}`
    : counters.processed;
  $("ind-matched").textContent = counters.matched;
  $("ind-mismatched").textContent = counters.mismatched;
  const evaluated = counters.matched + counters.mismatched;
  $("ind-match-rate").textContent = evaluated
    ? `${((counters.matched / evaluated) * 100).toFixed(1)}%`
    : "—";
}

$("mismatch-filter").addEventListener("change", (event) => {
  const onlyMismatches = event.target.checked;
  document.querySelectorAll("#body-table tr").forEach((tr) => {
    tr.hidden = onlyMismatches && tr.dataset.mismatch !== "1";
  });
});
