import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from core import (
    ENV_PATH,
    Message,
    add_memory,
    add_message,
    build_system_prompt,
    build_user_content,
    call_openai,
    clear_memories,
    connect_db,
    create_conversation,
    delete_conversation,
    delete_memory,
    get_all_messages,
    get_conversation_title,
    get_recent_messages,
    init_db,
    list_conversations,
    list_memories,
    load_env_file,
    search_conversations,
    update_conversation_title,
)

HOST = os.environ.get("CHATBOT_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("CHATBOT_WEB_PORT", "8000"))

INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>4o Preservation</title>
    <style>
      :root {
        color-scheme: light dark;
        --bg: #0f1115;
        --panel: #161a22;
        --muted: #9aa4b2;
        --text: #eef1f6;
        --accent: #5b8cff;
        --border: #242a36;
      }
      body {
        margin: 0;
        font-family: "Inter", system-ui, -apple-system, sans-serif;
        background: var(--bg);
        color: var(--text);
        min-height: 100vh;
      }
      header {
        padding: 16px 24px;
        border-bottom: 1px solid var(--border);
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      main {
        display: grid;
        grid-template-columns: 280px 1fr 260px;
        gap: 16px;
        padding: 16px;
        height: calc(100dvh - 72px);
        box-sizing: border-box;
        overflow: hidden;
      }
      section {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        min-height: 0;
        overflow: hidden;
      }
      h2 {
        font-size: 14px;
        margin: 0;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }
      .list {
        display: flex;
        flex-direction: column;
        gap: 8px;
        overflow-y: auto;
      }
      .list button {
        background: transparent;
        border: 1px solid transparent;
        color: inherit;
        text-align: left;
        padding: 8px;
        border-radius: 8px;
        cursor: pointer;
        white-space: pre-line;
      }
      .list-item {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .list-item button {
        flex: 1;
      }
      .danger {
        border: 1px solid var(--border);
        border-radius: 8px;
        background: transparent;
        color: #ff8f8f;
        cursor: pointer;
        padding: 6px 8px;
      }
      .list button.active {
        border-color: var(--accent);
        background: rgba(91, 140, 255, 0.12);
      }
      .search-row {
        display: flex;
        gap: 8px;
      }
      .search-row input {
        flex: 1;
      }
      .messages {
        flex: 1;
        min-height: 0;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 12px;
        padding-right: 4px;
      }
      .bubble {
        padding: 10px 12px;
        border-radius: 10px;
        line-height: 1.5;
        font-size: 15px;
        white-space: pre-wrap;
      }
      .bubble.user {
        background: rgba(91, 140, 255, 0.18);
        align-self: flex-end;
      }
      .bubble.assistant {
        background: rgba(255, 255, 255, 0.08);
        align-self: flex-start;
      }

      button:disabled,
      textarea:disabled,
      input:disabled {
        opacity: 0.65;
        cursor: not-allowed;
      }
      
      .bubble.pending {
        opacity: 0.8;
        font-style: italic;
      }
      
      .bubble.error {
        border: 1px solid rgba(255, 143, 143, 0.45);
        background: rgba(255, 143, 143, 0.12);
      }
      .composer {
        display: flex;
        gap: 8px;
        position: sticky;
        bottom: 0;
        background: var(--panel);
        padding-top: 8px;
      }
      textarea {
        flex: 1;
        background: transparent;
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 10px;
        color: inherit;
        resize: none;
        min-height: 64px;
      }
      button.primary {
        background: var(--accent);
        color: white;
        border: none;
        border-radius: 10px;
        padding: 10px 16px;
        cursor: pointer;
      }
      .memory-item {
        display: flex;
        flex-direction: column;
        gap: 4px;
        padding: 8px;
        border: 1px solid var(--border);
        border-radius: 8px;
      }
      .memory-actions {
        display: flex;
        gap: 8px;
      }
      .muted {
        color: var(--muted);
        font-size: 12px;
      }
      .row {
        display: flex;
        gap: 8px;
      }
      input[type="text"] {
        flex: 1;
        min-width: 0;
        background: transparent;
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 8px;
        color: inherit;
      }
      @media (max-width: 1100px) {
        body {
          font-size: 16px;
        }
        header {
          padding: 12px 14px;
        }
        main {
          grid-template-columns: 1fr;
          grid-template-rows: minmax(120px, 18dvh) minmax(0, 1fr) minmax(220px, 30dvh);
          min-height: calc(100dvh - 64px);
          height: auto;
          gap: 10px;
          padding: 10px;
          overflow-y: auto;
        }
        .panel-conversations,
        .panel-memories {
          min-height: 0;
        }
        .panel-chat {
          min-height: 46dvh;
        }
        .panel-memories {
          overflow-y: auto;
          padding-bottom: 10px;
        }
        .panel-memories .list {
          flex: 1;
          min-height: 120px;
        }
        .panel-memories .row {
          flex-wrap: wrap;
        }
        .panel-memories #saveMemory {
          flex: 1 1 100%;
        }
        .panel-memories #clearMemories {
          margin-top: 6px;
          background: transparent;
          border: 1px solid #8f4b4b;
          color: #ffb3b3;
          opacity: 0.9;
        }
        h2 {
          font-size: 13px;
        }
        .list button {
          padding: 10px;
          font-size: 15px;
        }
        .messages {
          gap: 12px;
        }
        .bubble {
          font-size: 16px;
          line-height: 1.6;
          padding: 12px 13px;
        }
        .composer {
          position: sticky;
          bottom: 0;
          padding-top: 8px;
          flex-direction: column;
        }
        textarea {
          min-height: 108px;
          font-size: 16px;
        }
        #fileInput {
          font-size: 15px;
        }
        button,
        button.primary {
          font-size: 15px;
          min-height: 42px;
        }
      }

      @media (max-width: 1100px) and (orientation: landscape) {
        main {
          grid-template-columns: minmax(165px, 0.8fr) minmax(0, 1.7fr) minmax(190px, 1fr);
          grid-template-rows: minmax(0, 1fr);
          min-height: calc(100dvh - 64px);
          height: calc(100dvh - 64px);
          overflow: hidden;
        }
        .panel-chat {
          min-height: 0;
        }
        .composer {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          grid-template-areas:
            "text send"
            "file file";
          align-items: stretch;
        }
        .composer textarea {
          grid-area: text;
          min-height: 86px;
        }
        .composer #sendMessage {
          grid-area: send;
          min-width: 84px;
        }
        .composer #fileInput {
          grid-area: file;
          width: 100%;
          min-width: 0;
        }
        .panel-memories .row {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
        }
        .panel-memories #saveMemory,
        .panel-memories #clearMemories {
          width: 100%;
        }
      }
    </style>
  </head>
  <body>
    <header>
      <div>
        <strong>4o Preservation</strong>
        <div class="muted">Shared conversations + memories</div>
      </div>
      <button class="primary" id="newConversation">New chat</button>
    </header>
    <main>
      <section class="panel-conversations">
        <h2>Conversations</h2>
        <div class="search-row">
          <input type="text" id="conversationSearch" placeholder="Search chats..." />
          <button id="clearConversationSearch">Clear</button>
        </div>
        <div class="list" id="conversationList"></div>
      </section>
      <section class="panel-chat">
        <h2 id="conversationTitle">Chat</h2>
        <div class="messages" id="messageList"></div>
        <div class="composer">
          <textarea id="messageInput" placeholder="Type a message..."></textarea>
          <input type="file" id="fileInput" multiple accept="image/*,.txt,.md,.csv,.json,.py,.log" />
          <button class="primary" id="sendMessage">Send</button>
        </div>
      </section>
      <section class="panel-memories">
        <h2>Memories</h2>
        <div class="row">
          <input type="text" id="memoryInput" placeholder="Add a memory..." />
          <button class="primary" id="saveMemory">Save</button>
        </div>
        <div class="list" id="memoryList"></div>
        <button id="clearMemories">Clear memories</button>
      </section>
    </main>
    <script>
      const state = {
        conversations: [],
        activeConversation: null,
        memories: [],
        searchQuery: "",
        isSending: false,
        pendingAssistantId: null,
      };
    
      let conversationSearchTimer = null;
    
      const conversationList = document.getElementById("conversationList");
      const messageList = document.getElementById("messageList");
      const conversationTitle = document.getElementById("conversationTitle");
      const messageInput = document.getElementById("messageInput");
      const fileInput = document.getElementById("fileInput");
      const newConversationBtn = document.getElementById("newConversation");
      const sendMessageBtn = document.getElementById("sendMessage");
      const memoryInput = document.getElementById("memoryInput");
      const saveMemoryBtn = document.getElementById("saveMemory");
      const memoryList = document.getElementById("memoryList");
      const clearMemoriesBtn = document.getElementById("clearMemories");
      const conversationSearchInput = document.getElementById("conversationSearch");
      const clearConversationSearchBtn = document.getElementById("clearConversationSearch");
    
      const api = async (path, options = {}) => {
        const response = await fetch(path, {
          headers: { "Content-Type": "application/json" },
          ...options,
        });
    
        if (!response.ok) {
          const text = await response.text();
          throw new Error(text || "Request failed");
        }
    
        return response.json();
      };
    
      const setComposerBusy = (busy) => {
        state.isSending = busy;
        messageInput.disabled = busy;
        fileInput.disabled = busy;
        sendMessageBtn.disabled = busy;
        sendMessageBtn.textContent = busy ? "Sending..." : "Send";
      };
    
      const renderConversations = () => {
        conversationList.innerHTML = "";
    
        state.conversations.forEach((convo) => {
          const row = document.createElement("div");
          row.className = "list-item";
    
          const button = document.createElement("button");
          const title = `${convo.title || "Untitled"} · ${convo.id.slice(0, 8)}`;
          const snippet = (convo.snippet || "").replace(/\s+/g, " ").trim();
          const preview = snippet.length > 96 ? `${snippet.slice(0, 93)}...` : snippet;
          const label = preview ? `${title}\\n${preview}` : title;
    
          button.textContent = label;
          button.className = convo.id === state.activeConversation ? "active" : "";
          button.onclick = () => selectConversation(convo.id);
    
          const remove = document.createElement("button");
          remove.className = "danger";
          remove.textContent = "✕";
          remove.title = "Delete conversation";
          remove.onclick = async () => {
            if (!confirm("Delete this conversation?")) return;
            await deleteConversation(convo.id);
          };
    
          row.appendChild(button);
          row.appendChild(remove);
          conversationList.appendChild(row);
        });
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
        if (shouldSkipAutoSnap()) {
          return;
        }
    
        const delays = [0, 50, 140, 280, 520];
        delays.forEach((delay) => {
          setTimeout(() => {
            if (!shouldSkipAutoSnap()) {
              scrollMessagesToBottom();
            }
          }, delay);
        });
    
        requestAnimationFrame(() => {
          if (!shouldSkipAutoSnap()) {
            scrollMessagesToBottom();
          }
        });
      };
    
      const createBubble = ({ role, content, extraClass = "", id = "" }) => {
        const bubble = document.createElement("div");
        bubble.className = `bubble ${role}${extraClass ? ` ${extraClass}` : ""}`;
        if (id) {
          bubble.dataset.id = id;
        }
        bubble.textContent = content;
        return bubble;
      };
    
      const appendMessageBubble = ({ role, content, extraClass = "", id = "" }) => {
        const bubble = createBubble({ role, content, extraClass, id });
        messageList.appendChild(bubble);
        scheduleMessageBottomSnap();
        return bubble;
      };
    
      const renderMessages = (messages = [], autoSnap = true) => {
        messageList.innerHTML = "";
    
        messages.forEach((message) => {
          const bubble = createBubble({
            role: message.role,
            content: message.content,
          });
          messageList.appendChild(bubble);
        });
    
        if (autoSnap) {
          scheduleMessageBottomSnap();
        }
      };
    
      const renderMemories = () => {
        memoryList.innerHTML = "";
    
        state.memories.forEach((memory) => {
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
          remove.onclick = () => deleteMemory(memory.id);
    
          actions.appendChild(remove);
          card.appendChild(text);
          card.appendChild(meta);
          card.appendChild(actions);
          memoryList.appendChild(card);
        });
      };
    
      const loadConversations = async ({ refreshMessages = true } = {}) => {
        const query = state.searchQuery.trim();
        const previousActiveConversation = state.activeConversation;
        const searchSuffix = query ? `?q=${encodeURIComponent(query)}` : "";
        const data = await api(`/api/conversations${searchSuffix}`);
    
        state.conversations = data.conversations;
    
        if (!state.activeConversation && data.conversations.length) {
          state.activeConversation = data.conversations[0].id;
        }
    
        if (
          state.activeConversation &&
          !state.conversations.some((convo) => convo.id === state.activeConversation)
        ) {
          state.activeConversation = state.conversations.length ? state.conversations[0].id : null;
        }
    
        renderConversations();
    
        if (!refreshMessages) {
          return;
        }
    
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
    
      const loadMessages = async (conversationId) => {
        const data = await api(`/api/conversations/${conversationId}`);
        conversationTitle.textContent = data.title || "Chat";
        renderMessages(data.messages);
      };
    
      const loadMemories = async () => {
        const data = await api("/api/memories");
        state.memories = data.memories;
        renderMemories();
      };
    
      const selectConversation = async (conversationId) => {
        if (state.isSending) return;
        state.activeConversation = conversationId;
        renderConversations();
        await loadMessages(conversationId);
      };
    
      const createConversation = async () => {
        if (state.isSending) return;
        const data = await api("/api/conversations", { method: "POST" });
        await loadConversations({ refreshMessages: false });
        await selectConversation(data.id);
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
    
      const buildAttachmentSummary = (attachments) => {
        if (!attachments.length) return "";
        const names = attachments.map((file) => file.name).join(", ");
        return `[Attached: ${names}]`;
      };
    
      const sendMessage = async () => {
        if (state.isSending) return;
      
        const content = messageInput.value.trim();
        const files = Array.from(fileInput.files || []);
        if (!content && files.length === 0) return;
      
        setComposerBusy(true);
      
        try {
          const conversationId = await ensureActiveConversation();
      
          // Handle /title without creating a pending assistant bubble.
          if (content.toLowerCase().startsWith("/title")) {
            const parts = content.split(/\s+/, 2);
            const newTitle = parts.length > 1 ? parts[1].trim() : "";
      
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
            return;
          }
      
          const attachments = [];
          for (const file of files) {
            if (file.type.startsWith("image/")) {
              const dataUrl = await new Promise((resolve) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.readAsDataURL(file);
              });
              attachments.push({ kind: "image", name: file.name, data_url: dataUrl });
            } else {
              const text = await file.text();
              attachments.push({ kind: "text", name: file.name, text });
            }
          }
      
          const optimisticTextParts = [];
          if (content) {
            optimisticTextParts.push(content);
          }
      
          const attachmentSummary = buildAttachmentSummary(files);
          if (attachmentSummary) {
            optimisticTextParts.push(attachmentSummary);
          }
      
          appendMessageBubble({
            role: "user",
            content: optimisticTextParts.join("\n\n") || "(Attachment upload)",
          });
      
          const pendingId = `pending-${Date.now()}`;
          state.pendingAssistantId = pendingId;
      
          appendMessageBubble({
            role: "assistant",
            content: "Thinking...",
            extraClass: "pending",
            id: pendingId,
          });
      
          messageInput.value = "";
          fileInput.value = "";
      
          await api("/api/send", {
            method: "POST",
            body: JSON.stringify({
              conversation_id: conversationId,
              content,
              attachments,
            }),
          });
      
          await loadConversations({ refreshMessages: false });
          await loadMessages(conversationId);
        } catch (error) {
          const pendingBubble = state.pendingAssistantId
            ? messageList.querySelector(`[data-id="${state.pendingAssistantId}"]`)
            : null;
      
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
        } finally {
          state.pendingAssistantId = null;
          setComposerBusy(false);
          messageInput.focus();
        }
      };
    
      const addMemory = async () => {
        const content = memoryInput.value.trim();
        if (!content) return;
    
        memoryInput.value = "";
        await api("/api/memories", {
          method: "POST",
          body: JSON.stringify({ content }),
        });
        await loadMemories();
      };
    
      const deleteMemory = async (id) => {
        await api(`/api/memories/${id}`, { method: "DELETE" });
        await loadMemories();
      };
    
      const clearMemories = async () => {
        if (!confirm("Clear all memories? This cannot be undone.")) {
          return;
        }
    
        await api("/api/memories", { method: "DELETE" });
        await loadMemories();
      };
    
      const deleteConversation = async (id) => {
        await api(`/api/conversations/${id}`, { method: "DELETE" });
        if (state.activeConversation === id) {
          state.activeConversation = null;
        }
        await loadConversations();
      };
    
      newConversationBtn.onclick = createConversation;
      sendMessageBtn.onclick = sendMessage;
      saveMemoryBtn.onclick = addMemory;
      clearMemoriesBtn.onclick = clearMemories;
    
      messageInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          sendMessage();
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
        conversationSearchTimer = setTimeout(async () => {
          await loadConversations({ refreshMessages: false });
        }, 180);
      });
    
      clearConversationSearchBtn.onclick = async () => {
        if (conversationSearchTimer) {
          clearTimeout(conversationSearchTimer);
          conversationSearchTimer = null;
        }
        conversationSearchInput.value = "";
        state.searchQuery = "";
        await loadConversations({ refreshMessages: false });
      };
    
      const initializeApp = async () => {
        try {
          await loadMemories();
        } catch (error) {
          console.error("Failed to load memories:", error);
        }
    
        try {
          await loadConversations();
          scheduleMessageBottomSnap();
        } catch (error) {
          console.error("Failed to load conversations:", error);
        }
      };
    
      initializeApp();
      window.addEventListener("load", scheduleMessageBottomSnap);
      window.addEventListener("pageshow", scheduleMessageBottomSnap);
    
      document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
          scheduleMessageBottomSnap();
        }
      });
    
      if (window.visualViewport) {
        window.visualViewport.addEventListener("resize", scheduleMessageBottomSnap);
      }
    </script>
  </body>
