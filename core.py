import json
import math
import os
import sqlite3
import uuid
import base64
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Tuple
from urllib import error, request
from urllib.parse import quote

DB_PATH = os.environ.get("CHATBOT_DB", "chatbot.db")
API_URL = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1/responses")
IMAGES_API_URL = os.environ.get("OPENAI_IMAGES_API_URL", "https://api.openai.com/v1/images/generations")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-2024-11-20")
IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
MAX_HISTORY = int(os.environ.get("CHATBOT_MAX_HISTORY", "50"))
MAX_OUTPUT_TOKENS = int(os.environ.get("CHATBOT_MAX_OUTPUT_TOKENS", "800"))
EMBEDDING_MODEL = os.environ.get("CHATBOT_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDINGS_ENABLED = os.environ.get("CHATBOT_USE_EMBEDDINGS", "1").lower() not in {"0", "false", "no"}
EMBEDDINGS_TOP_K = int(os.environ.get("CHATBOT_EMBEDDINGS_TOP_K", "6"))
ENV_PATH = os.environ.get("CHATBOT_ENV_FILE", ".env")
WEB_SEARCH_ENABLED = os.environ.get("CHATBOT_ENABLE_WEB_SEARCH", "1").lower() not in {"0", "false", "no"}
WEB_SEARCH_TOOL = os.environ.get("OPENAI_WEB_SEARCH_TOOL", "web_search").strip() or "web_search"
CODE_INTERPRETER_ENABLED = os.environ.get("CHATBOT_ENABLE_CODE_INTERPRETER", "1").lower() not in {"0", "false", "no"}
CODE_INTERPRETER_MEMORY_LIMIT = os.environ.get("CHATBOT_CODE_INTERPRETER_MEMORY_LIMIT", "1g").strip() or "1g"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id_id
ON messages (conversation_id, id);

CREATE INDEX IF NOT EXISTS idx_conversations_updated_at_created_at
ON conversations (updated_at DESC, created_at DESC);
"""

SYSTEM_PROMPT_TEMPLATE = """You are ChatGPT 4o running via API.
Use the following long-term memories to personalize responses. If they are irrelevant, ignore them.

Memories:
{memories}
"""


@dataclass
class Message:
    role: str
    content: object


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
    }
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN updated_at TEXT")
        conn.execute(
            """
            UPDATE conversations
            SET updated_at = COALESCE(
                (
                    SELECT MAX(messages.created_at)
                    FROM messages
                    WHERE messages.conversation_id = conversations.id
                ),
                created_at
            )
            WHERE updated_at IS NULL
            """
        )
        conn.commit()


def create_conversation(conn: sqlite3.Connection, title: Optional[str] = None) -> str:
    conversation_id = str(uuid.uuid4())
    now = now_iso()
    conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (conversation_id, title, now, now),
    )
    conn.commit()
    return conversation_id


def list_conversations(conn: sqlite3.Connection) -> List[Tuple[str, Optional[str], str]]:
    rows = conn.execute(
        "SELECT id, title, created_at FROM conversations ORDER BY updated_at DESC, created_at DESC"
    ).fetchall()
    return [(row["id"], row["title"], row["created_at"]) for row in rows]


def search_conversations(
    conn: sqlite3.Connection, query: str, limit: int = 25
) -> List[Tuple[str, Optional[str], str, Optional[str]]]:
    term = query.strip().lower()
    if not term:
        return []

    like_term = f"%{term}%"
    rows = conn.execute(
        """
        SELECT
            c.id,
            c.title,
            c.created_at,
            MAX(CASE WHEN LOWER(m.content) LIKE ? THEN m.content ELSE NULL END) AS snippet,
            MAX(CASE WHEN LOWER(c.title) LIKE ? THEN 1 ELSE 0 END) AS title_match,
            SUM(CASE WHEN LOWER(m.content) LIKE ? THEN 1 ELSE 0 END) AS message_matches,
            COALESCE(MAX(m.created_at), c.updated_at, c.created_at) AS sort_time
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        GROUP BY c.id, c.title, c.created_at, c.updated_at
        HAVING title_match > 0 OR message_matches > 0
        ORDER BY title_match DESC, message_matches DESC, sort_time DESC
        LIMIT ?
        """,
        (like_term, like_term, like_term, limit),
    ).fetchall()

    return [
        (row["id"], row["title"], row["created_at"], row["snippet"])
        for row in rows
    ]


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


def delete_conversation(conn: sqlite3.Connection, conversation_id: str) -> bool:
    conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
    cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    conn.commit()
    return cur.rowcount > 0


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
        raise RuntimeError(
            f"Unexpected Embeddings API response format: {response_data}"
        ) from exc


def upsert_memory_embedding(
    conn: sqlite3.Connection, memory_id: int, embedding: List[float]
) -> None:
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
    content = message.content if isinstance(message.content, str) else summarize_content(message.content)
    now = now_iso()
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, message.role, content, now),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    conn.commit()


def add_message_returning_id(
    conn: sqlite3.Connection, conversation_id: str, message: Message
) -> int:
    content = message.content if isinstance(message.content, str) else summarize_content(message.content)
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, message.role, content, now),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    conn.commit()
    return int(cur.lastrowid)


def summarize_content(content: object) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "input_text":
                parts.append(str(block.get("text", "")))
            elif block_type == "input_image":
                parts.append("[image]")
        return "\n".join(part for part in parts if part).strip() or "[attachment]"

    return str(content)


def encode_file_as_data_url(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as file_obj:
        encoded = base64.b64encode(file_obj.read()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as file_obj:
        return file_obj.read()


def build_user_content(
    text: Optional[str] = None,
    image_data_urls: Optional[List[str]] = None,
    file_texts: Optional[List[Tuple[str, str]]] = None,
) -> List[dict]:
    blocks: List[dict] = []

    if text:
        blocks.append({"type": "input_text", "text": text})

    for image_data_url in image_data_urls or []:
        blocks.append({"type": "input_image", "image_url": image_data_url})

    for filename, file_text in file_texts or []:
        blocks.append(
            {
                "type": "input_text",
                "text": f"File ({filename}):\n{file_text}",
            }
        )

    return blocks


def create_user_message(
    text: Optional[str] = None,
    image_paths: Optional[List[str]] = None,
    text_file_paths: Optional[List[str]] = None,
) -> Message:
    image_data_urls = [encode_file_as_data_url(path) for path in (image_paths or [])]
    file_texts = [
        (os.path.basename(path), read_text_file(path))
        for path in (text_file_paths or [])
    ]
    return Message("user", build_user_content(text, image_data_urls, file_texts))


def get_recent_messages(conn: sqlite3.Connection, conversation_id: str) -> List[Message]:
    rows = conn.execute(
        """
        SELECT role, content FROM messages
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
        SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id
        """,
        (conversation_id,),
    ).fetchall()
    return [Message(row["role"], row["content"]) for row in rows]


def get_all_messages_with_ids(conn: sqlite3.Connection, conversation_id: str) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, role, content, created_at
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id
        """,
        (conversation_id,),
    ).fetchall()


