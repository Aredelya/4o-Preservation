const state = {
  conversations: [],
  activeConversation: null,
  memories: [],
  searchQuery: "",
  isSending: false,
  editingMessageId: null,
  chatSearchQuery: "",
  chatSearchMatches: [],
  activeChatSearchIndex: -1,
};

let conversationSearchTimer = null;
let latestMessagesRequest = 0;
let latestConversationsRequest = 0;
let statusTimer = null;
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
const exportConversationMarkdownBtn = document.getElementById("exportConversationMarkdown");
const exportConversationJsonBtn = document.getElementById("exportConversationJson");

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

const apiStream = async (path, payload, handlers = {}) => {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(payload),
  });

  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Authentication required");
  }

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Streaming request failed");
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("Streaming not supported by this browser");
  }

  const decoder = new TextDecoder();
  let buffer = "";

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
  if (chatSearchBar || !messageList) return;

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
  chatSearchIcon.textContent = "🔍";
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
  chatSearchPrevBtn.textContent = "↑";
  chatSearchPrevBtn.title = "Previous match";
  chatSearchPrevBtn.setAttribute("aria-label", "Previous match");

  chatSearchNextBtn = document.createElement("button");
  chatSearchNextBtn.type = "button";
  chatSearchNextBtn.className = "secondary";
  chatSearchNextBtn.textContent = "↓";
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

const regenerateAssistantMessage = async (message) => {
  if (!message || message.role !== "assistant" || !message.id) return;
  if (!state.activeConversation || state.isSending) return;

  setComposerBusy(true);
  setEditingState(null);
  setStatus("Regenerating response...");

  try {
    await api("/api/regenerate", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: state.activeConversation,
        message_id: message.id,
        enable_web_search: !!enableWebSearchInput?.checked,
        enable_code_interpreter: !!enableCodeInterpreterInput?.checked,
      }),
    });

    await loadConversations({ refreshMessages: false });
    await loadMessages(state.activeConversation);
    setStatus("Response regenerated.", "", 2200);
  } catch (error) {
    setStatus(`Failed to regenerate response: ${error.message}`, "error");
  } finally {
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
  renderMemories();
};

const selectConversation = async (conversationId) => {
  if (state.isSending) return;

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
      enable_code_interpreter: !!enableCodeInterpreterInput?.checked,
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
      let streamedText = "";
      result = await new Promise((resolve, reject) => {
        apiStream("/api/send-stream", requestBody, {
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
  updateConversationActionState();

  await loadConversations();
  setStatus("Conversation deleted.", "", 2000);
};

const initializeApp = async () => {
  ensureChatSearchUI();

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

  if (!state.activeConversation || !chatSearchInput) return;

  event.preventDefault();
  ensureChatSearchUI();
  chatSearchInput.focus();
  chatSearchInput.select();
});

initializeApp();
updateConversationActionState();
updateImageCommandHint();
window.addEventListener("load", scheduleMessageBottomSnap);
