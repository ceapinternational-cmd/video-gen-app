const promptEl = document.getElementById("prompt");
const modelEl = document.getElementById("model");
const paramsEl = document.getElementById("params");
const generateBtn = document.getElementById("generateBtn");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const videoEl = document.getElementById("video");
const downloadLink = document.getElementById("downloadLink");
const healthEl = document.getElementById("health");

const POLL_INTERVAL = 3000; // 3s
const POLL_TIMEOUT = 10 * 60 * 1000; // 10 min

// ---- Health check au chargement ----
(async () => {
  try {
    const r = await fetch("/api/health");
    const data = await r.json();
    if (data.replicate_configured) {
      healthEl.textContent = `✅ API prête — modèle par défaut : ${data.default_model}`;
      healthEl.className = "health ok";
    } else {
      healthEl.textContent = "⚠️ REPLICATE_API_TOKEN non configuré côté serveur.";
      healthEl.className = "health ko";
    }
  } catch {
    healthEl.textContent = "❌ Backend injoignable.";
    healthEl.className = "health ko";
  }
})();

// ---- Génération ----
generateBtn.addEventListener("click", async () => {
  const prompt = promptEl.value.trim();
  if (!prompt) {
    setStatus("✏️ Écris un prompt avant de générer.", "ko");
    return;
  }

  let params = {};
  const rawParams = paramsEl.value.trim();
  if (rawParams) {
    try {
      params = JSON.parse(rawParams);
    } catch {
      setStatus("❌ JSON invalide dans les paramètres avancés.", "ko");
      return;
    }
  }

  const body = {
    prompt,
    model: modelEl.value.trim() || null,
    params,
  };

  generateBtn.disabled = true;
  resultEl.classList.add("hidden");
  setStatus('<span class="spinner"></span>Envoi de la requête...', "info");

  try {
    const r = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `Erreur HTTP ${r.status}`);
    }

    const { job_id } = await r.json();
    await pollJob(job_id);
  } catch (e) {
    setStatus(`❌ ${e.message}`, "ko");
    generateBtn.disabled = false;
  }
});

// ---- Polling du job ----
async function pollJob(jobId) {
  const start = Date.now();

  while (Date.now() - start < POLL_TIMEOUT) {
    try {
      const r = await fetch(`/api/jobs/${jobId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const job = await r.json();

      if (job.status === "pending") {
        setStatus('<span class="spinner"></span>En file d\'attente...', "info");
      } else if (job.status === "running") {
        const secs = Math.round((Date.now() - start) / 1000);
        setStatus(`<span class="spinner"></span>Génération en cours... (${secs}s)`, "info");
      } else if (job.status === "succeeded") {
        setStatus("✅ Vidéo prête !", "ok");
        showVideo(job.video_url);
        generateBtn.disabled = false;
        return;
      } else if (job.status === "failed") {
        setStatus(`❌ Échec : ${job.error}`, "ko");
        generateBtn.disabled = false;
        return;
      }
    } catch (e) {
      setStatus(`⚠️ Erreur de polling : ${e.message}`, "ko");
    }

    await new Promise((res) => setTimeout(res, POLL_INTERVAL));
  }

  setStatus("⏱️ Délai dépassé. Le job tourne peut-être encore côté serveur.", "ko");
  generateBtn.disabled = false;
}

// ---- Affichage ----
function showVideo(url) {
  videoEl.src = url;
  downloadLink.href = url;
  resultEl.classList.remove("hidden");
  videoEl.scrollIntoView({ behavior: "smooth", block: "start" });
}

function setStatus(html, type) {
  statusEl.innerHTML = html;
  statusEl.style.color =
    type === "ok" ? "#4ade80" : type === "ko" ? "#f87171" : "#9aa0a6";
}
