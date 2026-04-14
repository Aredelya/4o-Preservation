const state = {
  conversations: [],
  activeConversation: null,
  memories: [],
  searchQuery: "",
  isSending: false,
  editingMessageId: null,
};

let conversationSearchTimer = null;
let latestMessagesRequest = 0;
let latestConversationsRequest = 0;
let statusTimer = null;

const conversationList = document.getElementById("conversationList");
const messageList = document.getElementById("messageList");
const conversationTitle = document.getElementById("conversationTitle");
const messageInput = document.getElementById("messageInput");
const fileInput = document.getElementById("fileInput");
const newConversationBtn = document.getElementById("newConversation");
const sendMessageBtn = document.getElementById("sendMessage");
const enableWebSearchInput = document.getElementById("enableWebSearch");
const memoryInput = document.getElementById("memoryInput");
const saveMemoryBtn = document.getElementById("saveMemory");
const memoryList = document.getElementById("memoryList");
const clearMemoriesBtn = document.getElementById("clearMemories");
const conversationSearchInput = document.getElementById("conversationSearch");
const clearConversationSearchBtn = document.getElementById("clearConversationSearch");
const statusBanner = document.getElementById("statusBanner");
const historyToggleBtn = document.getElementById("historyToggle");
const closeHistoryBtn = document.getElementById("closeHistory");
const logoutButton = document.getElementById("logoutButton");

const setEditingState = (messageId = null) => {
  state.editingMessageId = messageId;
  sendMessageBtn.textContent = messageId ? "Save & resend" : "Send";
};

const mobileHistoryMedia = window.matchMedia("(max-width: 1100px) and (orientation: portrait)");

const isMobileHistoryMode = () => mobileHistoryMedia.matches;

const setMobileHistoryOpen = (open) => {
  const shouldOpen = open && isMobileHistoryMode();
  document.body.classList.toggle("mobile-history-open", shouldOpen);

  if (historyToggleBtn) {
    historyToggleBtn.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
  }
};

const closeMobileHistory = () => setMobileHistoryOpen(false);
const openMobileHistory = () => setMobileHistoryOpen(true);

const api = async (path, options = {}) => {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });

  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Authentication required");
  }

  const contentType = response.headers.get("Content-Type") || "";
  const isJson = contentType.includes("application/json");
  const body = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      isJson && body && typeof body === "object" && body.error
        ? body.error
        : typeof body === "string" && body.trim()
          ? body
          : "Request failed";
    throw new Error(message);
  }

  return body;
};

const setStatus = (message = "", kind = "", timeoutMs = 0) => {
  if (!statusBanner) return;

  if (statusTimer) {
    clearTimeout(statusTimer);
    statusTimer = null;
  }

  statusBanner.textContent = message;
  statusBanner.className = kind ? `status ${kind}` : "status";

  if (message && timeoutMs > 0) {
    statusTimer = setTimeout(() => {
      statusBanner.textContent = "";
      statusBanner.className = "status";
      statusTimer = null;
    }, timeoutMs);
  }
};

const setComposerBusy = (busy) => {
  state.isSending = busy;
  messageInput.disabled = busy;
  fileInput.disabled = busy;
  sendMessageBtn.disabled = busy;
  sendMessageBtn.textContent = busy
    ? (state.editingMessageId ? "Saving..." : "Sending...")
    : (state.editingMessageId ? "Save & resend" : "Send");
};

const clearDraftAttachments = () => {
  fileInput.value = "";
};

const startEditingMessage = (message) => {
  if (!message || message.role !== "user") return;

  state.editingMessageId = message.id;
  messageInput.value = message.content || "";
  clearDraftAttachments();
  setEditingState(message.id);
  messageInput.focus();
  messageInput.setSelectionRange(messageInput.value.length, messageInput.value.length);
  setStatus("Editing message. Everything after it will be replaced when you resend.");
};

const cancelEditingMessage = () => {
  setEditingState(null);
  messageInput.value = "";
  clearDraftAttachments();
  setStatus("", "");
};

