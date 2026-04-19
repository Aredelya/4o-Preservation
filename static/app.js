const state = {
  conversations: [],
  activeConversation: null,
  memories: [],
  memorySuggestions: [],
  editingSuggestionId: null,
  editingSuggestionDraft: null,
  settings: null,
  searchQuery: "",
  isSending: false,
  editingMessageId: null,
  chatSearchQuery: "",
  chatSearchMatches: [],
  activeChatSearchIndex: -1,
  activeStreamController: null,
  activeStreamKind: null,
};

let conversationSearchTimer = null;
let latestMessagesRequest = 0;
let latestConversationsRequest = 0;
let statusTimer = null;
let fileSearchStatusPollTimer = null;
let chatSearchBar = null;
let chatSearchInput = null;
let chatSearchPrevBtn = null;
let chatSearchNextBtn = null;
let chatSearchClearBtn = null;
let chatSearchCount = null;

const conversationList = document.getElementById("conversationList");
const messageList = document.getElementById("messageList");
const conversationTitle = document.getElementById("conversationTitle");
const messageInput = document.getElementById("messageInput");
const fileInput = document.getElementById("fileInput");
const imageCommandHint = document.getElementById("imageCommandHint");
const newConversationBtn = document.getElementById("newConversation");
const sendMessageBtn = document.getElementById("sendMessage");
const enableWebSearchInput = document.getElementById("enableWebSearch");
const enableCodeInterpreterInput = document.getElementById("enableCodeInterpreter");
const modelSelect = document.getElementById("modelSelect");
const enableReasoningInput = document.getElementById("enableReasoning");
const reasoningEffortSelect = document.getElementById("reasoningEffort");
const memoryInput = document.getElementById("memoryInput");
const memoryKindInput = document.getElementById("memoryKind");
const memoryScopeInput = document.getElementById("memoryScope");
const memoryPinnedInput = document.getElementById("memoryPinned");
const saveMemoryBtn = document.getElementById("saveMemory");
const memorySuggestionsList = document.getElementById("memorySuggestions");
const memoryList = document.getElementById("memoryList");
const clearMemoriesBtn = document.getElementById("clearMemories");
const conversationSearchInput = document.getElementById("conversationSearch");
const clearConversationSearchBtn = document.getElementById("clearConversationSearch");
const statusBanner = document.getElementById("statusBanner");
const historyToggleBtn = document.getElementById("historyToggle");
const closeHistoryBtn = document.getElementById("closeHistory");
const logoutButton = document.getElementById("logoutButton");
const exportConversationMarkdownBtn = document.getElementById("exportConversationMarkdown");
const exportConversationJsonBtn = document.getElementById("exportConversationJson");
const cancelResponseBtn = document.getElementById("cancelResponseBtn");

const MODEL_STORAGE_KEY = "chat-model-selection";
const REASONING_ENABLED_STORAGE_KEY = "chat-reasoning-enabled";
const REASONING_EFFORT_STORAGE_KEY = "chat-reasoning-effort";

const setEditingState = (messageId = null) => {
  state.editingMessageId = messageId;
  sendMessageBtn.textContent = messageId ? "Save & resend" : "Send";
};

const getStoredBoolean = (key, fallback = false) => {
  const value = window.localStorage.getItem(key);
  if (value === null) return fallback;
  return value === "1";
};

const getStoredString = (key, fallback = "") => {
  const value = window.localStorage.getItem(key);
  return value === null ? fallback : value;
};

const updateReasoningControls = () => {
  if (!enableReasoningInput || !reasoningEffortSelect) return;
  const enabled = !!enableReasoningInput.checked;
  reasoningEffortSelect.disabled = !enabled;
};

const populateChatSettingsControls = () => {
  if (!modelSelect || !state.settings) return;

  const settings = state.settings;
  const storedModel =
    getStoredString(MODEL_STORAGE_KEY, settings.default_model) || settings.default_model;
  const selectedReasoningEffort =
    getStoredString(REASONING_EFFORT_STORAGE_KEY, settings.default_reasoning_effort)
      || settings.default_reasoning_effort;

  modelSelect.innerHTML = "";
  for (const model of settings.chat_models || []) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.label || model.id;
    modelSelect.appendChild(option);
  }
  const availableModels = [...modelSelect.options].map((option) => option.value);
  modelSelect.value = availableModels.includes(storedModel)
    ? storedModel
    : settings.default_model;

  if (reasoningEffortSelect) {
    reasoningEffortSelect.innerHTML = "";
    for (const effort of settings.reasoning_efforts || ["low", "medium", "high"]) {
      const option = document.createElement("option");
      option.value = effort;
      option.textContent = effort.charAt(0).toUpperCase() + effort.slice(1);
      reasoningEffortSelect.appendChild(option);
    }
    reasoningEffortSelect.value = selectedReasoningEffort;
  }

  if (enableReasoningInput) {
    enableReasoningInput.checked = getStoredBoolean(REASONING_ENABLED_STORAGE_KEY, false);
  }

  updateReasoningControls();
};

const getSelectedChatOptions = () => ({
  model: modelSelect?.value || state.settings?.default_model || "",
  enable_reasoning: !!enableReasoningInput?.checked,
  reasoning_effort: reasoningEffortSelect?.value || state.settings?.default_reasoning_effort || "medium",
});

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

const apiStream = async (path, payload, handlers = {}) => {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(payload),
    signal: handlers.signal,
  });

  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Authentication required");
  }

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body?.error) {
        message = body.error;
      }
    } catch {
      try {
        const text = await response.text();
        if (text) {
          message = text;
        }
      } catch {}
    }
    throw new Error(message || "Streaming request failed");
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("Streaming not supported by this browser");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";

      for (const frame of frames) {
        const lines = frame.split("\n");
        let dataLine = "";
        for (const line of lines) {
          if (line.startsWith("data:")) {
            dataLine += line.slice(5).trim();
          }
        }
        if (!dataLine) continue;

        try {
          const event = JSON.parse(dataLine);
          handlers.onEvent?.(event);
        } catch (error) {
          console.warn("Failed to parse streaming event:", error);
        }
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {}
  }
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
const updateCancelResponseButton = () => {
  if (!cancelResponseBtn) return;

  const isActive = !!state.activeStreamController;
  cancelResponseBtn.hidden = !isActive;
  cancelResponseBtn.disabled = !isActive;
};

const beginActiveStream = (controller, kind = "response") => {
  state.activeStreamController = controller;
  state.activeStreamKind = kind;
  updateCancelResponseButton();
};

const clearActiveStream = () => {
  state.activeStreamController = null;
  state.activeStreamKind = null;
  updateCancelResponseButton();
};

const clearFileSearchStatusPoll = () => {
  if (fileSearchStatusPollTimer) {
    clearTimeout(fileSearchStatusPollTimer);
    fileSearchStatusPollTimer = null;
  }
};

const scheduleFileSearchStatusPoll = (conversationId, messages = []) => {
  clearFileSearchStatusPoll();

  const hasProcessingStatus = Array.isArray(messages) && messages.some((message) => {
    if (!message || message.role !== "user" || !message.file_search_status) return false;
    return String(message.file_search_status.state || "").trim() === "processing";
  });

  if (!hasProcessingStatus) return;
  if (!conversationId || conversationId !== state.activeConversation) return;
  if (state.isSending || state.activeStreamController) return;

  fileSearchStatusPollTimer = setTimeout(() => {
    if (!state.activeConversation || state.activeConversation !== conversationId) return;
    loadMessages(conversationId, { autoSnap: false, quietFileSearchRefresh: true }).catch((error) => {
      console.warn("Failed to refresh file search status:", error);
    });
  }, 3500);
};

const cancelActiveStream = () => {
  if (!state.activeStreamController) return false;
  state.activeStreamController.abort();
  return true;
};
const updateConversationActionState = () => {
  const disabled = !state.activeConversation || state.isSending;
  if (exportConversationMarkdownBtn) {
    exportConversationMarkdownBtn.disabled = disabled;
  }
  if (exportConversationJsonBtn) {
    exportConversationJsonBtn.disabled = disabled;
  }
};

const escapeRegExp = (value = "") =>
  String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const clearChatSearchHighlights = (root = messageList) => {
  if (!root) return;

  const marks = Array.from(root.querySelectorAll("mark.chat-search-hit"));
  for (const mark of marks) {
    const parent = mark.parentNode;
    if (!parent) continue;
    parent.replaceChild(document.createTextNode(mark.textContent || ""), mark);
    parent.normalize();
  }

  state.chatSearchMatches = [];
  state.activeChatSearchIndex = -1;
};

