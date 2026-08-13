const $ = (sel) => document.querySelector(sel);

const els = {
  form: $("#urlForm"),
  input: $("#urlInput"),
  analyzeBtn: $("#analyzeBtn"),
  errorBanner: $("#errorBanner"),
  previewCard: $("#previewCard"),
  thumb: $("#thumb"),
  durationBadge: $("#durationBadge"),
  videoTitle: $("#videoTitle"),
  channel: $("#channel"),
  qualityChips: $("#qualityChips"),
  downloadBtn: $("#downloadBtn"),
  progressCard: $("#progressCard"),
  progressTitle: $("#progressTitle"),
  statusBadge: $("#statusBadge"),
  progressPct: $("#progressPct"),
  barFill: $("#barFill"),
  speed: $("#speed"),
  eta: $("#eta"),
  doneActions: $("#doneActions"),
  saveBtn: $("#saveBtn"),
  downloadList: $("#downloadList"),
  toast: $("#toast"),
};

let videoInfo = null;
let selectedQuality = "best";
let toastTimer = null;
let streamDone = false;

function showToast(msg, isError = false) {
  els.toast.textContent = msg;
  els.toast.style.borderColor = isError ? "rgba(255,71,87,.5)" : "";
  els.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => els.toast.classList.remove("show"), 3500);
}

function showError(msg) {
  els.errorBanner.textContent = msg;
  els.errorBanner.classList.add("show");
}

function hideError() {
  els.errorBanner.classList.remove("show");
  els.errorBanner.textContent = "";
}

function formatDuration(sec) {
  if (sec === undefined || sec === null) return "";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function formatBytes(bytes) {
  if (bytes === undefined || bytes === null) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

/* ---------- Análisis del video ---------- */
els.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = els.input.value.trim();
  if (!url) return;
  hideError();
  els.analyzeBtn.classList.add("loading");
  els.analyzeBtn.disabled = true;
  try {
    const res = await fetch("/api/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "No se pudo obtener la información del video.");
    videoInfo = data;
    renderPreview();
    els.previewCard.hidden = false;
    els.previewCard.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (err) {
    showError(err.message);
  } finally {
    els.analyzeBtn.classList.remove("loading");
    els.analyzeBtn.disabled = false;
  }
});

function renderPreview() {
  els.thumb.src = videoInfo.thumbnail || "";
  els.videoTitle.textContent = videoInfo.title || "Video sin título";
  els.channel.textContent = videoInfo.channel ? `Por ${videoInfo.channel}` : "";
  els.durationBadge.textContent = formatDuration(videoInfo.duration);

  const heights = videoInfo.heights || [];
  const options = [{ value: "best", label: "Máxima calidad" }];
  for (const h of heights) options.push({ value: String(h), label: `${h}p` });
  if (videoInfo.has_audio_only) options.push({ value: "audio", label: "Solo audio (MP3)" });

  els.qualityChips.innerHTML = "";
  selectedQuality = "best";
  options.forEach((opt) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip" + (opt.value === "best" ? " active" : "");
    chip.textContent = opt.label;
    chip.dataset.value = opt.value;
    chip.addEventListener("click", () => {
      els.qualityChips.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      selectedQuality = opt.value;
    });
    els.qualityChips.appendChild(chip);
  });
}

/* ---------- Descarga ---------- */
els.downloadBtn.addEventListener("click", async () => {
  if (!videoInfo) return;
  const label = els.downloadBtn.querySelector("span");
  els.downloadBtn.disabled = true;
  label.textContent = "Preparando...";
  resetProgress();
  els.progressCard.hidden = false;
  els.progressCard.scrollIntoView({ behavior: "smooth", block: "center" });
  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: videoInfo.url, quality: selectedQuality }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Error al iniciar la descarga.");
    listenProgress(data.id);
  } catch (err) {
    setStatus("error", "Error");
    showToast(err.message, true);
    label.textContent = "Descargar";
    els.downloadBtn.disabled = false;
  }
});

function resetProgress() {
  els.barFill.style.width = "0%";
  els.progressPct.textContent = "0%";
  els.speed.textContent = "";
  els.eta.textContent = "";
  els.doneActions.hidden = true;
  els.saveBtn.hidden = true;
  setStatus("active", "Descargando");
}

function setStatus(kind, label) {
  els.statusBadge.textContent = label;
  els.statusBadge.className = "status-badge " + kind;
}

function updateProgressUI(s) {
  els.progressTitle.textContent = s.title || "Descargando...";
  els.progressPct.textContent = `${Math.round(s.progress)}%`;
  els.barFill.style.width = `${Math.min(s.progress, 100)}%`;
  els.speed.textContent = s.speed ? `Velocidad: ${s.speed}` : "";
  els.eta.textContent = s.eta ? `Tiempo restante: ${s.eta}` : "";
  if (s.status === "processing") setStatus("processing", "Procesando");
}

function listenProgress(id) {
  streamDone = false;
  const es = new EventSource(`/api/progress/${id}`);
  es.onmessage = (ev) => {
    let s;
    try {
      s = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (s.status === "error") {
      streamDone = true;
      es.close();
      finalizeError(s);
    } else if (s.status === "done") {
      streamDone = true;
      es.close();
      finalizeDone(s);
    } else {
      updateProgressUI(s);
    }
  };
  es.onerror = () => {
    es.close();
    if (!streamDone) showToast("Se perdió la conexión con el servidor.", true);
  };
}

function finalizeDone(s) {
  setStatus("done", "Completado");
  els.progressPct.textContent = "100%";
  els.barFill.style.width = "100%";
  els.speed.textContent = "";
  els.eta.textContent = "";
  if (s.filename) {
    els.saveBtn.href = `/api/file/${encodeURIComponent(s.filename)}`;
    els.saveBtn.hidden = false;
  }
  els.doneActions.hidden = false;
  showToast("¡Descarga completada!");
  resetDownloadButton();
  loadDownloads();
}

function finalizeError(s) {
  setStatus("error", "Error");
  showToast(s.error || "Error en la descarga.", true);
  resetDownloadButton();
}

function resetDownloadButton() {
  const label = els.downloadBtn.querySelector("span");
  label.textContent = "Descargar";
  els.downloadBtn.disabled = false;
}

/* ---------- Descargas recientes ---------- */
async function loadDownloads() {
  try {
    const res = await fetch("/api/downloads");
    if (!res.ok) return;
    renderList(await res.json());
  } catch {
    /* ignora errores de red aquí */
  }
}

function renderList(items) {
  if (!items.length) {
    els.downloadList.innerHTML = '<p class="empty">Aún no hay descargas. ¡Empieza ahora!</p>';
    return;
  }
  els.downloadList.innerHTML = "";
  items.forEach((it, i) => {
    const item = document.createElement("div");
    item.className = "list-item";
    item.style.animationDelay = `${Math.min(i * 0.06, 0.5)}s`;
    item.innerHTML = `
      <div class="file-icon">
        <svg viewBox="0 0 24 24">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
        </svg>
      </div>
      <span class="fname" title="${it.name.replace(/"/g, "&quot;")}">${it.name}</span>
      <span class="fsize">${formatBytes(it.size)}</span>
      <a href="/api/file/${encodeURIComponent(it.name)}" download>Descargar</a>
    `;
    els.downloadList.appendChild(item);
  });
}

loadDownloads();