const escapeHtml = (value = "") =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const renderMarkdown = (content = "") => {
  if (!content) return "";

  const lines = String(content).replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let inCodeBlock = false;
  let codeLang = "";
  let inList = false;

  const closeListIfOpen = () => {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
  };

  const renderInline = (text) => {
    let line = escapeHtml(text);
    line = line.replace(/`([^`]+?)`/g, "<code>$1</code>");
    line = line.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
    line = line.replace(/\*([^*]+?)\*/g, "<em>$1</em>");
    line = line.replace(
      /\[([^\]]+?)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
    return line;
  };

  for (const rawLine of lines) {
    const line = rawLine ?? "";

    if (line.startsWith("```")) {
      closeListIfOpen();
      if (!inCodeBlock) {
        inCodeBlock = true;
        codeLang = line.slice(3).trim();
        const classAttr = codeLang ? ` class="lang-${escapeHtml(codeLang)}"` : "";
        html.push(`<pre><code${classAttr}>`);
      } else {
        inCodeBlock = false;
        codeLang = "";
        html.push("</code></pre>");
      }
      continue;
    }

    if (inCodeBlock) {
      html.push(`${escapeHtml(line)}\n`);
      continue;
    }

    if (!line.trim()) {
      closeListIfOpen();
      html.push("<br>");
      continue;
    }

    const listMatch = line.match(/^\s*[-*]\s+(.+)$/);
    if (listMatch) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${renderInline(listMatch[1])}</li>`);
      continue;
    }

    closeListIfOpen();

    if (line.startsWith(">")) {
      html.push(`<blockquote>${renderInline(line.slice(1).trim())}</blockquote>`);
      continue;
    }

    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      html.push(`<h${level}>${renderInline(headingMatch[2])}</h${level}>`);
      continue;
    }

    html.push(`<p>${renderInline(line)}</p>`);
  }

  closeListIfOpen();

  if (inCodeBlock) {
    html.push("</code></pre>");
  }

  return html.join("");
};

const createBubble = ({ role, content, extraClass = "", id = "" }) => {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}${extraClass ? ` ${extraClass}` : ""}`;

  if (id) {
    bubble.dataset.id = id;
  }

  bubble.innerHTML = renderMarkdown(content);
  return bubble;
};

const createMessageElement = (message) => {
  const wrap = document.createElement("div");
  wrap.className = `message-row ${message.role}`;

  const bubble = createBubble({
    role: message.role,
    content: message.content,
    id: message.id ? String(message.id) : "",
  });

  wrap.appendChild(bubble);

  if (message.role === "user" && message.id) {
    const actions = document.createElement("div");
    actions.className = "message-actions";

    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "secondary message-action-button";
    editButton.textContent = "Edit";
    editButton.onclick = () => startEditingMessage(message);

    actions.appendChild(editButton);
    wrap.appendChild(actions);
  }

  return wrap;
};

const appendMessageBubble = ({ role, content, extraClass = "", id = "" }) => {
  const bubble = createBubble({ role, content, extraClass, id });
  messageList.appendChild(bubble);
  scheduleMessageBottomSnap();
  return bubble;
};

const renderMessages = (messages = [], autoSnap = true) => {
  messageList.innerHTML = "";

  for (const message of messages) {
    messageList.appendChild(createMessageElement(message));
  }

  if (autoSnap) {
    scheduleMessageBottomSnap();
  }
};

const renderConversations = () => {
  conversationList.innerHTML = "";

  for (const convo of state.conversations) {
    const row = document.createElement("div");
    row.className = "list-item";

    const button = document.createElement("button");
    const title = `${convo.title || "Untitled"} · ${convo.id.slice(0, 8)}`;
    const snippet = (convo.snippet || "").replace(/\s+/g, " ").trim();
    const preview = snippet.length > 96 ? `${snippet.slice(0, 93)}...` : snippet;

    button.textContent = preview ? `${title}\n${preview}` : title;
    button.className = convo.id === state.activeConversation ? "active" : "";
    button.type = "button";
    button.onclick = () => {
      void selectConversation(convo.id);
    };

    const remove = document.createElement("button");
    remove.className = "danger";
    remove.textContent = "✕";
    remove.type = "button";
    remove.title = "Delete conversation";
    remove.setAttribute("aria-label", "Delete conversation");
    remove.onclick = async () => {
      if (!confirm("Delete this conversation?")) return;

      try {
        await deleteConversation(convo.id);
      } catch (error) {
        setStatus(`Failed to delete conversation: ${error.message}`, "error");
      }
    };

    row.appendChild(button);
    row.appendChild(remove);
    conversationList.appendChild(row);
  }
};