const updateChatSearchControls = () => {
  const total = state.chatSearchMatches.length;
  const active = total > 0 && state.activeChatSearchIndex >= 0
    ? state.activeChatSearchIndex + 1
    : 0;

  if (chatSearchCount) {
    chatSearchCount.textContent = total ? `${active}/${total}` : "0/0";
  }

  if (chatSearchPrevBtn) {
    chatSearchPrevBtn.disabled = total <= 1;
  }

  if (chatSearchNextBtn) {
    chatSearchNextBtn.disabled = total <= 1;
  }

  if (chatSearchClearBtn) {
    chatSearchClearBtn.disabled = !state.chatSearchQuery.trim();
  }
};

const setActiveChatSearchMatch = (index, { scroll = true } = {}) => {
  const matches = state.chatSearchMatches;
  if (!matches.length) {
    state.activeChatSearchIndex = -1;
    updateChatSearchControls();
    return;
  }

  const normalizedIndex = ((index % matches.length) + matches.length) % matches.length;
  state.activeChatSearchIndex = normalizedIndex;

  matches.forEach((mark, idx) => {
    mark.classList.toggle("active", idx === normalizedIndex);
  });

  updateChatSearchControls();

  if (scroll) {
    const activeMark = matches[normalizedIndex];
    activeMark?.scrollIntoView({
      behavior: "smooth",
      block: "center",
      inline: "nearest",
    });
  }
};

const highlightTextNodeMatches = (textNode, regex) => {
  const text = textNode.nodeValue || "";
  regex.lastIndex = 0;

  let match = regex.exec(text);
  if (!match) return [];

  const fragment = document.createDocumentFragment();
  const createdMarks = [];
  let lastIndex = 0;

  while (match) {
    const start = match.index;
    const end = start + match[0].length;

    if (start > lastIndex) {
      fragment.appendChild(document.createTextNode(text.slice(lastIndex, start)));
    }

    const mark = document.createElement("mark");
    mark.className = "chat-search-hit";
    mark.textContent = text.slice(start, end);
    fragment.appendChild(mark);
    createdMarks.push(mark);

    lastIndex = end;
    match = regex.exec(text);
  }

  if (lastIndex < text.length) {
    fragment.appendChild(document.createTextNode(text.slice(lastIndex)));
  }

  textNode.parentNode?.replaceChild(fragment, textNode);
  return createdMarks;
};

const applyChatSearchHighlights = () => {
  if (!messageList) return;

  clearChatSearchHighlights(messageList);

  const query = state.chatSearchQuery.trim();
  if (!query) {
    updateChatSearchControls();
    return;
  }

  const regex = new RegExp(escapeRegExp(query), "gi");
  const matches = [];

  const bubbles = Array.from(messageList.querySelectorAll(".bubble"));
  for (const bubble of bubbles) {
    const walker = document.createTreeWalker(
      bubble,
      NodeFilter.SHOW_TEXT,
      {
        acceptNode(node) {
          const value = node.nodeValue || "";
          if (!value.trim()) return NodeFilter.FILTER_REJECT;

          const parent = node.parentElement;
          if (!parent) return NodeFilter.FILTER_REJECT;

          if (
            parent.closest("pre, code, script, style, textarea, button") ||
            parent.closest("mark.chat-search-hit")
          ) {
            return NodeFilter.FILTER_REJECT;
          }

          return NodeFilter.FILTER_ACCEPT;
        },
      },
    );

    const textNodes = [];
    let current;
    while ((current = walker.nextNode())) {
      textNodes.push(current);
    }

    for (const textNode of textNodes) {
      matches.push(...highlightTextNodeMatches(textNode, regex));
    }
  }

  state.chatSearchMatches = matches;
  state.activeChatSearchIndex = matches.length ? 0 : -1;

  if (matches.length) {
    setActiveChatSearchMatch(0, { scroll: false });
  } else {
    updateChatSearchControls();
  }
};

const runChatSearch = (query, { scrollToFirst = false } = {}) => {
  state.chatSearchQuery = String(query || "");
  applyChatSearchHighlights();

  if (scrollToFirst && state.chatSearchMatches.length) {
    setActiveChatSearchMatch(0, { scroll: true });
  } else {
    updateChatSearchControls();
  }
};

const goToNextChatSearchMatch = () => {
  if (!state.chatSearchMatches.length) return;
  setActiveChatSearchMatch(state.activeChatSearchIndex + 1);
};

const goToPreviousChatSearchMatch = () => {
  if (!state.chatSearchMatches.length) return;
  setActiveChatSearchMatch(state.activeChatSearchIndex - 1);
};

const clearChatSearch = () => {
  state.chatSearchQuery = "";
  if (chatSearchInput) {
    chatSearchInput.value = "";
  }
  clearChatSearchHighlights(messageList);
  updateChatSearchControls();
};

const ensureChatSearchUI = () => {
  if (!messageList) return;

  const isMobileChatLayout = window.matchMedia("(max-width: 760px)").matches;

  if (isMobileChatLayout) {
    if (chatSearchBar) {
      chatSearchBar.remove();
      chatSearchBar = null;
      chatSearchInput = null;
      chatSearchPrevBtn = null;
      chatSearchNextBtn = null;
      chatSearchClearBtn = null;
      chatSearchCount = null;
    }
    return;
  }

  if (chatSearchBar) return;

  const style = document.createElement("style");
  style.textContent = `
    .chat-search-bar {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin: 10px 0 12px;
      padding: 8px 10px;
      border-radius: 10px;
    }

    .chat-search-input-wrap {
      display: flex;
      align-items: center;
      gap: 8px;
      flex: 1 1 260px;
      min-width: 0;
    }

    .chat-search-icon {
      font-size: 0.95em;
      line-height: 1;
      opacity: 0.75;
      user-select: none;
      flex: 0 0 auto;
    }

    .chat-search-bar input {
      min-width: 0;
      width: 100%;
      flex: 1 1 auto;
    }

    .chat-search-controls {
      display: flex;
      align-items: center;
      gap: 8px;
      flex: 0 0 auto;
      flex-wrap: wrap;
      margin-left: auto;
    }

    .chat-search-count {
      font-size: 0.9em;
      opacity: 0.8;
      min-width: 3.5em;
      text-align: center;
      white-space: nowrap;
    }

    mark.chat-search-hit {
      background: rgba(255, 230, 120, 0.85);
      color: inherit;
      padding: 0 1px;
      border-radius: 2px;
    }

    mark.chat-search-hit.active {
      outline: 2px solid rgba(255, 170, 0, 0.9);
      background: rgba(255, 200, 80, 0.95);
    }

    @media (max-width: 700px) {
      .chat-search-bar {
        align-items: stretch;
      }

      .chat-search-input-wrap {
        flex: 1 1 100%;
        width: 100%;
      }

      .chat-search-controls {
        width: 100%;
        margin-left: 0;
        justify-content: flex-start;
      }

      .chat-search-controls button {
        flex: 0 0 auto;
      }

      .chat-search-count {
        margin-left: auto;
      }
    }
  `;
  document.head.appendChild(style);

  chatSearchBar = document.createElement("div");
  chatSearchBar.className = "chat-search-bar";

  const chatSearchInputWrap = document.createElement("div");
  chatSearchInputWrap.className = "chat-search-input-wrap";

  const chatSearchIcon = document.createElement("span");
  chatSearchIcon.className = "chat-search-icon";
  chatSearchIcon.textContent = "/";
  chatSearchIcon.setAttribute("aria-hidden", "true");

  chatSearchInput = document.createElement("input");
  chatSearchInput.type = "search";
  chatSearchInput.placeholder = "Search this chat";
  chatSearchInput.autocomplete = "off";
  chatSearchInput.spellcheck = false;
  chatSearchInput.setAttribute("aria-label", "Search current conversation");

  chatSearchPrevBtn = document.createElement("button");
  chatSearchPrevBtn.type = "button";
  chatSearchPrevBtn.className = "secondary";
  chatSearchPrevBtn.textContent = "Up";
  chatSearchPrevBtn.title = "Previous match";
  chatSearchPrevBtn.setAttribute("aria-label", "Previous match");

  chatSearchNextBtn = document.createElement("button");
  chatSearchNextBtn.type = "button";
  chatSearchNextBtn.className = "secondary";
  chatSearchNextBtn.textContent = "Down";
  chatSearchNextBtn.title = "Next match";
  chatSearchNextBtn.setAttribute("aria-label", "Next match");

  chatSearchCount = document.createElement("span");
  chatSearchCount.className = "chat-search-count";
  chatSearchCount.textContent = "0/0";

  chatSearchClearBtn = document.createElement("button");
  chatSearchClearBtn.type = "button";
  chatSearchClearBtn.className = "secondary";
  chatSearchClearBtn.textContent = "Clear";

  chatSearchInput.addEventListener("input", () => {
    runChatSearch(chatSearchInput.value);
  });

  chatSearchInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (event.shiftKey) {
        goToPreviousChatSearchMatch();
      } else {
        goToNextChatSearchMatch();
      }
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      clearChatSearch();
      messageInput?.focus();
    }
  });

  chatSearchPrevBtn.onclick = () => goToPreviousChatSearchMatch();
  chatSearchNextBtn.onclick = () => goToNextChatSearchMatch();
  chatSearchClearBtn.onclick = () => clearChatSearch();

  const chatSearchControls = document.createElement("div");
  chatSearchControls.className = "chat-search-controls";

  chatSearchInputWrap.appendChild(chatSearchIcon);
  chatSearchInputWrap.appendChild(chatSearchInput);

  chatSearchControls.appendChild(chatSearchPrevBtn);
  chatSearchControls.appendChild(chatSearchNextBtn);
  chatSearchControls.appendChild(chatSearchCount);
  chatSearchControls.appendChild(chatSearchClearBtn);

  chatSearchBar.appendChild(chatSearchInputWrap);
  chatSearchBar.appendChild(chatSearchControls);

  const anchor =
    conversationTitle?.parentElement && conversationTitle.parentElement.contains(messageList)
      ? conversationTitle
      : null;

  if (anchor?.parentElement) {
    anchor.insertAdjacentElement("afterend", chatSearchBar);
  } else {
    messageList.parentElement?.insertBefore(chatSearchBar, messageList);
  }

  updateChatSearchControls();
};

