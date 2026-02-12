import json
import math
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Tuple
from urllib import error, request

DB_PATH = os.environ.get("CHATBOT_DB", "chatbot.db")
API_URL = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1/responses")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
MAX_HISTORY = int(os.environ.get("CHATBOT_MAX_HISTORY", "50"))
MAX_OUTPUT_TOKENS = int(os.environ.get("CHATBOT_MAX_OUTPUT_TOKENS", "800"))
EMBEDDING_MODEL = os.environ.get("CHATBOT_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDINGS_ENABLED = os.environ.get("CHATBOT_USE_EMBEDDINGS", "1").lower() not in {"0", "false", "no"}
EMBEDDINGS_TOP_K = int(os.environ.get("CHATBOT_EMBEDDINGS_TOP_K", "6"))
ENV_PATH = os.environ.get("CHATBOT_ENV_FILE", ".env")

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

CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id INTEGER PRIMARY KEY,
    embedding TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
"""

SYSTEM_PROMPT_TEMPLATE = """You are ChatGPT 4o running via API.

Use the following long-term memories to personalize responses. If they are irrelevant, ignore them.
Memories:
{memories}
"""


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


def call_openai_embeddings(input_text: str) -> List[float]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")

    payload = {
        "model": EMBEDDING_MODEL,
        "input": input_text,
    }

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        "https://api.openai.com/v1/embeddings",
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
        raise RuntimeError(
            f"OpenAI Embeddings API error ({http_error.code}): {detail}"
        ) from http_error

    try:
        return response_data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Embeddings API response format: {response_data}") from exc


def upsert_memory_embedding(conn: sqlite3.Connection, memory_id: int, embedding: List[float]) -> None:
    conn.execute(
        """
        INSERT INTO memory_embeddings (memory_id, embedding, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(memory_id) DO UPDATE SET
            embedding = excluded.embedding,
            updated_at = excluded.updated_at
        """,
        (memory_id, json.dumps(embedding), now_iso()),
    )
    conn.commit()


def add_memory(conn: sqlite3.Connection, content: str) -> None:
    cur = conn.execute(
        "INSERT INTO memories (content, created_at) VALUES (?, ?)",
        (content, now_iso()),
    )
    memory_id = cur.lastrowid
    conn.commit()

    if EMBEDDINGS_ENABLED and memory_id is not None:
        embedding = call_openai_embeddings(content)
        upsert_memory_embedding(conn, memory_id, embedding)


def delete_memory(conn: sqlite3.Connection, memory_id: int) -> bool:
    cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))
    conn.commit()
    return cur.rowcount > 0


def clear_memories(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM memories")
    conn.execute("DELETE FROM memory_embeddings")
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


def get_all_messages(conn: sqlite3.Connection, conversation_id: str) -> List[Message]:
    rows = conn.execute(
        """
        SELECT role, content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id
        """,
        (conversation_id,),
    ).fetchall()
    return [Message(row["role"], row["content"]) for row in rows]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return -1.0
    return dot / (mag_a * mag_b)


def find_relevant_memories(conn: sqlite3.Connection, query: str, top_k: int = EMBEDDINGS_TOP_K) -> List[Tuple[int, str, str]]:
    if not EMBEDDINGS_ENABLED:
        return list_memories(conn)

    query_embedding = call_openai_embeddings(query)
    rows = conn.execute(
        """
        SELECT m.id, m.content, m.created_at, me.embedding
        FROM memories m
        LEFT JOIN memory_embeddings me ON m.id = me.memory_id
        ORDER BY m.id
        """
    ).fetchall()

    scored = []
    for row in rows:
        emb_raw = row["embedding"]
        if emb_raw is None:
            memory_embedding = call_openai_embeddings(row["content"])
            upsert_memory_embedding(conn, row["id"], memory_embedding)
        else:
            memory_embedding = json.loads(emb_raw)

        score = cosine_similarity(query_embedding, memory_embedding)
        scored.append((score, row["id"], row["content"], row["created_at"]))

    scored.sort(key=lambda item: item[0], reverse=True)
    best = scored[: max(1, top_k)]
    return [(mem_id, content, created_at) for _, mem_id, content, created_at in best]


def build_system_prompt(conn: sqlite3.Connection, query: Optional[str] = None) -> str:
    if query and EMBEDDINGS_ENABLED:
        memories = find_relevant_memories(conn, query)
    else:
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
        "max_output_tokens": MAX_OUTPUT_TOKENS,
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


def load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        print(f"Warning: Unable to read env file {path}: {exc}")