const renderMemories = () => {
  memoryList.innerHTML = "";

  for (const memory of state.memories) {
    const card = document.createElement("div");
    card.className = "memory-item";

    const text = document.createElement("div");
    text.textContent = memory.content;

    const meta = document.createElement("div");
    meta.className = "muted";
    meta.textContent = `#${memory.id} · ${memory.created_at}`;

    const actions = document.createElement("div");
    actions.className = "memory-actions";

    const remove = document.createElement("button");
    remove.textContent = "Delete";
    remove.type = "button";
    remove.onclick = async () => {
      try {
        await deleteMemory(memory.id);
      } catch (error) {
        setStatus(`Failed to delete memory: ${error.message}`, "error");
      }
    };

    actions.appendChild(remove);
    card.appendChild(text);
    card.appendChild(meta);
    card.appendChild(actions);
    memoryList.appendChild(card);
  }
};

const scrollMessagesToBottom = () => {
  messageList.scrollTop = messageList.scrollHeight;
  const lastBubble = messageList.lastElementChild;

  if (lastBubble) {
    lastBubble.scrollIntoView({ block: "end" });
  }
};

const shouldSkipAutoSnap = () => {
  const active = document.activeElement;
  return active === conversationSearchInput || active === memoryInput;
};

const scheduleMessageBottomSnap = () => {
  if (shouldSkipAutoSnap()) return;

  const delays = [0, 50, 140, 280, 520];

  for (const delay of delays) {
    setTimeout(() => {
      if (!shouldSkipAutoSnap()) {
        scrollMessagesToBottom();
      }
    }, delay);
  }

  requestAnimationFrame(() => {
    if (!shouldSkipAutoSnap()) {
      scrollMessagesToBottom();
    }
  });
};

const loadMessages = async (conversationId) => {
  const requestId = ++latestMessagesRequest;
  const data = await api(`/api/conversations/${conversationId}`);

  if (requestId !== latestMessagesRequest) return;
  if (conversationId !== state.activeConversation) return;

  conversationTitle.textContent = data.title || "Chat";

  if (
    state.editingMessageId &&
    !data.messages.some((message) => message.id === state.editingMessageId)
  ) {
    setEditingState(null);
  }

  renderMessages(data.messages);
};

const loadConversations = async ({ refreshMessages = true } = {}) => {
  const requestId = ++latestConversationsRequest;
  const query = state.searchQuery.trim();
  const previousActiveConversation = state.activeConversation;

  const searchSuffix = query ? `?q=${encodeURIComponent(query)}` : "";
  const data = await api(`/api/conversations${searchSuffix}`);

  if (requestId !== latestConversationsRequest) return;

  state.conversations = data.conversations;

  if (!state.activeConversation && state.conversations.length) {
    state.activeConversation = state.conversations[0].id;
  }

  if (
    state.activeConversation &&
    !state.conversations.some((convo) => convo.id === state.activeConversation)
  ) {
    state.activeConversation = state.conversations.length ? state.conversations[0].id : null;
  }

  renderConversations();

  if (!refreshMessages) return;

  const activeChanged = previousActiveConversation !== state.activeConversation;

  if (state.activeConversation) {
    if (activeChanged || messageList.childElementCount === 0) {
      await loadMessages(state.activeConversation);
    }
  } else {
    conversationTitle.textContent = query ? "No matching conversation" : "Chat";
    renderMessages([], false);
  }
};

const loadMemories = async () => {
  const data = await api("/api/memories");
  state.memories = data.memories;
  renderMemories();
};

const selectConversation = async (conversationId) => {
  if (state.isSending) return;

  state.activeConversation = conversationId;
  setEditingState(null);
  clearDraftAttachments();
  renderConversations();
  await loadMessages(conversationId);
  closeMobileHistory();
};