const downloadConversationExport = async (format) => {
  if (!state.activeConversation) return;

  const response = await fetch(
    `/api/conversations/${encodeURIComponent(state.activeConversation)}/export?format=${encodeURIComponent(format)}`,
    {
      method: "GET",
      credentials: "same-origin",
    },
  );

  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Authentication required");
  }

  if (!response.ok) {
    let message = "Export failed";
    try {
      const contentType = response.headers.get("Content-Type") || "";
      if (contentType.includes("application/json")) {
        const body = await response.json();
        if (body && typeof body === "object" && body.error) {
          message = body.error;
        }
      } else {
        const text = await response.text();
        if (text.trim()) {
          message = text.trim();
        }
      }
    } catch (_error) {
      // keep fallback message
    }
    throw new Error(message);
  }

  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match?.[1] || `conversation.${format === "json" ? "json" : "md"}`;

  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
};

const triggerBrowserDownload = (href, filename) => {
  const link = document.createElement("a");
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
};

const downloadImageFromSrc = async (src, filename = "generated-image.png") => {
  if (!src) {
    throw new Error("Missing image source");
  }

  if (src.startsWith("data:image/")) {
    triggerBrowserDownload(src, filename);
    return;
  }

  try {
    const response = await fetch(src, {
      method: "GET",
      credentials: "omit",
    });

    if (!response.ok) {
      throw new Error(`Image request failed (${response.status})`);
    }

    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);

    try {
      triggerBrowserDownload(objectUrl, filename);
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
    return;
  } catch (_error) {
    window.open(src, "_blank", "noopener,noreferrer");
  }
};

const copyTextToClipboard = async (text) => {
  const value = String(text ?? "");
  if (!value) {
    throw new Error("Nothing to copy");
  }

  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  document.body.appendChild(textarea);
  textarea.select();

  try {
    const ok = document.execCommand("copy");
    if (!ok) {
      throw new Error("Copy command failed");
    }
  } finally {
    textarea.remove();
  }
};

const attachCodeCopyButtons = (bubble) => {
  if (!bubble) return;

  const codeBlocks = Array.from(bubble.querySelectorAll("pre > code"));
  for (const code of codeBlocks) {
    const pre = code.parentElement;
    if (!pre) continue;
    if (pre.parentElement?.classList.contains("code-block-wrap")) continue;

    const wrap = document.createElement("div");
    wrap.className = "code-block-wrap";

    const actions = document.createElement("div");
    actions.className = "code-block-actions";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary code-copy-button";
    button.textContent = "Copy code";
    button.onclick = async () => {
      try {
        await copyTextToClipboard(code.textContent || "");
        setStatus("Code copied.", "", 1800);
      } catch (error) {
        setStatus(`Failed to copy code: ${error.message}`, "error");
      }
    };

    actions.appendChild(button);

    const parent = pre.parentNode;
    if (!parent) continue;

    parent.insertBefore(wrap, pre);
    wrap.appendChild(actions);
    wrap.appendChild(pre);
  }
};
const inferImageExtension = (src = "") => {
  if (src.startsWith("data:image/")) {
    const match = src.match(/^data:image\/([a-zA-Z0-9.+-]+);base64,/);
    const ext = (match?.[1] || "png").toLowerCase();
    return ext === "jpeg" ? "jpg" : ext;
  }

  try {
    const url = new URL(src, window.location.origin);
    const pathname = url.pathname || "";
    const match = pathname.match(/\.([a-zA-Z0-9]+)$/);
    if (match) {
      return match[1].toLowerCase();
    }
  } catch (_error) {
    // ignore and fall back
  }

  return "png";
};

const addImageDownloadActions = (wrap, bubble, message) => {
  if (!wrap || !bubble || !message || message.role !== "assistant") return;

  const images = Array.from(bubble.querySelectorAll("img"));
  if (!images.length) return;

  const actions = document.createElement("div");
  actions.className = "image-actions";

  images.forEach((img, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary image-action-button";
    button.textContent = images.length > 1 ? `Download image ${index + 1}` : "Download image";
    button.onclick = async () => {
      try {
        const extension = inferImageExtension(img.currentSrc || img.src);
        const filenameBase = message.id ? `conversation-image-${message.id}` : "generated-image";
        const filename = images.length > 1
          ? `${filenameBase}-${index + 1}.${extension}`
          : `${filenameBase}.${extension}`;

        await downloadImageFromSrc(img.currentSrc || img.src, filename);
        setStatus("Image download started.", "", 2000);
      } catch (error) {
        setStatus(`Failed to download image: ${error.message}`, "error");
      }
    };
    actions.appendChild(button);
  });

  wrap.appendChild(actions);
};

const setComposerBusy = (busy) => {
  state.isSending = busy;
  messageInput.disabled = busy;
  fileInput.disabled = busy;
  sendMessageBtn.disabled = busy;
  sendMessageBtn.textContent = busy
    ? (state.editingMessageId ? "Saving..." : "Sending...")
    : (state.editingMessageId ? "Save & resend" : "Send");
  updateConversationActionState();
};

const clearDraftAttachments = () => {
  fileInput.value = "";
};

const isImageCommandDraft = (value = "") =>
  String(value).trimStart().toLowerCase().startsWith("/image ");

const updateImageCommandHint = () => {
  if (!imageCommandHint) return;
  const visible = isImageCommandDraft(messageInput.value);
  imageCommandHint.hidden = !visible;
  imageCommandHint.setAttribute("aria-hidden", visible ? "false" : "true");
};

const startEditingMessage = (message) => {
  if (!message || message.role !== "user") return;

  state.editingMessageId = message.id;
  messageInput.value = message.content || "";
  clearDraftAttachments();
  setEditingState(message.id);
  updateImageCommandHint();
  messageInput.focus();
  messageInput.setSelectionRange(messageInput.value.length, messageInput.value.length);

  setStatus("Editing message. Everything after it will be replaced when you resend.");
};

const getMessageRowById = (messageId) => {
  const bubbles = Array.from(messageList.querySelectorAll(".bubble[data-id]"));
  const bubble = bubbles.find((node) => String(node.dataset.id) === String(messageId));
  return bubble?.closest(".message-row") || null;
};

const getTailRowsFromMessageId = (messageId) => {
  const rows = Array.from(messageList.querySelectorAll(".message-row"));
  return rows.filter((row) => {
    const bubble = row.querySelector(".bubble[data-id]");
    if (!bubble) return false;
    const rowMessageId = Number(bubble.dataset.id);
    return Number.isFinite(rowMessageId) && rowMessageId >= Number(messageId);
  });
};

const setRowsDimmed = (rows, dimmed) => {
  for (const row of rows) {
    row.style.opacity = dimmed ? "0.45" : "";
  }
};

const createStreamingAssistantRow = (content = "Thinking...") => {
  ensureFileSearchStatusStyles();

  const row = document.createElement("div");
  row.className = "message-row assistant";

  const bubble = createBubble({
    role: "assistant",
    content,
    extraClass: "pending",
    id: `regen-pending-${Date.now()}`,
  });

  row.appendChild(bubble);
  const activityTimeline = createActivityTimeline();
  row.appendChild(activityTimeline.container);

  return { row, bubble, activityTimeline };
};

