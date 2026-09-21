let conversationId = null;
let modelId = null;
let assistantElement = null;

const $ = (id) => document.getElementById(id);

async function get(path) {
  const response = await fetch(`/api/v1${path}`);
  return response.json();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
  }[character]));
}

function renderMessages(messages) {
  $("messages").innerHTML = messages.map((message) => `
    <div class="message ${escapeHtml(message.role)}" data-message-id="${message.id}">${escapeHtml(message.content)}</div>
  `).join("");
  const streaming = messages.find((message) => message.role === "assistant" && message.status === "streaming");
  assistantElement = streaming ? document.querySelector(`[data-message-id="${streaming.id}"]`) : null;
}

async function refresh() {
  const [models, conversations] = await Promise.all([get("/models"), get("/conversations")]);
  if (modelId === null) {
    modelId = models.find((model) => model.installed && model.enabled)?.id ?? null;
  }
  $("models").innerHTML = models.map((model) => `
    <div class="model"><button data-model="${model.id}" ${model.installed && model.enabled ? "" : "disabled"}>
      <strong>${escapeHtml(model.label)}</strong><br>
      <span class="level">${model.installed ? `${escapeHtml(model.fit.level)} · estimé${model.loaded ? " · chargé" : ""}` : "non installé"}</span>
    </button></div>
  `).join("");
  $("conversations").innerHTML = conversations.map((conversation) => `
    <div class="conversation"><button data-conversation="${conversation.id}">${escapeHtml(conversation.title)}</button></div>
  `).join("");
  document.querySelectorAll("[data-model]").forEach((button) => {
    button.onclick = () => { modelId = Number(button.dataset.model); };
  });
  document.querySelectorAll("[data-conversation]").forEach((button) => {
    button.onclick = () => openConversation(Number(button.dataset.conversation));
  });
}

async function openConversation(id) {
  conversationId = id;
  renderMessages(await get(`/conversations/${id}/messages`));
}

function appendToken(event) {
  if (!assistantElement) return;
  assistantElement.textContent += JSON.parse(event.data);
}

function parseEventData(event) {
  return event.data ? JSON.parse(event.data) : {};
}

async function refreshImageWorkflows() {
  const workflows = await get("/image/workflows");
  $("image-workflow").innerHTML = workflows.map((workflow) => `
    <option value="${escapeHtml(workflow.id)}">${escapeHtml(workflow.label)}</option>
  `).join("");
}

function addImageOutput(output) {
  const image = document.createElement("img");
  image.src = output.url;
  image.alt = output.name;
  image.loading = "lazy";
  $("image-results").appendChild(image);
}

async function generateImage() {
  const seed = $("image-seed").value;
  const response = await fetch("/api/v1/image/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workflow_id: $("image-workflow").value,
      prompt: $("image-prompt").value,
      seed: seed ? Number(seed) : null,
      steps: Number($("image-steps").value),
      cfg: Number($("image-cfg").value),
      width: 1024,
      height: 1024,
    }),
  });
  const result = await response.json();
  if (!response.ok) {
    $("image-status").textContent = result.error?.message || "La génération image a échoué";
    return;
  }
  $("image-results").innerHTML = "";
  $("image-status").textContent = "Génération en cours...";
  const source = new EventSource(`/api/v1/jobs/${result.job_id}/stream`);
  source.addEventListener("progress", (event) => {
    const data = parseEventData(event);
    $("image-status").textContent = data.status || "Génération en cours...";
  });
  source.addEventListener("output", (event) => addImageOutput(parseEventData(event)));
  source.addEventListener("completed", () => {
    source.close();
    $("image-status").textContent = "Génération terminée";
  });
  source.addEventListener("cancelled", () => {
    source.close();
    $("image-status").textContent = "Génération annulée";
  });
  source.addEventListener("error", (event) => {
    source.close();
    const data = parseEventData(event);
    $("image-status").textContent = data.message || "Erreur pendant la génération";
  });
}

$("new-conversation").onclick = async () => {
  if (modelId === null) {
    alert("Aucun modèle installé n'est disponible");
    return;
  }
  const response = await fetch("/api/v1/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: modelId }),
  });
  const conversation = await response.json();
  conversationId = conversation.id;
  await refresh();
};

async function submitMessage(options = {}) {
  if (!conversationId) return;
  const response = await fetch(`/api/v1/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: $("content").value, ...options }),
  });
  const result = await response.json();
  if (!response.ok) {
    const error = result.error || {};
    const details = error.details || {};
    if (error.code === "confirmation_required" && details.fit) {
      if (confirm(`${error.message}. Continuer malgré la recommandation mémoire ?`)) {
        return submitMessage({ ...options, force: true });
      }
    } else if (error.code === "confirmation_required" && details.remote) {
      if (confirm(`${error.message}. Continuer ?`)) {
        return submitMessage({ ...options, allow_remote: true });
      }
    } else {
      alert(error.message || "La requête a échoué");
    }
    return;
  }
  $("content").value = "";
  await openConversation(conversationId);
  const source = new EventSource(`/api/v1/jobs/${result.job_id}/stream`);
  source.addEventListener("token", appendToken);
  source.addEventListener("completed", async () => {
    source.close();
    await openConversation(conversationId);
  });
  source.addEventListener("cancelled", async () => {
    source.close();
    await openConversation(conversationId);
  });
  source.addEventListener("error", async () => {
    source.close();
    await openConversation(conversationId);
  });
}

$("message-form").onsubmit = async (event) => {
  event.preventDefault();
  await submitMessage();
};

$("generate-image").onclick = generateImage;

Promise.all([refresh(), refreshImageWorkflows()]).catch((error) => {
  console.error(error);
  $("image-status").textContent = "Impossible de charger les workflows image";
});
