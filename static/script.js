const promptEl = document.getElementById("prompt");
const modelEl = document.getElementById("model");
const paramsEl = document.getElementById("params");
const generateBtn = document.getElementById("generateBtn");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const videoEl = document.getElementById("video");
const downloadLink = document.getElementById("downloadLink");
const healthEl = document.getElementById("health");
const imageInput = document.getElementById("imageInput");
const imagePreview = document.getElementById("imagePreview");

const POLL_INTERVAL = 3000;
const POLL_TIMEOUT = 10 * 60 * 1000;

// ---- Aperçu local des images sélectionnées ----
imageInput.addEventListener("change", () => {
  imagePreview.innerHTML = "";
  const files = Array.from(imageInput.files).slice(0, 5);
  files.forEach((file) => {
    const img = document.createElement("img");
    img.src = URL.createObjectURL(file);
    imagePreview.appendChild(img);
  });
});

// ---- Health check ----
(async () => {
  try {
    const r = await fetch("/api/health");
    const data = await r.json();
    if (data.replicate_configured) {
      healthEl.textContent = `✅ API prête — modèle par défaut : ${data.default_model}`;
      healthEl.className = "health ok";
    } else {
      healthEl.textContent = "⚠️ Clé API Agnes non configurée côté serveur.";
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

  generateBtn.disabled = true;
  resultEl.classList.add("hidden");

  // --- Upload des images si présentes ---
  let uploadedImageUrls = [];
  const files = imageInput.files;
  if (files && files.length > 0) {
    setStatus('<span class="spinner"></span>Envoi des images...', "info");
    try {
      const formData = new FormData();
      for (let i = 0; i < Math.min(files.length, 5); i++) {
        formData.append("files", files[i]);
      }
      const uploadRes = await fetch("/api/upload", {
        method: "POST",
        body: formData,
      });
      if (!uploadRes.ok) {
        const err = await uploadRes.json().catch(() => ({}));
        throw new Error(err.detail || "Erreur upload images");
      }
      const uploadData = await uploadRes.json();
      uploadedImageUrls = uploadData.urls;
    } catch (e) {
      setStatus(`❌ ${e.message}`, "ko");
      generateBtn.disabled = false;
      return;
    }
  }

  // --- Préparation du body ---
  const body = {
    prompt,
    model: modelEl.value.trim() || null,
    params: {
      ...params,
      image_urls: uploadedImageUrls,
    },
  };

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

// ---- Polling ----
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

  setStatus("⏱️ Délai dépassé.", "ko");
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