const regenerateAssistantMessage = async (message) => {
  if (!message || message.role !== "assistant" || !message.id) return;
  if (!state.activeConversation || state.isSending) return;

  setComposerBusy(true);
  setEditingState(null);
  setStatus("Regenerating response...");

  const tailRows = getTailRowsFromMessageId(message.id);
  setRowsDimmed(tailRows, true);

  const {
    row: streamingRow,
    bubble: streamingBubble,
    activityTimeline,
  } = createStreamingAssistantRow("Thinking...");
  const anchorRow = tailRows.length ? tailRows[tailRows.length - 1] : getMessageRowById(message.id);

  if (anchorRow?.parentNode) {
    anchorRow.parentNode.insertBefore(streamingRow, anchorRow.nextSibling);
  } else {
    messageList.appendChild(streamingRow);
  }

  scheduleMessageBottomSnap();

  try {
    let streamedText = "";
    const controller = new AbortController();
    beginActiveStream(controller, "regenerate");

    const result = await new Promise((resolve, reject) => {
      apiStream("/api/regenerate-stream", {
        conversation_id: state.activeConversation,
        message_id: message.id,
        enable_web_search: !!enableWebSearchInput?.checked,
        enable_code_interpreter: !!enableCodeInterpreterInput?.checked,
        ...getSelectedChatOptions(),
      }, {
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === "delta") {
            streamedText += event.delta || "";
            streamingBubble.innerHTML = renderMarkdown(streamedText || "Thinking...");
            return;
          }

          if (event.type === "status") {
            const statusText = String(event.status || "").replaceAll("_", " ");
            if (statusText) {
              setStatus(`Tool progress: ${statusText}...`, "", 2500);
            }
            return;
          }

          if (event.type === "activity") {
            appendActivityEntry(activityTimeline, event.activity);
            const summary = String(event.activity?.summary || "").trim();
            if (summary) {
              setStatus(summary, "", 2500);
            }
            return;
          }

          if (event.type === "error") {
            reject(new Error(event.error || "Streaming request failed"));
            return;
          }

          if (event.type === "done") {
            resolve(event);
          }
        },
      }).catch(reject);
    });

    streamingBubble.innerHTML = renderMarkdown(
      result.assistant_message?.content || streamedText || "(No response)",
    );
    streamingBubble.classList.remove("pending");

    await loadConversations({ refreshMessages: false });
    await loadMessages(state.activeConversation);
    setStatus("Response regenerated.", "", 2200);
  } catch (error) {
    if (error?.name === "AbortError") {
      streamingRow.remove();
      setRowsDimmed(tailRows, false);
      setStatus("Response canceled.", "", 1800);
      return;
    }

    streamingRow.remove();
    setRowsDimmed(tailRows, false);
    setStatus(`Failed to regenerate response: ${error.message}`, "error");
  } finally {
    clearActiveStream();
    setComposerBusy(false);
    messageInput.focus();
  }
};

const cancelEditingMessage = () => {
  setEditingState(null);
  messageInput.value = "";
  clearDraftAttachments();
  updateImageCommandHint();

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
  let codeFence = "";
  let inList = false;

  const closeListIfOpen = () => {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
  };

  const renderInline = (text) => {
    let line = escapeHtml(text);
    line = line.replace(
      /!\[([^\]]*?)\]\((https?:\/\/[^\s)]+|data:image\/[a-zA-Z0-9.+-]+;base64,[a-zA-Z0-9+/=]+)\)/g,
      '<img src="$2" alt="$1" loading="lazy" />',
    );
    line = line.replace(/`([^`]+?)`/g, "<code>$1</code>");
    line = line.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
    line = line.replace(/\*([^*]+?)\*/g, "<em>$1</em>");
    line = line.replace(
      /\[([^\]]+?)\]\((https?:\/\/[^\s)]+|\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
    return line;
  };

  for (const rawLine of lines) {
    const line = rawLine ?? "";

    const fenceMatch = line.match(/^\s{0,3}(```+|~~~+)\s*([^`]*)$/);
    if (fenceMatch) {
      closeListIfOpen();
      if (!inCodeBlock) {
        inCodeBlock = true;
        codeFence = fenceMatch[1];
        codeLang = (fenceMatch[2] || "").trim();
        const classAttr = codeLang ? ` class="lang-${escapeHtml(codeLang)}"` : "";
        html.push(`<pre><code${classAttr}>`);
      } else if (fenceMatch[1][0] === codeFence[0] && fenceMatch[1].length >= codeFence.length) {
        inCodeBlock = false;
        codeFence = "";
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
    codeFence = "";
    html.push("</code></pre>");
  }

  return html.join("");
};

const ensureFileSearchStatusStyles = () => {
  if (document.getElementById("fileSearchStatusStyles")) return;

  const style = document.createElement("style");
  style.id = "fileSearchStatusStyles";
  style.textContent = `
    .message-meta-row {
      display: flex;
      justify-content: flex-start;
      margin-top: 6px;
      gap: 8px;
      flex-wrap: wrap;
    }

    .file-search-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 9px;
      border-radius: 999px;
      font-size: 0.78rem;
      line-height: 1.2;
      border: 1px solid rgba(255, 255, 255, 0.08);
      background: rgba(255, 255, 255, 0.04);
      color: rgba(255, 255, 255, 0.82);
      max-width: min(100%, 34rem);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .file-search-chip::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 999px;
      flex: 0 0 auto;
      background: #7d8792;
    }

    .file-search-chip.completed::before {
      background: #58c472;
    }

    .file-search-chip.processing::before {
      background: #f0b54a;
    }

    .file-search-chip.failed::before {
      background: #ef6b6b;
    }

    .file-search-chip.partial::before {
      background: #6fb4ff;
    }

    .used-tools-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .used-tool-chip {
      display: inline-flex;
      align-items: center;
      padding: 4px 9px;
      border-radius: 999px;
      font-size: 0.78rem;
      line-height: 1.2;
      border: 1px solid rgba(120, 196, 255, 0.18);
      background: rgba(67, 123, 204, 0.14);
      color: rgba(222, 236, 255, 0.9);
      white-space: nowrap;
    }

    .assistant-model-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .assistant-model-chip {
      display: inline-flex;
      align-items: center;
      padding: 4px 9px;
      border-radius: 999px;
      font-size: 0.78rem;
      line-height: 1.2;
      border: 1px solid rgba(255, 255, 255, 0.1);
      background: rgba(255, 255, 255, 0.06);
      color: rgba(255, 255, 255, 0.86);
      white-space: nowrap;
    }

    .activity-timeline {
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-width: min(78ch, 84%);
      margin-top: 2px;
    }

    .activity-timeline[hidden] {
      display: none;
    }

    .activity-timeline-title {
      font-size: 0.76rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: rgba(255, 255, 255, 0.54);
      padding-left: 2px;
    }

    .activity-timeline-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .activity-entry {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 7px 10px;
      border-radius: 12px;
      font-size: 0.84rem;
      line-height: 1.35;
      border: 1px solid rgba(255, 255, 255, 0.08);
      background: rgba(255, 255, 255, 0.04);
      color: rgba(255, 255, 255, 0.84);
    }

    .activity-entry::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 999px;
      flex: 0 0 auto;
      background: #7d8792;
    }

    .activity-entry.running::before {
      background: #f0b54a;
    }

    .activity-entry.completed::before {
      background: #58c472;
    }

    .activity-entry.failed::before {
      background: #ef6b6b;
    }

    .activity-entry-tool {
      font-weight: 600;
      color: rgba(225, 235, 255, 0.94);
    }

    .activity-entry-summary {
      color: rgba(255, 255, 255, 0.8);
    }
  `;
  document.head.appendChild(style);
};

const buildFileSearchStatusText = (status) => {
  if (!status || typeof status !== "object") return "";

  const label = String(status.label || "").trim();
  if (!label) return "";

  const parts = [label];
  const total = Number(status.total || 0);
  const completed = Number(status.completed || 0);
  const failed = Number(status.failed || 0);
  const processing = Number(status.processing || 0);
  const missing = Number(status.missing || 0);

  if (total > 1) {
    parts.push(`${completed}/${total}`);
  }

  if (processing > 0) {
    parts.push(`${processing} pending`);
  } else if (failed > 0 && completed > 0) {
    parts.push(`${failed} failed`);
  } else if (failed > 0 && !completed) {
    parts.push(`${failed} failed`);
  } else if (missing > 0) {
    parts.push(`${missing} missing`);
  }

  return parts.join(" · ");
};

const getToolDisplayLabel = (toolKey) => {
  const labelMap = {
    web: "Web",
    github: "GitHub",
    files: "File Search",
    python: "Python",
  };
  const key = String(toolKey || "").trim().toLowerCase();
  return labelMap[key] || key.replaceAll("_", " ").trim() || "Tool";
};

const normalizeToolsUsed = (toolsUsed) => {
  if (!Array.isArray(toolsUsed)) return [];

  const normalized = [];
  const seen = new Set();
  for (const rawTool of toolsUsed) {
    const key = String(rawTool || "").trim().toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    normalized.push({ key, label: getToolDisplayLabel(key) });
  }

  return normalized;
};

const normalizeAssistantModelInfo = (message) => {
  if (!message || message.role !== "assistant") return [];

  const chips = [];
  const actualModel = String(message.model || "").trim();
  const requestedModel = String(message.requested_model || "").trim();
  const reasoningEnabled = !!message.reasoning_enabled;
  const reasoningEffort = String(message.reasoning_effort || "").trim();

  if (actualModel) {
    chips.push({
      label: actualModel,
      title: requestedModel && requestedModel !== actualModel
        ? `Requested ${requestedModel}; used ${actualModel}`
        : `Used ${actualModel}`,
    });
  }

  if (reasoningEnabled) {
    const label = reasoningEffort ? `Reasoning: ${reasoningEffort}` : "Reasoning";
    chips.push({
      label,
      title: reasoningEffort
        ? `Reasoning mode enabled (${reasoningEffort})`
        : "Reasoning mode enabled",
    });
  }

  return chips;
};

const normalizeActivityLog = (activityLog) => {
  if (!Array.isArray(activityLog)) return [];

  const normalized = [];
  for (const rawEntry of activityLog) {
    if (!rawEntry || typeof rawEntry !== "object") continue;

    const tool = String(rawEntry.tool || "").trim().toLowerCase();
    const label = String(rawEntry.label || "").trim() || getToolDisplayLabel(tool);
    const state = String(rawEntry.state || "running").trim().toLowerCase() || "running";
    const summary = String(rawEntry.summary || "").trim();

    normalized.push({
      tool,
      label,
      state: ["running", "completed", "failed"].includes(state) ? state : "running",
      summary,
    });
  }

  return normalized;
};

const createActivityTimeline = () => {
  ensureFileSearchStatusStyles();

  const container = document.createElement("div");
  container.className = "activity-timeline";
  container.hidden = true;

  const title = document.createElement("div");
  title.className = "activity-timeline-title";
  title.textContent = "Activity";

  const list = document.createElement("div");
  list.className = "activity-timeline-list";

  container.appendChild(title);
  container.appendChild(list);
  return { container, list };
};

const appendActivityEntry = (timeline, activity) => {
  if (!timeline?.container || !timeline?.list || !activity || typeof activity !== "object") {
    return;
  }

  const tool = String(activity.tool || "").trim().toLowerCase();
  const state = String(activity.state || "running").trim().toLowerCase() || "running";
  const summary = String(activity.summary || "").trim();
  if (!tool && !summary) return;

  timeline.container.hidden = false;

  const entry = document.createElement("div");
  entry.className = `activity-entry ${state}`;

  const toolLabel = document.createElement("span");
  toolLabel.className = "activity-entry-tool";
  toolLabel.textContent = getToolDisplayLabel(tool || activity.label || "");

  const summaryLabel = document.createElement("span");
  summaryLabel.className = "activity-entry-summary";
  summaryLabel.textContent = summary || `${toolLabel.textContent} step`;

  entry.appendChild(toolLabel);
  entry.appendChild(summaryLabel);
  timeline.list.appendChild(entry);
  scheduleMessageBottomSnap();
};

const populateActivityTimeline = (timeline, activityLog = []) => {
  const normalizedLog = normalizeActivityLog(activityLog);
  if (!normalizedLog.length) return;

  for (const entry of normalizedLog) {
    appendActivityEntry(timeline, entry);
  }
};

const createBubble = ({ role, content, extraClass = "", id = "" }) => {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}${extraClass ? ` ${extraClass}` : ""}`;

  if (id) {
    bubble.dataset.id = id;
  }

  bubble.innerHTML = renderMarkdown(content);
  attachCodeCopyButtons(bubble);
  return bubble;
};