def get_message_row(
    conn: sqlite3.Connection, conversation_id: str, message_id: int
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, role, content, created_at
        FROM messages
        WHERE conversation_id = ? AND id = ?
        LIMIT 1
        """,
        (conversation_id, message_id),
    ).fetchone()


def replace_message_from_id(
    conn: sqlite3.Connection,
    conversation_id: str,
    message_id: int,
    new_message: Message,
) -> int:
    row = get_message_row(conn, conversation_id, message_id)
    if row is None:
        raise ValueError("Message not found")
    if row["role"] != "user":
        raise ValueError("Only user messages can be edited")

    content = (
        new_message.content
        if isinstance(new_message.content, str)
        else summarize_content(new_message.content)
    )
    now = now_iso()
    conn.execute(
        "UPDATE messages SET content = ?, created_at = ? WHERE id = ?",
        (content, now, message_id),
    )
    conn.execute(
        "DELETE FROM messages WHERE conversation_id = ? AND id > ?",
        (conversation_id, message_id),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    conn.commit()
    return message_id


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return -1.0
    return dot / (mag_a * mag_b)


def find_relevant_memories(
    conn: sqlite3.Connection, query: str, top_k: int = EMBEDDINGS_TOP_K
) -> List[Tuple[int, str, str]]:
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


def call_openai(
    messages: Iterable[Message],
    web_search_mode: str = "off",
    enable_code_interpreter: bool = True,
) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")

    payload = {
        "model": MODEL,
        "input": [{"role": message.role, "content": message.content} for message in messages],
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    normalized_mode = (web_search_mode or "off").strip().lower()
    tools = []

    if WEB_SEARCH_ENABLED and normalized_mode in {"auto", "force"}:
        tool_type = WEB_SEARCH_TOOL if WEB_SEARCH_TOOL in {"web_search", "web_search_preview"} else "web_search"
        tools.append({"type": tool_type})
        if normalized_mode == "force":
            payload["tool_choice"] = "required"

    if CODE_INTERPRETER_ENABLED and enable_code_interpreter:
        tools.append(
            {
                "type": "code_interpreter",
                "container": {
                    "type": "auto",
                    "memory_limit": CODE_INTERPRETER_MEMORY_LIMIT,
                },
            }
        )

    if tools:
        payload["tools"] = tools

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

    text = extract_response_text(response_data)
    if text:
        citations = extract_container_file_citations(response_data)
        if citations:
            lines = []
            for container_id, file_id, filename in citations:
                safe_name = filename or f"file-{file_id}"
                lines.append(
                    f"- [{safe_name}](/api/container-files/{container_id}/{file_id}/{quote(safe_name)})"
                )
            text = f"{text}\n\nDownloads:\n" + "\n".join(lines)
        return text

    raise RuntimeError(f"Unexpected API response format: {response_data}")


def stream_openai(
    messages: Iterable[Message],
    web_search_mode: str = "off",
    enable_code_interpreter: bool = True,
):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")

    payload = {
        "model": MODEL,
        "input": [{"role": message.role, "content": message.content} for message in messages],
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "stream": True,
    }
    normalized_mode = (web_search_mode or "off").strip().lower()
    tools = []

    if WEB_SEARCH_ENABLED and normalized_mode in {"auto", "force"}:
        tool_type = WEB_SEARCH_TOOL if WEB_SEARCH_TOOL in {"web_search", "web_search_preview"} else "web_search"
        tools.append({"type": tool_type})
        if normalized_mode == "force":
            payload["tool_choice"] = "required"

    if CODE_INTERPRETER_ENABLED and enable_code_interpreter:
        tools.append(
            {
                "type": "code_interpreter",
                "container": {
                    "type": "auto",
                    "memory_limit": CODE_INTERPRETER_MEMORY_LIMIT,
                },
            }
        )

    if tools:
        payload["tools"] = tools

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

    text_chunks: List[str] = []
    completed_response = None

    try:
        with request.urlopen(req, timeout=180) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue

                payload_str = line[5:].strip()
                if payload_str == "[DONE]":
                    break

                try:
                    event = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")

                if event_type == "response.output_text.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        text_chunks.append(delta)
                        yield {"type": "text_delta", "delta": delta}
                elif event_type in {"response.output_item.added", "response.output_item.done"}:
                    item = event.get("item") or {}
                    item_type = str(item.get("type") or "").strip()
                    if item_type in {"code_interpreter_call", "web_search_call"}:
                        yield {"type": "status", "status": item_type}
                elif event_type == "response.completed":
                    completed_response = event.get("response")
    except error.HTTPError as http_error:
        detail = http_error.read().decode("utf-8")
        raise RuntimeError(f"OpenAI API error ({http_error.code}): {detail}") from http_error

    full_text = "".join(text_chunks).strip()
    if isinstance(completed_response, dict):
        extracted = extract_response_text(completed_response)
        if extracted:
            full_text = extracted
        citations = extract_container_file_citations(completed_response)
        if citations:
            lines = []
            for container_id, file_id, filename in citations:
                safe_name = filename or f"file-{file_id}"
                lines.append(
                    f"- [{safe_name}](/api/container-files/{container_id}/{file_id}/{quote(safe_name)})"
                )
            full_text = f"{full_text}\n\nDownloads:\n" + "\n".join(lines) if full_text else "Downloads:\n" + "\n".join(lines)

    yield {"type": "done", "text": full_text}


def call_openai_image(
    prompt: str,
    size: str = "1024x1024",
    *,
    model: Optional[str] = None,
    quality: Optional[str] = None,
    background: Optional[str] = None,
    output_format: Optional[str] = None,
) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")

    payload = {
        "model": model or IMAGE_MODEL,
        "prompt": prompt,
        "size": size,
    }

    if quality:
        payload["quality"] = quality
    if background:
        payload["background"] = background
    if output_format:
        payload["output_format"] = output_format

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        IMAGES_API_URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with request.urlopen(req, timeout=120) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as http_error:
        detail = http_error.read().decode("utf-8")
        raise RuntimeError(
            f"OpenAI Images API error ({http_error.code}): {detail}"
        ) from http_error

    data_items = response_data.get("data")
    if isinstance(data_items, list) and data_items:
        first = data_items[0]
        if isinstance(first, dict):
            b64_json = first.get("b64_json")
            if isinstance(b64_json, str) and b64_json:
                image_format = str(payload.get("output_format") or "png").strip().lower()
                if image_format == "jpg":
                    image_format = "jpeg"
                if image_format not in {"png", "webp", "jpeg"}:
                    image_format = "png"
                return f"data:image/{image_format};base64,{b64_json}"

            url_value = first.get("url")
            if isinstance(url_value, str) and url_value:
                return url_value

    raise RuntimeError(f"Unexpected Images API response format: {response_data}")


def extract_container_file_citations(response_data: dict) -> List[Tuple[str, str, str]]:
    citations: List[Tuple[str, str, str]] = []
    seen = set()

    for item in response_data.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue

        for block in item.get("content", []):
            if not isinstance(block, dict):
                continue
            for annotation in block.get("annotations", []):
                if not isinstance(annotation, dict):
                    continue
                if annotation.get("type") != "container_file_citation":
                    continue

                container_id = str(annotation.get("container_id") or "").strip()
                file_id = str(annotation.get("file_id") or "").strip()
                filename = str(annotation.get("filename") or "").strip()
                if not container_id or not file_id:
                    continue

                key = (container_id, file_id)
                if key in seen:
                    continue
                seen.add(key)
                citations.append((container_id, file_id, filename))

    return citations


def fetch_container_file_content(container_id: str, file_id: str) -> Tuple[bytes, str]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")

    url = f"https://api.openai.com/v1/containers/{container_id}/files/{file_id}/content"
    req = request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )

    try:
        with request.urlopen(req, timeout=120) as response:
            payload = response.read()
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            return payload, content_type
    except error.HTTPError as http_error:
        detail = http_error.read().decode("utf-8")
        raise RuntimeError(
            f"Container file download error ({http_error.code}): {detail}"
        ) from http_error


def extract_response_text(response_data: dict) -> str:
    output_text = response_data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    texts: List[str] = []
    for item in response_data.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        if item.get("role") != "assistant":
            continue
        for block in item.get("content", []):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in {"output_text", "text"}:
                text_value = block.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    texts.append(text_value.strip())
                elif isinstance(text_value, dict):
                    nested = text_value.get("value")
                    if isinstance(nested, str) and nested.strip():
                        texts.append(nested.strip())

    return "\n\n".join(texts).strip()


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