</html>
"""


class ChatHandler(BaseHTTPRequestHandler):
    def _send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_text(self, text: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            payload = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if parsed.path == "/api/conversations":
            params = parse_qs(parsed.query)
            query = (params.get("q", [""])[0] or "").strip()
            with connect_db() as conn:
                init_db(conn)
                if query:
                    conversations = [
                        {
                            "id": convo_id,
                            "title": title,
                            "created_at": created_at,
                            "snippet": snippet,
                        }
                        for convo_id, title, created_at, snippet in search_conversations(conn, query)
                    ]
                else:
                    conversations = [
                        {"id": convo_id, "title": title, "created_at": created_at}
                        for convo_id, title, created_at in list_conversations(conn)
                    ]
            self._send_json({"conversations": conversations})
            return

        if parsed.path.startswith("/api/conversations/"):
            conversation_id = parsed.path.split("/")[-1]
            with connect_db() as conn:
                init_db(conn)
                messages = [
                    {"role": message.role, "content": message.content}
                    for message in get_all_messages(conn, conversation_id)
                ]
                title = get_conversation_title(conn, conversation_id)
            self._send_json({"messages": messages, "title": title})
            return

        if parsed.path == "/api/memories":
            with connect_db() as conn:
                init_db(conn)
                memories = [
                    {"id": mem_id, "content": content, "created_at": created_at}
                    for mem_id, content, created_at in list_memories(conn)
                ]
            self._send_json({"memories": memories})
            return

        self._send_text("Not found", status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/conversations":
            with connect_db() as conn:
                init_db(conn)
                conversation_id = create_conversation(conn)
            self._send_json({"id": conversation_id})
            return

        if parsed.path == "/api/send":
            payload = self._read_json()
            conversation_id = payload.get("conversation_id")
            content = (payload.get("content") or "").strip()
            attachments = payload.get("attachments") or []
            use_web_search = False

            if not conversation_id or (not content and not attachments):
                self._send_text("Missing conversation_id and message payload", HTTPStatus.BAD_REQUEST)
                return

            # Handle /title in the web UI the same way the CLI does.
            if content.lower().startswith("/title"):
                parts = content.split(None, 1)
                new_title = parts[1].strip() if len(parts) > 1 else ""
                if not new_title:
                    self._send_text("Missing title text", HTTPStatus.BAD_REQUEST)
                    return

                with connect_db() as conn:
                    init_db(conn)
                    updated = update_conversation_title(conn, conversation_id, new_title)

                self._send_json(
                    {
                        "status": "ok",
                        "command": "title",
                        "updated": updated,
                        "title": new_title,
                    }
                )
                return

            if content.lower().startswith("/web "):
                use_web_search = True
                content = content[5:].strip()

            image_data_urls = [
                a.get("data_url")
                for a in attachments
                if a.get("kind") == "image" and a.get("data_url")
            ]
            file_texts = [
                (a.get("name") or "file", a.get("text") or "")
                for a in attachments
                if a.get("kind") == "text"
            ]
            user_content = build_user_content(content or None, image_data_urls, file_texts)
            user_message = Message("user", user_content)

            with connect_db() as conn:
                init_db(conn)
                history = get_recent_messages(conn, conversation_id)
                system_prompt = build_system_prompt(conn, content or "Attachment upload")
                messages = [Message("system", system_prompt), *history, user_message]
                response_text = call_openai(messages, use_web_search=use_web_search)
                add_message(conn, conversation_id, user_message)
                add_message(conn, conversation_id, Message("assistant", response_text))

            self._send_json(
                {
                    "status": "ok",
                    "assistant_message": {
                        "role": "assistant",
                        "content": response_text,
                    },
                }
            )
            return

        if parsed.path == "/api/memories":
            payload = self._read_json()
            content = payload.get("content")
            if not content:
                self._send_text("Missing memory content", HTTPStatus.BAD_REQUEST)
                return
            with connect_db() as conn:
                init_db(conn)
                add_memory(conn, content)
            self._send_json({"status": "ok"})
            return

        if parsed.path == "/api/title":
            payload = self._read_json()
            conversation_id = payload.get("conversation_id")
            title = payload.get("title")
            if not conversation_id or title is None:
                self._send_text("Missing conversation_id or title", HTTPStatus.BAD_REQUEST)
                return
            with connect_db() as conn:
                init_db(conn)
                updated = update_conversation_title(conn, conversation_id, title)
            self._send_json({"updated": updated})
            return

        self._send_text("Not found", status=HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/conversations/"):
            conversation_id = parsed.path.split("/")[-1]
            with connect_db() as conn:
                init_db(conn)
                deleted = delete_conversation(conn, conversation_id)
            self._send_json({"deleted": deleted})
            return

        if parsed.path == "/api/memories":
            with connect_db() as conn:
                init_db(conn)
                clear_memories(conn)
            self._send_json({"status": "ok"})
            return

        if parsed.path.startswith("/api/memories/"):
            memory_id = parsed.path.split("/")[-1]
            try:
                memory_id_int = int(memory_id)
            except ValueError:
                self._send_text("Invalid memory id", HTTPStatus.BAD_REQUEST)
                return
            with connect_db() as conn:
                init_db(conn)
                deleted = delete_memory(conn, memory_id_int)
            self._send_json({"deleted": deleted})
            return

        self._send_text("Not found", status=HTTPStatus.NOT_FOUND)


def main() -> None:
    load_env_file(ENV_PATH)
    with connect_db() as conn:
        init_db(conn)
    server = HTTPServer((HOST, PORT), ChatHandler)
    print(f"Web app running on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
