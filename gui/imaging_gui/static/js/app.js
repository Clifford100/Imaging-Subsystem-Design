const imageSelect = document.getElementById("imageSelect");
const backgroundSelect = document.getElementById("backgroundSelect");
const imageUpload = document.getElementById("imageUpload");
const backgroundUpload = document.getElementById("backgroundUpload");
const uploadBtn = document.getElementById("uploadBtn");
const uploadBackgroundBtn = document.getElementById("uploadBackgroundBtn");
const startBtn = document.getElementById("startBtn");
const statusBox = document.getElementById("status");
const preview = document.getElementById("selectedPreview");
const emptyPreview = document.getElementById("emptyPreview");
const backgroundPreview = document.getElementById("backgroundPreview");
const emptyBackgroundPreview = document.getElementById("emptyBackgroundPreview");
const dataString = document.getElementById("dataString");
const summaryCards = document.getElementById("summaryCards");
const metadataTable = document.getElementById("metadataTable");
const stagesGrid = document.getElementById("stagesGrid");
const particleTableBody = document.querySelector("#particleTable tbody");
const csvLink = document.getElementById("csvLink");

function setStatus(message, type = "") {
    statusBox.textContent = message;
    statusBox.className = `status ${type}`;
}

async function fetchJSON(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || "Request failed.");
    }
    return data;
}

async function loadImages(selectedName = null) {
    const data = await fetchJSON("/api/images");
    imageSelect.innerHTML = "";

    if (!data.images.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No images found in captured_images/";
        imageSelect.appendChild(option);
        updatePreview();
        return;
    }

    data.images.forEach((img) => {
        const option = document.createElement("option");
        option.value = img.filename;
        option.textContent = `${img.filename} (${img.size_kb} kB)`;
        imageSelect.appendChild(option);
    });

    if (selectedName) imageSelect.value = selectedName;
    updatePreview();
}

async function loadBackgrounds(selectedName = null) {
    const data = await fetchJSON("/api/backgrounds");
    backgroundSelect.innerHTML = "";

    const autoOption = document.createElement("option");
    autoOption.value = "AUTO";
    autoOption.textContent = "Auto select background image";
    backgroundSelect.appendChild(autoOption);

    data.backgrounds.forEach((bg) => {
        const option = document.createElement("option");
        option.value = bg.filename;
        option.textContent = `${bg.filename} (${bg.size_kb} kB)`;
        backgroundSelect.appendChild(option);
    });

    if (selectedName) backgroundSelect.value = selectedName;
    updateBackgroundPreview();
}

function updatePreview() {
    const filename = imageSelect.value;
    if (!filename) {
        preview.style.display = "none";
        emptyPreview.style.display = "block";
        return;
    }
    preview.src = `/captured_images/${encodeURIComponent(filename)}?t=${Date.now()}`;
    preview.style.display = "block";
    emptyPreview.style.display = "none";
}

function updateBackgroundPreview() {
    const filename = backgroundSelect.value;
    if (!filename || filename === "AUTO") {
        backgroundPreview.style.display = "none";
        emptyBackgroundPreview.style.display = "block";
        emptyBackgroundPreview.textContent = "Auto background selection enabled.";
        return;
    }
    backgroundPreview.src = `/background_images/${encodeURIComponent(filename)}?t=${Date.now()}`;
    backgroundPreview.style.display = "block";
    emptyBackgroundPreview.style.display = "none";
}

function renderSummary(summary) {
    const cards = [
        ["Total", summary.counts.total],
        ["Circular", summary.counts.circular],
        ["Rod-like", summary.counts.rod_like],
        ["Irregular", summary.counts.irregular],
        ["Area mm²", summary.total_area_mm2],
        ["Background", summary.background_source],
    ];

    summaryCards.innerHTML = cards
        .map(([label, value]) => `<div class="card"><div class="value">${value}</div><div class="label">${label}</div></div>`)
        .join("");

    csvLink.href = summary.csv_url;
    csvLink.classList.remove("hidden");
}

function formatKey(key) {
    return key.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function renderMetadata(metadata) {
    metadataTable.innerHTML = Object.entries(metadata)
        .map(([key, value]) => `
            <div class="kv-item">
                <div class="kv-key">${formatKey(key)}</div>
                <div class="kv-value">${value}</div>
            </div>
        `)
        .join("");
}

function renderStages(stages) {
    stagesGrid.innerHTML = stages
        .map((stage) => `
            <article class="stage-card">
                <img src="${stage.image}" alt="${stage.name}" />
                <h3>${stage.name}</h3>
            </article>
        `)
        .join("");
}

function renderParticles(particles) {
    if (!particles.length) {
        particleTableBody.innerHTML = `<tr><td colspan="8">No particles detected.</td></tr>`;
        return;
    }

    particleTableBody.innerHTML = particles
        .map((p) => `
            <tr>
                <td>${p.id}</td>
                <td>${p.class}</td>
                <td>${p.area_mm2}</td>
                <td>${p.equivalent_diameter_mm}</td>
                <td>${p.aspect_ratio}</td>
                <td>${p.circularity}</td>
                <td>${p.solidity}</td>
                <td>(${p.centroid_x_px}, ${p.centroid_y_px})</td>
            </tr>
        `)
        .join("");
}

uploadBtn.addEventListener("click", async () => {
    const file = imageUpload.files[0];
    if (!file) {
        setStatus("Select an image file before uploading.", "error");
        return;
    }

    const formData = new FormData();
    formData.append("image", file);

    try {
        setStatus("Uploading captured image...");
        const data = await fetchJSON("/api/upload", { method: "POST", body: formData });
        await loadImages(data.filename);
        setStatus("Captured image uploaded successfully.", "ok");
    } catch (err) {
        setStatus(err.message, "error");
    }
});

uploadBackgroundBtn.addEventListener("click", async () => {
    const file = backgroundUpload.files[0];
    if (!file) {
        setStatus("Select a background image before uploading.", "error");
        return;
    }

    const formData = new FormData();
    formData.append("background", file);

    try {
        setStatus("Uploading background image...");
        const data = await fetchJSON("/api/upload-background", { method: "POST", body: formData });
        await loadBackgrounds(data.filename);
        setStatus("Background image uploaded successfully.", "ok");
    } catch (err) {
        setStatus(err.message, "error");
    }
});

startBtn.addEventListener("click", async () => {
    const filename = imageSelect.value;
    if (!filename) {
        setStatus("Select a captured image first.", "error");
        return;
    }

    try {
        setStatus("Processing image using selected/auto background reference...");
        startBtn.disabled = true;

        const result = await fetchJSON("/api/process", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                filename,
                background_filename: backgroundSelect.value || "AUTO",
                data_string: dataString.value.trim(),
            }),
        });

        renderSummary(result.summary);
        renderMetadata(result.metadata);
        renderStages(result.stages);
        renderParticles(result.particles);
        setStatus("Processing complete.", "ok");
    } catch (err) {
        setStatus(err.message, "error");
    } finally {
        startBtn.disabled = false;
    }
});

imageSelect.addEventListener("change", updatePreview);
backgroundSelect.addEventListener("change", updateBackgroundPreview);

Promise.all([loadImages(), loadBackgrounds()]).catch((err) => setStatus(err.message, "error"));
