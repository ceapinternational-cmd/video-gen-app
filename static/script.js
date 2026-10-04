const providerEl = document.getElementById("provider");
const replicateModelEl = document.getElementById("replicateModel");
const replicateModelBox = document.getElementById("replicateModelBox");
const promptEl = document.getElementById("prompt");
const generateBtn = document.getElementById("generateBtn");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const videoEl = document.getElementById("video");
const downloadLink = document.getElementById("downloadLink");
const healthEl = document.getElementById("health");
const imageInput = document.getElementById("imageInput");
const imagePreview = document.getElementById("imagePreview");

const POLL_INTERVAL = 5000;
const POLL_TIMEOUT = 40 * 60 * 1000;

// Afficher/cacher le menu du modèle Replicate
providerEl.addEventListener("change", () => {
  if (providerEl.value === "replicate") {
    replicateModelBox.style.display = "block";
  } else {
    replicateModelBox.style.display = "none";
  }
});

// Aperçu image
imageInput.addEventListener("change", () => {
  imagePreview.innerHTML = "";
  const file = imageInput.files[0];
  if (file) {
    const img = document.createElement("img");
    img.src = URL.createObjectURL(file);
    imagePreview.appendChild(img);
  }
});

// Health check
(async () => {
  try {
    const r = await fetch("/api/health");
    const data = await r.json();
    const parts = [];
    if (data.replicate_configured) parts.push("Replicate ✅");
    if (data.agnes_configured) parts.push("Agnes ✅");
    if (parts.length > 0) {
      healthEl.textContent = "API prêtes — " + parts.join(" · ");
      healthEl.className = "health ok";
    } else {
      healthEl.textContent = "⚠️ Aucun fournisseur configuré.";
      healthEl.className = "health ko";
    }
  } catch {
    healthEl.textContent = "❌ Backend injoignable.";
    healthEl.className = "health ko";
  }
})();

// Générer
generateBtn.addEventListener("click", async () => {
  const prompt = promptEl.value.trim();
  const provider = providerEl.value;
  const model = provider === "replicate" ? replicateModelEl.value : null;

  if (!prompt) {
    setStatus("✏️ Écris un prompt avant de générer.", "ko");
    return;
  }

  generateBtn.disabled = true;
  resultEl.classList.add("hidden");

  // Upload image si présente
  let uploadedImageUrls = [];
  const file = imageInput.files[0];
  if (file) {
    setStatus('<span class="spinner"></span>Envoi de l\'image...', "info");
    try {
      const formData = new FormData();
      formData.append("files", file);
      const uploadRes = await fetch("/api/upload", {
        method: "POST",
        body: formData,
      });
      if (!uploadRes.ok) throw new Error("Erreur upload image");
      const uploadData = await uploadRes.json();
      uploadedImageUrls = uploadData.urls;
    } catch (e) {
      setStatus(`❌ ${e.message}`, "ko");
      generateBtn.disabled = false;
      return;
    }
  }

  const body = {
    prompt,
    provider,
    model,
    params: {
      image_urls: uploadedImageUrls,
    },
  };

  const providerName = provider === "replicate" ? "Replicate" : "Agnes";
  setStatus(`<span class="spinner"></span>Envoi à ${providerName}...`, "info");

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

    const data = await r.json();
    await pollJob(data.job_id, provider);
  } catch (e) {
    setStatus(`❌ ${e.message}`, "ko");
    generateBtn.disabled = false;
  }
});

// Polling
async function pollJob(jobId, provider) {
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
        if (job.error) {
          setStatus(`<span class="spinner"></span>${job.error}`, "info");
        } else {
          setStatus(`<span class="spinner"></span>Génération en cours... (${secs}s)`, "info");
        }
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