const createConversation = async () => {
  if (state.isSending) return;

  const data = await api("/api/conversations", { method: "POST" });
  setEditingState(null);
  await loadConversations({ refreshMessages: false });
  await selectConversation(data.id);
  closeMobileHistory();
};

const ensureActiveConversation = async () => {
  if (state.activeConversation) {
    return state.activeConversation;
  }

  const data = await api("/api/conversations", { method: "POST" });
  state.activeConversation = data.id;
  await loadConversations({ refreshMessages: false });
  return data.id;
};

const buildAttachmentSummary = (files) => {
  if (!files.length) return "";
  return `[Attached: ${files.map((file) => file.name).join(", ")}]`;
};

const readAttachments = async (files) => {
  const attachments = [];

  for (const file of files) {
    if (file.type.startsWith("image/")) {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error(`Failed to read image: ${file.name}`));
        reader.readAsDataURL(file);
      });

      attachments.push({
        kind: "image",
        name: file.name,
        data_url: dataUrl,
      });
    } else {
      const text = await file.text();
      attachments.push({
        kind: "text",
        name: file.name,
        text,
      });
    }
  }

  return attachments;
};

const sendMessage = async () => {
  if (state.isSending) return;

  const rawContent = messageInput.value;
  const content = rawContent.trim();
  const files = Array.from(fileInput.files || []);

  if (!content && files.length === 0) return;

  setComposerBusy(true);
  setStatus("");

  let pendingBubble = null;

  try {
    const conversationId = await ensureActiveConversation();

    if (content.toLowerCase().startsWith("/title")) {
      const newTitle = content.slice("/title".length).trim();

      if (!newTitle) {
        throw new Error("Missing title text");
      }

      await api("/api/title", {
        method: "POST",
        body: JSON.stringify({
          conversation_id: conversationId,
          title: newTitle,
        }),
      });

      conversationTitle.textContent = newTitle;
      messageInput.value = "";
      fileInput.value = "";
      await loadConversations({ refreshMessages: false });
      setStatus("Title updated.", "", 2000);
      return;
    }

    const attachments = await readAttachments(files);
    const isEditing = !!state.editingMessageId;

    const optimisticTextParts = [];
    if (content) {
      optimisticTextParts.push(content);
    }

    const attachmentSummary = buildAttachmentSummary(files);
    if (attachmentSummary) {
      optimisticTextParts.push(attachmentSummary);
    }

    if (!isEditing) {
      appendMessageBubble({
        role: "user",
        content: optimisticTextParts.join("\n\n") || "(Attachment upload)",
      });
    }

    pendingBubble = appendMessageBubble({
      role: "assistant",
      content: "Thinking...",
      extraClass: "pending",
      id: `pending-${Date.now()}`,
    });

    const requestPath = isEditing ? "/api/edit" : "/api/send";
    const requestBody = {
      conversation_id: conversationId,
      content,
      attachments,
      enable_web_search: !!enableWebSearchInput?.checked,
    };

    if (isEditing) {
      requestBody.message_id = state.editingMessageId;
    }

    messageInput.value = "";
    fileInput.value = "";

    const result = await api(requestPath, {
      method: "POST",
      body: JSON.stringify(requestBody),
    });

    if (pendingBubble) {
      pendingBubble.innerHTML = renderMarkdown(result.assistant_message?.content || "(No response)");
      pendingBubble.classList.remove("pending");
      pendingBubble.classList.remove("error");
    }

    setEditingState(null);
    await loadConversations({ refreshMessages: false });
    await loadMessages(conversationId);
    setStatus(isEditing ? "Message updated and response regenerated." : "", "", isEditing ? 2500 : 0);
  } catch (error) {
    if (pendingBubble) {
      pendingBubble.textContent = `Failed to send: ${error.message}`;
      pendingBubble.classList.remove("pending");
      pendingBubble.classList.add("error");
    } else {
      appendMessageBubble({
        role: "assistant",
        content: `Failed to send: ${error.message}`,
        extraClass: "error",
      });
    }

    setStatus(`Failed to send: ${error.message}`, "error");
  } finally {
    setComposerBusy(false);
    messageInput.focus();
  }
};