const createMessageElement = (message) => {
  ensureFileSearchStatusStyles();

  const wrap = document.createElement("div");
  wrap.className = `message-row ${message.role}`;

  const bubble = createBubble({
    role: message.role,
    content: message.content,
    id: message.id ? String(message.id) : "",
  });

  wrap.appendChild(bubble);
  addImageDownloadActions(wrap, bubble, message);

  const actions = document.createElement("div");
  actions.className = "message-actions";

  const copyButton = document.createElement("button");
  copyButton.type = "button";
  copyButton.className = "secondary message-action-button";
  copyButton.textContent = "Copy";
  copyButton.onclick = async () => {
    try {
      await copyTextToClipboard(message.content || "");
      setStatus("Message copied.", "", 1800);
    } catch (error) {
      setStatus(`Failed to copy message: ${error.message}`, "error");
    }
  };
  actions.appendChild(copyButton);

  if (message.role === "user" && message.id) {
    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "secondary message-action-button";
    editButton.textContent = "Edit";
    editButton.onclick = () => startEditingMessage(message);
    actions.appendChild(editButton);
  }

  if (message.role === "assistant" && message.id) {
    const regenerateButton = document.createElement("button");
    regenerateButton.type = "button";
    regenerateButton.className = "secondary message-action-button";
    regenerateButton.textContent = "Regenerate";
    regenerateButton.onclick = () => {
      void regenerateAssistantMessage(message);
    };
    actions.appendChild(regenerateButton);
  }

  if (actions.childElementCount > 0) {
    wrap.appendChild(actions);
  }

  if (message.role === "assistant") {
    const activityLog = normalizeActivityLog(message.activity_log);
    if (activityLog.length) {
      const persistedActivityTimeline = createActivityTimeline();
      populateActivityTimeline(persistedActivityTimeline, activityLog);
      wrap.appendChild(persistedActivityTimeline.container);
    }
  }

  if (message.role === "user" && message.file_search_status) {
    const chipText = buildFileSearchStatusText(message.file_search_status);
    if (chipText) {
      const metaRow = document.createElement("div");
      metaRow.className = "message-meta-row";

      const chip = document.createElement("div");
      const statusState = String(message.file_search_status.state || "processing").trim() || "processing";
      chip.className = `file-search-chip ${statusState}`;
      chip.textContent = chipText;

      const filenames = Array.isArray(message.file_search_status.filenames)
        ? message.file_search_status.filenames.filter(Boolean).join(", ")
        : "";
      const errorText = String(message.file_search_status.error || "").trim();
      const titleParts = [];
      if (filenames) titleParts.push(filenames);
      if (errorText) titleParts.push(errorText);
      if (titleParts.length) {
        chip.title = titleParts.join("\n");
      }

      metaRow.appendChild(chip);
      wrap.appendChild(metaRow);
    }
  }

  if (message.role === "assistant") {
    const modelChips = normalizeAssistantModelInfo(message);
    if (modelChips.length) {
      const metaRow = document.createElement("div");
      metaRow.className = "message-meta-row assistant-model-row";

      for (const modelChip of modelChips) {
        const chip = document.createElement("div");
        chip.className = "assistant-model-chip";
        chip.textContent = modelChip.label;
        if (modelChip.title) {
          chip.title = modelChip.title;
        }
        metaRow.appendChild(chip);
      }

      wrap.appendChild(metaRow);
    }

    const toolsUsed = normalizeToolsUsed(message.tools_used);
    if (toolsUsed.length) {
      const metaRow = document.createElement("div");
      metaRow.className = "message-meta-row used-tools-row";

      for (const tool of toolsUsed) {
        const chip = document.createElement("div");
        chip.className = "used-tool-chip";
        chip.textContent = tool.label;
        chip.title = `Used ${tool.label}`;
        metaRow.appendChild(chip);
      }

      wrap.appendChild(metaRow);
    }
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

  applyChatSearchHighlights();

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
    remove.textContent = "×";
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
  const titleCase = (value = "") =>
    String(value)
      .replaceAll("_", " ")
      .trim()
      .replace(/\b\w/g, (match) => match.toUpperCase());

  for (const memory of state.memories) {
    const card = document.createElement("div");
    card.className = "memory-item";

    const text = document.createElement("div");
    text.textContent = memory.content;

    const tags = document.createElement("div");
    tags.className = "memory-tags";

    const tagValues = [
      titleCase(memory.kind || "note"),
      memory.scope === "conversation" ? "Current chat" : "Global",
    ];
    if (memory.pinned) {
      tagValues.push("Pinned");
    }
    if (memory.source && memory.source !== "user") {
      tagValues.push(titleCase(memory.source));
    }

    for (const tagValue of tagValues) {
      const chip = document.createElement("span");
      chip.className = "memory-tag";
      chip.textContent = tagValue;
      tags.appendChild(chip);
    }

    const meta = document.createElement("div");
    meta.className = "muted";
    meta.textContent = `#${memory.id} · ${memory.created_at}`;
    const confidence = Number(memory.confidence);
    const confidenceText = Number.isFinite(confidence) ? ` · confidence ${confidence.toFixed(2)}` : "";
    meta.textContent = `#${memory.id} · ${memory.created_at}${confidenceText}`;

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
    card.appendChild(tags);
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
  return active === conversationSearchInput || active === memoryInput || active === chatSearchInput;
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

const loadMessages = async (conversationId, options = {}) => {
  const { autoSnap = true, quietFileSearchRefresh = false } = options;
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

  renderMessages(data.messages, autoSnap);
  scheduleFileSearchStatusPoll(conversationId, data.messages);

  if (!quietFileSearchRefresh) {
    const hasProcessingStatus = Array.isArray(data.messages) && data.messages.some((message) => {
      if (!message || message.role !== "user" || !message.file_search_status) return false;
      return String(message.file_search_status.state || "").trim() === "processing";
    });
    if (hasProcessingStatus) {
      setStatus("File search indexing is still running...", "", 1800);
    }
  }
};

const renderMemorySuggestions = () => {
  if (!memorySuggestionsList) return;
  memorySuggestionsList.innerHTML = "";

  const titleCase = (value = "") =>
    String(value)
      .replaceAll("_", " ")
      .trim()
      .replace(/\b\w/g, (match) => match.toUpperCase());

  const suggestions = Array.isArray(state.memorySuggestions) ? state.memorySuggestions : [];
  if (!suggestions.length) {
    memorySuggestionsList.hidden = true;
    return;
  }

  memorySuggestionsList.hidden = false;

  const heading = document.createElement("div");
  heading.className = "muted";
  heading.textContent = "Suggested memories";
  memorySuggestionsList.appendChild(heading);

  for (const suggestion of suggestions) {
    const card = document.createElement("div");
    card.className = "memory-item suggestion-item";

    const isEditing = state.editingSuggestionId === suggestion.id;

    if (isEditing) {
      const draft = state.editingSuggestionDraft || {
        content: suggestion.content,
        kind: suggestion.kind || "note",
        scope: suggestion.scope || "global",
        pinned: !!suggestion.pinned,
      };

      const editor = document.createElement("div");
      editor.className = "suggestion-editor";

      const editorInput = document.createElement("textarea");
      editorInput.className = "suggestion-editor-input";
      editorInput.value = draft.content || "";
      editorInput.rows = 3;
      editorInput.placeholder = "Edit suggested memory...";
      editorInput.oninput = () => {
        state.editingSuggestionDraft = {
          ...draft,
          content: editorInput.value,
          kind: editorKind.value,
          scope: editorScope.value,
          pinned: !!editorPinned.checked,
        };
      };

      const editorControls = document.createElement("div");
      editorControls.className = "memory-controls suggestion-editor-controls";

      const editorKind = document.createElement("select");
      editorKind.innerHTML = `
        <option value="note">General note</option>
        <option value="preference">Preference</option>
        <option value="project">Project fact</option>
        <option value="task">Task / goal</option>
        <option value="fact">Fact</option>
        <option value="identity">Identity</option>
      `;
      editorKind.value = draft.kind || "note";

      const editorScope = document.createElement("select");
      editorScope.innerHTML = `
        <option value="global">Global</option>
        <option value="conversation">Current chat</option>
      `;
      editorScope.value = draft.scope || "global";

      const editorPinnedLabel = document.createElement("label");
      editorPinnedLabel.className = "search-toggle memory-pin-toggle";
      const editorPinned = document.createElement("input");
      editorPinned.type = "checkbox";
      editorPinned.checked = !!draft.pinned;
      editorPinnedLabel.appendChild(editorPinned);
      editorPinnedLabel.append(" Pin");

      const syncDraft = () => {
        state.editingSuggestionDraft = {
          content: editorInput.value,
          kind: editorKind.value,
          scope: editorScope.value,
          pinned: !!editorPinned.checked,
        };
      };

      editorKind.onchange = syncDraft;
      editorScope.onchange = syncDraft;
      editorPinned.onchange = syncDraft;

      editorControls.appendChild(editorKind);
      editorControls.appendChild(editorScope);
      editorControls.appendChild(editorPinnedLabel);

      const meta = document.createElement("div");
      meta.className = "muted";
      const confidence = Number(suggestion.confidence);
      const confidenceText = Number.isFinite(confidence) ? ` · confidence ${confidence.toFixed(2)}` : "";
      meta.textContent = `#${suggestion.id} · ${suggestion.created_at}${confidenceText}`;

      const actions = document.createElement("div");
      actions.className = "memory-actions";

      const save = document.createElement("button");
      save.textContent = "Save";
      save.type = "button";
      save.className = "primary";
      save.onclick = async () => {
        try {
          syncDraft();
          const nextDraft = { ...(state.editingSuggestionDraft || draft) };
          await updateMemorySuggestion(suggestion.id, nextDraft);
          state.editingSuggestionId = null;
          state.editingSuggestionDraft = null;
          await loadMemories();
          setStatus("Memory suggestion updated.", "", 2000);
        } catch (error) {
          setStatus(`Failed to update suggestion: ${error.message}`, "error");
        }
      };

      const cancel = document.createElement("button");
      cancel.textContent = "Cancel";
      cancel.type = "button";
      cancel.onclick = () => {
        state.editingSuggestionId = null;
        state.editingSuggestionDraft = null;
        renderMemorySuggestions();
      };

      actions.appendChild(save);
      actions.appendChild(cancel);
      editor.appendChild(editorInput);
      card.appendChild(editor);
      card.appendChild(editorControls);
      card.appendChild(meta);
      card.appendChild(actions);
    } else {
      const text = document.createElement("div");
      text.textContent = suggestion.content;

      const tags = document.createElement("div");
      tags.className = "memory-tags";
      const tagValues = [
        titleCase(suggestion.kind || "note"),
        suggestion.scope === "conversation" ? "Current chat" : "Global",
        suggestion.pinned ? "Pinned" : "",
        "Suggested",
      ].filter(Boolean);

      for (const tagValue of tagValues) {
        const chip = document.createElement("span");
        chip.className = "memory-tag";
        chip.textContent = tagValue;
        tags.appendChild(chip);
      }

      const meta = document.createElement("div");
      meta.className = "muted";
      const confidence = Number(suggestion.confidence);
      const confidenceText = Number.isFinite(confidence) ? ` · confidence ${confidence.toFixed(2)}` : "";
      meta.textContent = `#${suggestion.id} · ${suggestion.created_at}${confidenceText}`;

      const actions = document.createElement("div");
      actions.className = "memory-actions";

      const edit = document.createElement("button");
      edit.textContent = "Edit";
      edit.type = "button";
      edit.onclick = () => {
        state.editingSuggestionId = suggestion.id;
        state.editingSuggestionDraft = {
          content: suggestion.content,
          kind: suggestion.kind || "note",
          scope: suggestion.scope || "global",
          pinned: !!suggestion.pinned,
        };
        renderMemorySuggestions();
      };

      const accept = document.createElement("button");
      accept.textContent = "Accept";
      accept.type = "button";
      accept.className = "primary";
      accept.onclick = async () => {
        try {
          await acceptMemorySuggestion(suggestion.id);
        } catch (error) {
          setStatus(`Failed to accept suggestion: ${error.message}`, "error");
        }
      };

      const reject = document.createElement("button");
      reject.textContent = "Reject";
      reject.type = "button";
      reject.onclick = async () => {
        try {
          await rejectMemorySuggestion(suggestion.id);
        } catch (error) {
          setStatus(`Failed to reject suggestion: ${error.message}`, "error");
        }
      };

      actions.appendChild(edit);
      actions.appendChild(accept);
      actions.appendChild(reject);
      card.appendChild(text);
      card.appendChild(tags);
      card.appendChild(meta);
      card.appendChild(actions);
    }

    memorySuggestionsList.appendChild(card);
  }
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
  updateConversationActionState();

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
  state.memorySuggestions = data.suggestions || [];
  if (
    state.editingSuggestionId &&
    !state.memorySuggestions.some((suggestion) => suggestion.id === state.editingSuggestionId)
  ) {
    state.editingSuggestionId = null;
    state.editingSuggestionDraft = null;
  }
  renderMemorySuggestions();
  renderMemories();
};

const loadSettings = async () => {
  const data = await api("/api/settings");
  state.settings = data || null;
  populateChatSettingsControls();
};

const selectConversation = async (conversationId) => {
  if (state.isSending) return;

  clearFileSearchStatusPoll();
  state.activeConversation = conversationId;
  setEditingState(null);
  clearDraftAttachments();
  clearChatSearch();
  renderConversations();
  updateConversationActionState();
  await loadMessages(conversationId);
  closeMobileHistory();
};

const createConversation = async () => {
  if (state.isSending) return;

  clearFileSearchStatusPoll();
  const data = await api("/api/conversations", { method: "POST" });
  setEditingState(null);
  updateConversationActionState();
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

const TEXT_ATTACHMENT_EXTENSIONS = new Set([
  ".txt",
  ".md",
  ".markdown",
  ".csv",
  ".tsv",
  ".json",
  ".jsonl",
  ".py",
  ".js",
  ".ts",
  ".tsx",
  ".jsx",
  ".html",
  ".htm",
  ".css",
  ".xml",
  ".yaml",
  ".yml",
  ".log",
  ".toml",
  ".ini",
  ".cfg",
  ".sql",
]);

const getFileExtension = (filename = "") => {
  const lastDot = filename.lastIndexOf(".");
  return lastDot >= 0 ? filename.slice(lastDot).toLowerCase() : "";
};

const readFileAsDataURL = (file, label) =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error(`Failed to read ${label}: ${file.name}`));
    reader.readAsDataURL(file);
  });

const isTextLikeAttachment = (file) => {
  const extension = getFileExtension(file.name);
  if (TEXT_ATTACHMENT_EXTENSIONS.has(extension)) return true;

  const mimeType = String(file.type || "").toLowerCase();
  return (
    mimeType.startsWith("text/") ||
    mimeType === "application/json" ||
    mimeType === "application/xml"
  );
};

const readAttachments = async (files) => {
  const attachments = [];

  for (const file of files) {
    if (file.type.startsWith("image/")) {
      const dataUrl = await readFileAsDataURL(file, "image");

      attachments.push({
        kind: "image",
        name: file.name,
        data_url: dataUrl,
      });
      continue;
    }

    if (isTextLikeAttachment(file)) {
      const text = await file.text();
      attachments.push({
        kind: "text",
        name: file.name,
        text,
      });
      continue;
    }

    if (getFileExtension(file.name) === ".pdf" || file.type === "application/pdf") {
      const dataUrl = await readFileAsDataURL(file, "file");
      attachments.push({
        kind: "file",
        name: file.name,
        data_url: dataUrl,
      });
      continue;
    }

    throw new Error(`Unsupported attachment type: ${file.name}. Use images, text-like files, or PDFs.`);
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
  let pendingActivityTimeline = null;
  let streamedText = "";

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

    const pendingAssistant = createStreamingAssistantRow("Thinking...");
    pendingBubble = pendingAssistant.bubble;
    pendingActivityTimeline = pendingAssistant.activityTimeline;
    messageList.appendChild(pendingAssistant.row);
    scheduleMessageBottomSnap();

    const requestPath = isEditing ? "/api/edit" : "/api/send";
    const requestBody = {
      conversation_id: conversationId,
      content,
      attachments,
      enable_web_search: !!enableWebSearchInput?.checked,
      enable_code_interpreter: !!enableCodeInterpreterInput?.checked,
      ...getSelectedChatOptions(),
    };

    if (isEditing) {
      requestBody.message_id = state.editingMessageId;
    }

    messageInput.value = "";
    fileInput.value = "";

    let result;
    if (isEditing) {
      result = await api(requestPath, {
        method: "POST",
        body: JSON.stringify(requestBody),
      });
    } else {
      const controller = new AbortController();
      beginActiveStream(controller, "send");

      result = await new Promise((resolve, reject) => {
        apiStream("/api/send-stream", requestBody, {
          signal: controller.signal,
          onEvent: (event) => {
            if (event.type === "delta") {
              streamedText += event.delta || "";
              if (pendingBubble) {
                pendingBubble.innerHTML = renderMarkdown(streamedText);
              }
              return;
            }
            if (event.type === "status") {
              const statusText = String(event.status || "").replaceAll("_", " ");
              if (statusText) {
                setStatus(`Tool progress: ${statusText}...`, "", 2500);
              }
              return;
            }
            if (event.type === "activity") {
              appendActivityEntry(pendingActivityTimeline, event.activity);
              const summary = String(event.activity?.summary || "").trim();
              if (summary) {
                setStatus(summary, "", 2500);
              }
              return;
            }
            if (event.type === "error") {
              reject(new Error(event.error || "Streaming request failed"));
              return;
            }
            if (event.type === "done") {
              resolve(event);
            }
          },
        }).catch(reject);
      });
    }

    if (pendingBubble) {
      pendingBubble.innerHTML = renderMarkdown(
        result.assistant_message?.content || streamedText || "(No response)",
      );
      pendingBubble.classList.remove("pending");
      pendingBubble.classList.remove("error");
    }

    setEditingState(null);
    await loadConversations({ refreshMessages: false });
    await loadMessages(conversationId);
    await loadMemories();
    setStatus(isEditing ? "Message updated and response regenerated." : "", "", isEditing ? 2500 : 0);
  } catch (error) {
    if (error?.name === "AbortError") {
      if (pendingBubble) {
        pendingBubble.innerHTML = renderMarkdown(streamedText || "Canceled.");
        pendingBubble.classList.remove("pending");
      }
      setStatus("Response canceled.", "", 1800);
      return;
    }

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
    clearActiveStream();
    setComposerBusy(false);
    messageInput.focus();
  }
};

const addMemory = async () => {
  const content = memoryInput.value.trim();
  if (!content) return;

  try {
    const scope = String(memoryScopeInput?.value || "global").trim().toLowerCase() || "global";
    if (scope === "conversation" && !state.activeConversation) {
      throw new Error("Open a conversation before saving a current-chat memory.");
    }
    await api("/api/memories", {
      method: "POST",
      body: JSON.stringify({
        content,
        kind: memoryKindInput?.value || "note",
        scope,
        pinned: !!memoryPinnedInput?.checked,
        conversation_id: scope === "conversation" ? state.activeConversation : "",
      }),
    });
    memoryInput.value = "";
    if (memoryKindInput) memoryKindInput.value = "note";
    if (memoryScopeInput) memoryScopeInput.value = "global";
    if (memoryPinnedInput) memoryPinnedInput.checked = false;
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

const acceptMemorySuggestion = async (id) => {
  await api(`/api/memory-suggestions/${id}/accept`, { method: "POST" });
  if (state.editingSuggestionId === id) {
    state.editingSuggestionId = null;
    state.editingSuggestionDraft = null;
  }
  await loadMemories();
  setStatus("Memory suggestion accepted.", "", 2000);
};

const rejectMemorySuggestion = async (id) => {
  await api(`/api/memory-suggestions/${id}`, { method: "DELETE" });
  if (state.editingSuggestionId === id) {
    state.editingSuggestionId = null;
    state.editingSuggestionDraft = null;
  }
  await loadMemories();
  setStatus("Memory suggestion rejected.", "", 2000);
};

const updateMemorySuggestion = async (id, draft) => {
  await api(`/api/memory-suggestions/${id}/update`, {
    method: "POST",
    body: JSON.stringify({
      content: String(draft?.content || "").trim(),
      kind: draft?.kind || "note",
      scope: draft?.scope || "global",
      pinned: !!draft?.pinned,
      conversation_id: draft?.scope === "conversation" ? (state.activeConversation || "") : "",
    }),
  });
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
    clearFileSearchStatusPoll();
    state.activeConversation = null;
    conversationTitle.textContent = "Chat";
    renderMessages([], false);
  }
  updateConversationActionState();

  await loadConversations();
  setStatus("Conversation deleted.", "", 2000);
};

const initializeApp = async () => {
  clearFileSearchStatusPoll();
  ensureChatSearchUI();

  try {
    await loadSettings();
  } catch (error) {
    console.error("Failed to load settings:", error);
    setStatus(`Failed to load settings: ${error.message}`, "error");
  }

  try {
    await loadMemories();
  } catch (error) {
    console.error("Failed to load memories:", error);
    setStatus(`Failed to load memories: ${error.message}`, "error");
  }

  try {
    await loadConversations();
    updateConversationActionState();
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
if (cancelResponseBtn) {
  cancelResponseBtn.onclick = () => {
    const canceled = cancelActiveStream();
    if (canceled) {
      setStatus("Response canceled.", "", 1800);
    }
  };
}

if (modelSelect) {
  modelSelect.addEventListener("change", () => {
    window.localStorage.setItem(MODEL_STORAGE_KEY, modelSelect.value || "");
  });
}

if (enableReasoningInput) {
  enableReasoningInput.addEventListener("change", () => {
    window.localStorage.setItem(REASONING_ENABLED_STORAGE_KEY, enableReasoningInput.checked ? "1" : "0");
    updateReasoningControls();
  });
}

if (reasoningEffortSelect) {
  reasoningEffortSelect.addEventListener("change", () => {
    window.localStorage.setItem(REASONING_EFFORT_STORAGE_KEY, reasoningEffortSelect.value || "medium");
  });
}

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

if (exportConversationMarkdownBtn) {
  exportConversationMarkdownBtn.onclick = async () => {
    try {
      await downloadConversationExport("md");
      setStatus("Conversation exported as Markdown.", "", 2000);
    } catch (error) {
      setStatus(`Failed to export Markdown: ${error.message}`, "error");
    }
  };
}

if (exportConversationJsonBtn) {
  exportConversationJsonBtn.onclick = async () => {
    try {
      await downloadConversationExport("json");
      setStatus("Conversation exported as JSON.", "", 2000);
    } catch (error) {
      setStatus(`Failed to export JSON: ${error.message}`, "error");
    }
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

messageInput.addEventListener("input", () => {
  updateImageCommandHint();
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

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!state.activeStreamController) return;

  const canceled = cancelActiveStream();
  if (canceled) {
    event.preventDefault();
    setStatus("Response canceled.", "", 1800);
  }
});

document.addEventListener("keydown", (event) => {
  const isFindShortcut =
    (event.ctrlKey || event.metaKey) &&
    !event.shiftKey &&
    !event.altKey &&
    event.key.toLowerCase() === "f";

  if (!isFindShortcut) return;

  const activeEl = document.activeElement;
  const tagName = activeEl?.tagName?.toLowerCase();
  const isTypingInEditableField =
    tagName === "input" ||
    tagName === "textarea" ||
    activeEl?.isContentEditable;

  if (isTypingInEditableField && activeEl !== chatSearchInput) {
    return;
  }

  if (!state.activeConversation) return;

  ensureChatSearchUI();

  if (!chatSearchInput) return;

  event.preventDefault();
  chatSearchInput.focus();
  chatSearchInput.select();
});

window.addEventListener("resize", () => {
  ensureChatSearchUI();
});

initializeApp();
updateConversationActionState();
updateCancelResponseButton();
updateImageCommandHint();
window.addEventListener("load", scheduleMessageBottomSnap);

/* --- Mobile drawer polish: memories drawer + backdrop + mutual exclusion --- */
(() => {
  const memoryToggleBtnMobileUi = document.getElementById("memoryToggle");
  const closeMemoriesBtnMobileUi = document.getElementById("closeMemories");
  const backdropMobileUi = document.getElementById("mobileDrawerBackdrop");
  const memoryPanelMobileUi = document.getElementById("memoryPanel");

  if (!memoryToggleBtnMobileUi || !closeMemoriesBtnMobileUi || !backdropMobileUi || !memoryPanelMobileUi) {
    return;
  }

  const mobileDrawerMediaMobileUi = window.matchMedia("(max-width: 1100px) and (orientation: portrait)");

  const isMobileDrawerModeMobileUi = () => mobileDrawerMediaMobileUi.matches;

  const syncMobileDrawersMobileUi = () => {
    const historyOpen = document.body.classList.contains("mobile-history-open") && isMobileDrawerModeMobileUi();
    const memoriesOpen = document.body.classList.contains("mobile-memories-open") && isMobileDrawerModeMobileUi();
    const anyOpen = historyOpen || memoriesOpen;

    backdropMobileUi.hidden = !anyOpen;

    if (historyToggleBtn) {
      historyToggleBtn.setAttribute("aria-expanded", historyOpen ? "true" : "false");
    }

    memoryToggleBtnMobileUi.setAttribute("aria-expanded", memoriesOpen ? "true" : "false");
  };

  const closeAllMobileDrawersMobileUi = () => {
    document.body.classList.remove("mobile-history-open", "mobile-memories-open");
    syncMobileDrawersMobileUi();
  };

  const setMobileMemoriesOpenMobileUi = (open) => {
    const shouldOpen = open && isMobileDrawerModeMobileUi();
    document.body.classList.toggle("mobile-memories-open", shouldOpen);

    if (shouldOpen) {
      document.body.classList.remove("mobile-history-open");
    }

    syncMobileDrawersMobileUi();
  };

  const toggleMobileMemoriesMobileUi = () => {
    if (!isMobileDrawerModeMobileUi()) return;
    const willOpen = !document.body.classList.contains("mobile-memories-open");
    setMobileMemoriesOpenMobileUi(willOpen);
  };

  memoryToggleBtnMobileUi.addEventListener("click", () => {
    toggleMobileMemoriesMobileUi();
  });

  closeMemoriesBtnMobileUi.addEventListener("click", () => {
    setMobileMemoriesOpenMobileUi(false);
  });

  backdropMobileUi.addEventListener("click", () => {
    closeAllMobileDrawersMobileUi();
  });

  if (historyToggleBtn) {
    historyToggleBtn.addEventListener("click", () => {
      document.body.classList.remove("mobile-memories-open");
      setTimeout(syncMobileDrawersMobileUi, 0);
    });
  }

  if (closeHistoryBtn) {
    closeHistoryBtn.addEventListener("click", () => {
      setTimeout(syncMobileDrawersMobileUi, 0);
    });
  }

  if (conversationList) {
    conversationList.addEventListener("click", () => {
      if (isMobileDrawerModeMobileUi()) {
        setTimeout(syncMobileDrawersMobileUi, 0);
      }
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAllMobileDrawersMobileUi();
    }
  });

  const bodyObserverMobileUi = new MutationObserver(() => {
    syncMobileDrawersMobileUi();
  });

  bodyObserverMobileUi.observe(document.body, {
    attributes: true,
    attributeFilter: ["class"]
  });

  const handleViewportChangeMobileUi = () => {
    if (!isMobileDrawerModeMobileUi()) {
      closeAllMobileDrawersMobileUi();
    } else {
      syncMobileDrawersMobileUi();
    }
  };

  if (typeof mobileDrawerMediaMobileUi.addEventListener === "function") {
    mobileDrawerMediaMobileUi.addEventListener("change", handleViewportChangeMobileUi);
  } else if (typeof mobileDrawerMediaMobileUi.addListener === "function") {
    mobileDrawerMediaMobileUi.addListener(handleViewportChangeMobileUi);
  }

  syncMobileDrawersMobileUi();
})();

/* --- Mobile composer tools collapse --- */
(() => {
  const toggleComposerToolsBtn = document.getElementById("toggleComposerTools");
  const composerTools = document.getElementById("composerTools");

  if (!toggleComposerToolsBtn || !composerTools) return;

  const mobileComposerMedia = window.matchMedia("(max-width: 760px)");

  const isPhoneLayout = () => mobileComposerMedia.matches;

  const setComposerToolsOpen = (open) => {
    const shouldOpen = !!open && isPhoneLayout();
    composerTools.classList.toggle("open", shouldOpen);
    toggleComposerToolsBtn.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
    toggleComposerToolsBtn.textContent = shouldOpen ? "Hide tools" : "Tools";
  };

  const resetComposerTools = () => {
    composerTools.classList.remove("open");
    toggleComposerToolsBtn.setAttribute("aria-expanded", "false");
    toggleComposerToolsBtn.textContent = "Tools";
  };

  toggleComposerToolsBtn.addEventListener("click", () => {
    if (!isPhoneLayout()) return;
    setComposerToolsOpen(!composerTools.classList.contains("open"));
  });

  const handleComposerViewportChange = () => {
    if (!isPhoneLayout()) {
      composerTools.classList.remove("open");
      toggleComposerToolsBtn.setAttribute("aria-expanded", "false");
      toggleComposerToolsBtn.textContent = "Tools";
    } else {
      resetComposerTools();
    }
  };

  if (typeof mobileComposerMedia.addEventListener === "function") {
    mobileComposerMedia.addEventListener("change", handleComposerViewportChange);
  } else if (typeof mobileComposerMedia.addListener === "function") {
    mobileComposerMedia.addListener(handleComposerViewportChange);
  }

  handleComposerViewportChange();
})();

