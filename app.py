#!/usr/bin/env python3
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Tuple
from urllib import request, error

DB_PATH = os.environ.get("CHATBOT_DB", "chatbot.db")
API_URL = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1/responses")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
MAX_HISTORY = int(os.environ.get("CHATBOT_MAX_HISTORY", "50"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

SYSTEM_PROMPT_TEMPLATE = """You are ChatGPT 4o running via API.

Use the following long-term memories to personalize responses. If they are irrelevant, ignore them.
Memories:
{memories}
"""

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_BLUE = "\033[34m"
ANSI_GREEN = "\033[32m"
ANSI_CYAN = "\033[36m"
ANSI_DIM = "\033[2m"

@dataclass
class Message:
    role: str
    content: str


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def create_conversation(conn: sqlite3.Connection, title: Optional[str] = None) -> str:
    conversation_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO conversations (id, title, created_at) VALUES (?, ?, ?)",
        (conversation_id, title, now_iso()),
    )
    conn.commit()
    return conversation_id


def list_conversations(conn: sqlite3.Connection) -> List[Tuple[str, Optional[str], str]]:
    rows = conn.execute(
        "SELECT id, title, created_at FROM conversations ORDER BY created_at DESC"
    ).fetchall()
    return [(row["id"], row["title"], row["created_at"]) for row in rows]


def conversation_exists(conn: sqlite3.Connection, conversation_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM conversations WHERE id = ? LIMIT 1", (conversation_id,)
    ).fetchone()
    return row is not None


def update_conversation_title(
    conn: sqlite3.Connection, conversation_id: str, title: str
) -> bool:
    cur = conn.execute(
        "UPDATE conversations SET title = ? WHERE id = ?",
        (title, conversation_id),
    )
    conn.commit()
    return cur.rowcount > 0


def get_conversation_title(
    conn: sqlite3.Connection, conversation_id: str
) -> Optional[str]:
    row = conn.execute(
        "SELECT title FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    return row["title"] if row else None


def list_memories(conn: sqlite3.Connection) -> List[Tuple[int, str, str]]:
    rows = conn.execute(
        "SELECT id, content, created_at FROM memories ORDER BY id"
    ).fetchall()
    return [(row["id"], row["content"], row["created_at"]) for row in rows]


def add_memory(conn: sqlite3.Connection, content: str) -> None:
    conn.execute(
        "INSERT INTO memories (content, created_at) VALUES (?, ?)",
        (content, now_iso()),
    )
    conn.commit()


def delete_memory(conn: sqlite3.Connection, memory_id: int) -> bool:
    cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()
    return cur.rowcount > 0


def clear_memories(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM memories")
    conn.commit()


def add_message(conn: sqlite3.Connection, conversation_id: str, message: Message) -> None:
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, message.role, message.content, now_iso()),
    )
    conn.commit()


def get_recent_messages(conn: sqlite3.Connection, conversation_id: str) -> List[Message]:
    rows = conn.execute(
        """
        SELECT role, content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (conversation_id, MAX_HISTORY),
    ).fetchall()
    return [Message(row["role"], row["content"]) for row in reversed(rows)]


def build_system_prompt(conn: sqlite3.Connection) -> str:
    memories = list_memories(conn)
    if memories:
        memory_lines = [f"- ({mem_id}) {content}" for mem_id, content, _ in memories]
        memories_text = "\n".join(memory_lines)
    else:
        memories_text = "- (none)"
    return SYSTEM_PROMPT_TEMPLATE.format(memories=memories_text)


def call_openai(messages: Iterable[Message]) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")

    payload = {
        "model": MODEL,
        "input": [
            {"role": message.role, "content": message.content} for message in messages
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        API_URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with request.urlopen(req, timeout=90) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as http_error:
        detail = http_error.read().decode("utf-8")
        raise RuntimeError(f"OpenAI API error ({http_error.code}): {detail}") from http_error

    try:
        return response_data["output"][0]["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected API response format: {response_data}") from exc


def supports_color() -> bool:
    return sys.stdout.isatty()


def style(text: str, *codes: str) -> str:
    if not supports_color() or not codes:
        return text
    return f"{''.join(codes)}{text}{ANSI_RESET}"


def format_prompt(conversation_id: str, title: Optional[str]) -> str:
    title_display = title or "Untitled"
    convo_short = conversation_id.split("-")[0]
    header = f"{title_display} · {convo_short}"
    return style(f"\n{header}\nYou:", ANSI_BOLD, ANSI_BLUE) + " "


def print_banner(conversation_id: str, title: Optional[str]) -> None:
    title_display = title or "Untitled"
    banner = f"Chat ready · {title_display}"
    print(style(banner, ANSI_BOLD, ANSI_GREEN))
    print(style(f"Conversation: {conversation_id}", ANSI_DIM))


def print_help() -> None:
    print(
        """
Commands:
  /new                     Start a new conversation.
  /conversations           List saved conversations.
  /open <id>               Resume a conversation by id.
  /title <text>            Rename the current conversation.
  /memory add <text>        Add a long-term memory.
  /memory list              List stored memories.
  /memory delete <id>       Delete a memory by id.
  /memory clear             Delete all memories.
  /help                    Show this help message.
  /exit                    Exit the app.

Notes:
  - Set OPENAI_API_KEY in your environment.
  - Memories are injected into the system prompt on every request.
  - Use /conversations and /open to pick up past threads.
"""
    )


def handle_memory_command(conn: sqlite3.Connection, args: List[str]) -> None:
    if not args:
        print("Usage: /memory [add|list|delete|clear] ...")
        return

    action = args[0]
    if action == "add":
        content = " ".join(args[1:]).strip()
        if not content:
            print("Usage: /memory add <text>")
            return
        add_memory(conn, content)
        print("Memory saved.")
    elif action == "list":
        memories = list_memories(conn)
        if not memories:
            print("No memories saved.")
            return
        for mem_id, content, created_at in memories:
            print(f"[{mem_id}] {content} (added {created_at})")
    elif action == "delete":
        if len(args) < 2:
            print("Usage: /memory delete <id>")
            return
        try:
            mem_id = int(args[1])
        except ValueError:
            print("Memory id must be a number.")
            return
        if delete_memory(conn, mem_id):
            print("Memory deleted.")
        else:
            print("Memory not found.")
    elif action == "clear":
        clear_memories(conn)
        print("All memories cleared.")
    else:
        print("Unknown /memory action. Use add, list, delete, or clear.")


def main() -> int:
    conn = connect_db()
    init_db(conn)

    conversation_id = create_conversation(conn)
    current_title = get_conversation_title(conn, conversation_id)
    print_banner(conversation_id, current_title)
    print("Type /help for commands.")

    while True:
        try:
            user_input = input(format_prompt(conversation_id, current_title)).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.split()
            command = parts[0]
            args = parts[1:]

            if command == "/exit":
                print("Goodbye.")
                break
            if command == "/help":
                print_help()
                continue
            if command == "/new":
                conversation_id = create_conversation(conn)
                current_title = get_conversation_title(conn, conversation_id)
                print_banner(conversation_id, current_title)
                continue
            if command == "/conversations":
                conversations = list_conversations(conn)
                if not conversations:
                    print("No conversations found.")
                    continue
                for convo_id, title, created_at in conversations:
                    title_display = title or "Untitled"
                    print(f"{convo_id} | {title_display} | {created_at}")
                continue
            if command == "/open":
                if not args:
                    print("Usage: /open <id>")
                    continue
                target_id = args[0]
                if conversation_exists(conn, target_id):
                    conversation_id = target_id
                    current_title = get_conversation_title(conn, conversation_id)
                    print_banner(conversation_id, current_title)
                else:
                    print("Conversation not found.")
                continue
            if command == "/title":
                title = " ".join(args).strip()
                if not title:
                    print("Usage: /title <text>")
                    continue
                if update_conversation_title(conn, conversation_id, title):
                    current_title = title
                    print("Title updated.")
                else:
                    print("Unable to update title.")
                continue
            if command == "/memory":
                handle_memory_command(conn, args)
                continue

            print("Unknown command. Type /help for help.")
            continue

        add_message(conn, conversation_id, Message("user", user_input))
        system_prompt = build_system_prompt(conn)
        history = get_recent_messages(conn, conversation_id)
        messages = [Message("system", system_prompt), *history]

        try:
            response_text = call_openai(messages)
        except RuntimeError as exc:
            print(f"Error: {exc}")
            continue

        add_message(conn, conversation_id, Message("assistant", response_text))
        print(f"\nAssistant: {response_text}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