const addMemory = async () => {
  const content = memoryInput.value.trim();
  if (!content) return;

  memoryInput.value = "";

  try {
    await api("/api/memories", {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    await loadMemories();
    setStatus("Memory saved.", "", 2000);
  } catch (error) {
    setStatus(`Failed to save memory: ${error.message}`, "error");
  }
};

const deleteMemory = async (id) => {
  await api(`/api/memories/${id}`, { method: "DELETE" });
  await loadMemories();
  setStatus("Memory deleted.", "", 2000);
};

const clearMemories = async () => {
  if (!confirm("Clear all memories? This cannot be undone.")) return;

  try {
    await api("/api/memories", { method: "DELETE" });
    await loadMemories();
    setStatus("Memories cleared.", "", 2000);
  } catch (error) {
    setStatus(`Failed to clear memories: ${error.message}`, "error");
  }
};

const deleteConversation = async (id) => {
  await api(`/api/conversations/${id}`, { method: "DELETE" });

  if (state.activeConversation === id) {
    state.activeConversation = null;
    conversationTitle.textContent = "Chat";
    renderMessages([], false);
  }

  await loadConversations();
  setStatus("Conversation deleted.", "", 2000);
};

const initializeApp = async () => {
  try {
    await loadMemories();
  } catch (error) {
    console.error("Failed to load memories:", error);
    setStatus(`Failed to load memories: ${error.message}`, "error");
  }

  try {
    await loadConversations();
    scheduleMessageBottomSnap();
  } catch (error) {
    console.error("Failed to load conversations:", error);
    setStatus(`Failed to load conversations: ${error.message}`, "error");
  }
};

newConversationBtn.onclick = async () => {
  try {
    await createConversation();
    setStatus("Conversation created.", "", 2000);
  } catch (error) {
    setStatus(`Failed to create conversation: ${error.message}`, "error");
  }
};

sendMessageBtn.onclick = () => {
  void sendMessage();
};

saveMemoryBtn.onclick = () => {
  void addMemory();
};

clearMemoriesBtn.onclick = () => {
  void clearMemories();
};

if (historyToggleBtn) {
  historyToggleBtn.onclick = () => {
    const isOpen = document.body.classList.contains("mobile-history-open");
    setMobileHistoryOpen(!isOpen);
  };
}

if (closeHistoryBtn) {
  closeHistoryBtn.onclick = () => {
    closeMobileHistory();
  };
}

if (logoutButton) {
  logoutButton.onclick = () => {
    window.location.href = "/logout";
  };
}

messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.editingMessageId) {
    event.preventDefault();
    cancelEditingMessage();
    return;
  }

  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    void sendMessage();
  }
});

conversationSearchInput.addEventListener("focus", () => {
  if (conversationSearchTimer) {
    clearTimeout(conversationSearchTimer);
    conversationSearchTimer = null;
  }
});

conversationSearchInput.addEventListener("input", () => {
  state.searchQuery = conversationSearchInput.value;

  if (conversationSearchTimer) {
    clearTimeout(conversationSearchTimer);
  }

  conversationSearchTimer = setTimeout(() => {
    void loadConversations({ refreshMessages: false }).catch((error) => {
      setStatus(`Failed to search conversations: ${error.message}`, "error");
    });
  }, 180);
});

clearConversationSearchBtn.onclick = async () => {
  if (conversationSearchTimer) {
    clearTimeout(conversationSearchTimer);
    conversationSearchTimer = null;
  }

  conversationSearchInput.value = "";
  state.searchQuery = "";

  try {
    await loadConversations({ refreshMessages: false });
    setStatus("");
  } catch (error) {
    setStatus(`Failed to clear search: ${error.message}`, "error");
  }
};

mobileHistoryMedia.addEventListener("change", () => {
  if (!isMobileHistoryMode()) {
    closeMobileHistory();
  }
});

initializeApp();
window.addEventListener("load", scheduleMessageBottomSnap);
