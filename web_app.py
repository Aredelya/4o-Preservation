import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

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
    delete_memory,
    get_all_messages,
    get_conversation_title,
    get_recent_messages,
    init_db,
    list_conversations,
    list_memories,
    load_env_file,
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
        height: calc(100vh - 72px);
        box-sizing: border-box;
      }
      section {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 12px;
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
      }
      .list button.active {
        border-color: var(--accent);
        background: rgba(91, 140, 255, 0.12);
      }
      .messages {
        flex: 1;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .bubble {
        padding: 10px 12px;
        border-radius: 10px;
        line-height: 1.4;
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
      .composer {
        display: flex;
        gap: 8px;
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
        background: transparent;
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 8px;
        color: inherit;
      }
      @media (max-width: 900px) {
        main {
          grid-template-columns: 1fr;
          height: auto;
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
      <section>
        <h2>Conversations</h2>
        <div class="list" id="conversationList"></div>
      </section>
      <section>
        <h2 id="conversationTitle">Chat</h2>
        <div class="messages" id="messageList"></div>
        <div class="composer">
          <textarea id="messageInput" placeholder="Type a message..."></textarea>
          <input type="file" id="fileInput" multiple accept="image/*,.txt,.md,.csv,.json,.py,.log" />
          <button class="primary" id="sendMessage">Send</button>
        </div>
      </section>
      <section>
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
      };

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

      const renderConversations = () => {
        conversationList.innerHTML = "";
        state.conversations.forEach((convo) => {
          const button = document.createElement("button");
          button.textContent = `${convo.title || "Untitled"} · ${convo.id.slice(0, 8)}`;
          button.className = convo.id === state.activeConversation ? "active" : "";
          button.onclick = () => selectConversation(convo.id);
          conversationList.appendChild(button);
        });
      };

      const renderMessages = (messages = []) => {
        messageList.innerHTML = "";
        messages.forEach((message) => {
          const bubble = document.createElement("div");
          bubble.className = `bubble ${message.role}`;
          bubble.textContent = message.content;
          messageList.appendChild(bubble);
        });
        messageList.scrollTop = messageList.scrollHeight;
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

      const loadConversations = async () => {
        const data = await api("/api/conversations");
        state.conversations = data.conversations;
        if (!state.activeConversation && data.conversations.length) {
          state.activeConversation = data.conversations[0].id;
        }
        renderConversations();
        if (state.activeConversation) {
          await loadMessages(state.activeConversation);
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
        state.activeConversation = conversationId;
        renderConversations();
        await loadMessages(conversationId);
      };

      const createConversation = async () => {
        const data = await api("/api/conversations", { method: "POST" });
        state.activeConversation = data.id;
        await loadConversations();
      };

      const sendMessage = async () => {
        const content = messageInput.value.trim();
        const files = Array.from(fileInput.files || []);
        if (!state.activeConversation) return;
        if (!content && files.length === 0) return;

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

        messageInput.value = "";
        fileInput.value = "";
        await api("/api/send", {
          method: "POST",
          body: JSON.stringify({
            conversation_id: state.activeConversation,
            content,
            attachments,
          }),
        });
        await loadMessages(state.activeConversation);
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
        await api("/api/memories", { method: "DELETE" });
        await loadMemories();
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

      loadConversations();
      loadMemories();
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
            with connect_db() as conn:
                init_db(conn)
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
            if not conversation_id or (not content and not attachments):
                self._send_text("Missing conversation_id and message payload", HTTPStatus.BAD_REQUEST)
                return

            image_data_urls = [a.get("data_url") for a in attachments if a.get("kind") == "image" and a.get("data_url")]
            file_texts = [(a.get("name") or "file", a.get("text") or "") for a in attachments if a.get("kind") == "text"]
            user_content = build_user_content(content or None, image_data_urls, file_texts)
            user_message = Message("user", user_content)

            with connect_db() as conn:
                init_db(conn)
                history = get_recent_messages(conn, conversation_id)
                system_prompt = build_system_prompt(conn, content or "Attachment upload")
                messages = [Message("system", system_prompt), *history, user_message]
                response_text = call_openai(messages)
                add_message(conn, conversation_id, user_message)
                add_message(conn, conversation_id, Message("assistant", response_text))
            self._send_json({"status": "ok"})
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
