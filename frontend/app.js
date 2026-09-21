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
  $("models").innerHTML = models.map((model) => `
    <div class="model"><button data-model="${model.id}">
      <strong>${escapeHtml(model.label)}</strong><br>
      <span class="level">${escapeHtml(model.fit.level)} · estimé${model.loaded ? " · chargé" : ""}</span>
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

$("new-conversation").onclick = async () => {
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

refresh();
