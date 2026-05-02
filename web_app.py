import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import time
import shlex
import re
from typing import Optional
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from core import (
    AVAILABLE_CHAT_MODELS,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_REASONING_MODEL,
    ENV_PATH,
    Message,
    REASONING_EFFORT_OPTIONS,
    accept_memory_suggestion,
    add_memory,
    add_message,
    add_message_returning_id,
    auto_extract_memory_suggestions_from_user_text,
    build_system_prompt,
    build_replay_history_from_rows,
    build_user_content,
    call_openai,
    call_openai_image,
    clear_memories,
    cleanup_orphaned_attachments,
    connect_db,
    conversation_exists,
    create_conversation,
    delete_conversation,
    delete_memory,
    delete_memory_suggestion,
    delete_memory_suggestions_from_message_id,
    deserialize_message_metadata,
    extract_message_attachment_cards,
    fetch_container_file_content,
    get_all_messages_with_ids,
    get_conversation_title,
    get_message_file_search_status,
    get_message_row,
    get_previous_user_message_row,
    get_recent_messages,
    get_recent_message_rows_with_ids,
    infer_inspected_attachment_message_ids,
    init_db,
    list_conversations,
    list_conversation_folders,
    list_folder_conversations,
    list_memories,
    list_memory_suggestions,
    load_env_file,
    message_from_row,
    normalize_chat_model,
    reasoning_model_supported,
    raw_content_has_inspectable_attachments,
    replace_message_from_id,
    resolve_chat_settings,
    search_conversations,
    stream_openai,
    summarize_content,
    update_memory_suggestion,
    update_conversation_pinned,
    update_conversation_title,
    create_conversation_folder,
    assign_conversation_to_folder,
    update_folder_pinned,
    delete_conversation_folder,
)

load_env_file(ENV_PATH)

HOST = os.environ.get("CHATBOT_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("CHATBOT_WEB_PORT", "8000"))

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

MAX_BODY_SIZE = 30 * 1024 * 1024
MAX_ATTACHMENTS = 8
MAX_TEXT_ATTACHMENT_CHARS = 200_000
MAX_BINARY_ATTACHMENT_BYTES = 20 * 1024 * 1024

WEB_PASSWORD = os.environ.get("CHATBOT_WEB_PASSWORD", "").strip()
WEB_PASSWORD_HASH = os.environ.get("CHATBOT_WEB_PASSWORD_HASH", "").strip().lower()
WEB_SESSION_SECRET = os.environ.get("CHATBOT_WEB_SESSION_SECRET", "").strip()
WEB_SESSION_TTL = int(os.environ.get("CHATBOT_WEB_SESSION_TTL", "1209600"))
WEB_COOKIE_NAME = os.environ.get("CHATBOT_WEB_COOKIE_NAME", "chatbot_session")
WEB_COOKIE_SECURE = os.environ.get("CHATBOT_WEB_COOKIE_SECURE", "0").strip() == "1"

logger = logging.getLogger(__name__)

IMAGE_COMMAND_ALLOWED_MODELS = {
    "gpt-image-1.5",
    "gpt-image-1",
    "gpt-image-1-mini",
    "dall-e-2",
    "dall-e-3",
}

IMAGE_COMMAND_ALLOWED_SIZES = {
    "1024x1024",
    "1024x1536",
    "1536x1024",
    "auto",
}

IMAGE_COMMAND_ALLOWED_QUALITIES = {
    "low",
    "medium",
    "high",
    "auto",
}

IMAGE_COMMAND_ALLOWED_BACKGROUNDS = {
    "transparent",
    "opaque",
    "auto",
}

IMAGE_COMMAND_ALLOWED_OUTPUT_FORMATS = {
    "png",
    "webp",
    "jpeg",
}


def parse_image_command(raw_command: str) -> tuple[str, dict]:
    tokens = shlex.split(raw_command or "")
    if not tokens:
        raise ValueError("Missing image prompt")

    option_aliases = {
        "model": "model",
        "size": "size",
        "quality": "quality",
        "background": "background",
        "output_format": "output_format",
        "format": "output_format",
        "fmt": "output_format",
    }

    options: dict[str, str] = {}
    prompt_tokens: list[str] = []
    parsing_options = True

    for token in tokens:
        if parsing_options and "=" in token:
            key, value = token.split("=", 1)
            normalized_key = option_aliases.get(key.strip().lower())
            if normalized_key:
                cleaned_value = value.strip()
                if not cleaned_value:
                    raise ValueError(f"Missing value for {key.strip()}")
                options[normalized_key] = cleaned_value
                continue
        parsing_options = False
        prompt_tokens.append(token)

    prompt = " ".join(prompt_tokens).strip()
    if not prompt:
        raise ValueError("Missing image prompt")

    if "model" in options:
        model = options["model"].strip().lower()
        if model not in IMAGE_COMMAND_ALLOWED_MODELS:
            raise ValueError(
                "Invalid image model. Allowed values: "
                + ", ".join(sorted(IMAGE_COMMAND_ALLOWED_MODELS))
            )
        options["model"] = model

    if "size" in options:
        size = options["size"].strip().lower()
        if size not in IMAGE_COMMAND_ALLOWED_SIZES:
            raise ValueError(
                "Invalid image size. Allowed values: "
                + ", ".join(sorted(IMAGE_COMMAND_ALLOWED_SIZES))
            )
        options["size"] = size

    if "quality" in options:
        quality = options["quality"].strip().lower()
        if quality not in IMAGE_COMMAND_ALLOWED_QUALITIES:
            raise ValueError(
                "Invalid image quality. Allowed values: "
                + ", ".join(sorted(IMAGE_COMMAND_ALLOWED_QUALITIES))
            )
        options["quality"] = quality

    if "background" in options:
        background = options["background"].strip().lower()
        if background not in IMAGE_COMMAND_ALLOWED_BACKGROUNDS:
            raise ValueError(
                "Invalid image background. Allowed values: "
                + ", ".join(sorted(IMAGE_COMMAND_ALLOWED_BACKGROUNDS))
            )
        options["background"] = background

    if "output_format" in options:
        output_format = options["output_format"].strip().lower()
        if output_format == "jpg":
            output_format = "jpeg"
        if output_format not in IMAGE_COMMAND_ALLOWED_OUTPUT_FORMATS:
            raise ValueError(
                "Invalid image output_format. Allowed values: "
                + ", ".join(sorted(IMAGE_COMMAND_ALLOWED_OUTPUT_FORMATS))
            )
        options["output_format"] = output_format

    return prompt, options


def parse_data_url(data_url: str) -> tuple[str, str]:
    if not isinstance(data_url, str) or not data_url.startswith("data:") or "," not in data_url:
        raise ValueError("Invalid data URL")

    header, encoded = data_url.split(",", 1)
    mime_type = header[5:].split(";", 1)[0].strip() or "application/octet-stream"
    if ";base64" not in header.lower():
        raise ValueError("Only base64 data URLs are supported")

    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Attachment is not valid base64") from exc

    return mime_type, base64.b64encode(raw).decode("ascii")

def auth_enabled() -> bool:
    return bool(WEB_PASSWORD or WEB_PASSWORD_HASH)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def password_matches(candidate: str) -> bool:
    if not auth_enabled():
        return True

    candidate_hash = sha256_hex(candidate)

    if WEB_PASSWORD_HASH:
        return hmac.compare_digest(candidate_hash, WEB_PASSWORD_HASH)

    return hmac.compare_digest(candidate_hash, sha256_hex(WEB_PASSWORD))


def get_session_secret() -> str:
    # If not explicitly set, generate one for this process.
    # Sessions will be invalidated on restart unless CHATBOT_WEB_SESSION_SECRET is set.
    if WEB_SESSION_SECRET:
        return WEB_SESSION_SECRET
    return _RUNTIME_SESSION_SECRET


_RUNTIME_SESSION_SECRET = secrets.token_hex(32)


def make_session_token(expires_at: int) -> str:
    payload = str(expires_at).encode("utf-8")
    signature = hmac.new(
        get_session_secret().encode("utf-8"),
        payload,
        hashlib.sha256,
    ).digest()
    signature_b64 = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{expires_at}.{signature_b64}"


def verify_session_token(token: str) -> bool:
    if not token or "." not in token:
        return False

    expires_str, signature_b64 = token.split(".", 1)

    try:
        expires_at = int(expires_str)
    except ValueError:
        return False

    if expires_at < int(time.time()):
        return False

    expected = make_session_token(expires_at)
    return hmac.compare_digest(token, expected)


class ApiError(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.message = message
        self.status = status


class ChatHandler(BaseHTTPRequestHandler):
    server_version = "ChatServer/1.1"

    def log_message(self, format: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    def _send_bytes(self, payload: bytes, content_type: str, status: HTTPStatus, extra_headers=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'",
        )

        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)

        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK, extra_headers=None) -> None:
        payload = json.dumps(data).encode("utf-8")
        self._send_bytes(payload, "application/json; charset=utf-8", status, extra_headers=extra_headers)

    def _send_sse_headers(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def _send_sse_event(self, data: dict) -> None:
        chunk = f"data: {json.dumps(data)}\n\n".encode("utf-8")
        self.wfile.write(chunk)
        self.wfile.flush()

    def _send_text(self, text: str, status: HTTPStatus = HTTPStatus.OK, extra_headers=None) -> None:
        payload = text.encode("utf-8")
        self._send_bytes(payload, "text/plain; charset=utf-8", status, extra_headers=extra_headers)

    def _send_redirect(self, location: str, extra_headers=None) -> None:
        headers = {"Location": location}
        if extra_headers:
            headers.update(extra_headers)
        self._send_bytes(b"", "text/plain; charset=utf-8", HTTPStatus.SEE_OTHER, extra_headers=headers)

    def _send_api_error(self, message: str, status: HTTPStatus) -> None:
        self._send_json({"error": message}, status)

    def _send_file(self, path: Path, status: HTTPStatus = HTTPStatus.OK) -> None:
        if not path.exists() or not path.is_file():
            self._send_api_error("Not found", HTTPStatus.NOT_FOUND)
            return

        payload = path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(path))

        if not content_type:
            content_type = "application/octet-stream"
        elif content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type = f"{content_type}; charset=utf-8"

        self._send_bytes(payload, content_type, status)

    def _path_parts(self) -> list[str]:
        return [part for part in urlparse(self.path).path.split("/") if part]

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError("Invalid Content-Length", HTTPStatus.BAD_REQUEST) from exc

        if length <= 0:
            return {}
        if length > MAX_BODY_SIZE:
            raise ApiError("Request body too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ApiError("Request body must be UTF-8", HTTPStatus.BAD_REQUEST) from exc
        except json.JSONDecodeError as exc:
            raise ApiError("Malformed JSON", HTTPStatus.BAD_REQUEST) from exc

    def _build_cookie_header(self, name: str, value: str, max_age=None, expires=None) -> str:
        cookie = SimpleCookie()
        cookie[name] = value
        morsel = cookie[name]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = "Lax"

        if WEB_COOKIE_SECURE:
            morsel["secure"] = True

        if max_age is not None:
            morsel["max-age"] = str(max_age)

        if expires is not None:
            morsel["expires"] = expires

        return morsel.OutputString()

    def _get_cookie(self, name: str) -> str:
        raw_cookie = self.headers.get("Cookie", "")
        if not raw_cookie:
            return ""

        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            return ""

        morsel = cookie.get(name)
        return morsel.value if morsel else ""

    def _is_authenticated(self) -> bool:
        if not auth_enabled():
            return True

        token = self._get_cookie(WEB_COOKIE_NAME)
        return verify_session_token(token)

    def _require_auth(self) -> bool:
        if self._is_authenticated():
            return True

        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._send_api_error("Authentication required", HTTPStatus.UNAUTHORIZED)
            return False

        self._send_redirect("/login")
        return False

    def _validate_attachments(self, attachments: list[dict]) -> list[dict]:
        if not isinstance(attachments, list):
            raise ApiError("attachments must be a list")

        if len(attachments) > MAX_ATTACHMENTS:
            raise ApiError(f"Too many attachments (max {MAX_ATTACHMENTS})")

        validated: list[dict] = []

        for idx, item in enumerate(attachments):
            if not isinstance(item, dict):
                raise ApiError(f"Attachment #{idx + 1} must be an object")

            kind = item.get("kind")
            name = str(item.get("name") or "file")

            if kind == "image":
                data_url = item.get("data_url")
                if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
                    raise ApiError(f"Attachment '{name}' is not a valid image data URL")
                validated.append({"kind": "image", "name": name, "data_url": data_url})

            elif kind == "text":
                text = item.get("text")
                if not isinstance(text, str):
                    raise ApiError(f"Attachment '{name}' must contain text")
                if len(text) > MAX_TEXT_ATTACHMENT_CHARS:
                    raise ApiError(
                        f"Attachment '{name}' is too large "
                        f"(max {MAX_TEXT_ATTACHMENT_CHARS} chars)"
                    )
                validated.append({"kind": "text", "name": name, "text": text})

            elif kind == "file":
                data_url = item.get("data_url")
                if not isinstance(data_url, str):
                    raise ApiError(f"Attachment '{name}' must contain a data URL")
                try:
                    mime_type, file_data = parse_data_url(data_url)
                except ValueError as exc:
                    raise ApiError(f"Attachment '{name}' is not a valid file upload") from exc
                if mime_type != "application/pdf" and not name.lower().endswith(".pdf"):
                    raise ApiError(f"Attachment '{name}' must be a PDF")
                binary_size = (len(file_data) * 3) // 4
                if binary_size > MAX_BINARY_ATTACHMENT_BYTES:
                    raise ApiError(
                        f"Attachment '{name}' is too large "
                        f"(max {MAX_BINARY_ATTACHMENT_BYTES // (1024 * 1024)} MB)"
                    )
                validated.append(
                    {
                        "kind": "file",
                        "name": name,
                        "file_data": file_data,
                        "mime_type": mime_type,
                    }
                )

            else:
                raise ApiError(f"Unsupported attachment kind for '{name}'")

        return validated

    def _build_user_message(self, content: str, attachments: list[dict]) -> Message:
        image_data_urls = [
            attachment["data_url"]
            for attachment in attachments
            if attachment["kind"] == "image"
        ]
        file_texts = [
            (attachment["name"], attachment["text"])
            for attachment in attachments
            if attachment["kind"] == "text"
        ]
        file_inputs = [
            {
                "filename": attachment["name"],
                "file_data": attachment["file_data"],
            }
            for attachment in attachments
            if attachment["kind"] == "file"
        ]
        user_content = build_user_content(content or None, image_data_urls, file_texts, file_inputs)
        return Message("user", user_content)
    
    def _list_conversations(self, query: str) -> dict:
        with connect_db() as conn:
            if query:
                conversations = [
                    {
                        "id": convo_id,
                        "title": title,
                        "created_at": created_at,
                        "snippet": snippet,
                        "pinned": pinned,
                    }
                    for convo_id, title, created_at, snippet, pinned in search_conversations(conn, query)
                ]
            else:
                conversations = [
                    {
                        "id": convo_id,
                        "title": title,
                        "created_at": created_at,
                        "pinned": pinned,
                    }
                    for convo_id, title, created_at, pinned in list_conversations(conn)
                ]

            folders = list_conversation_folders(conn)
        return {"conversations": conversations, "folders": folders}

    def _handle_create_folder(self, payload: dict) -> dict:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ApiError("Folder name required")
        with connect_db() as conn:
            folder_id = create_conversation_folder(conn, name)
        return {"id": folder_id, "name": name}

    def _handle_pin_folder(self, folder_id: str, payload: dict) -> dict:
        pinned = bool(payload.get("pinned", False))
        with connect_db() as conn:
            updated = update_folder_pinned(conn, folder_id, pinned)
        return {"updated": updated, "pinned": pinned}

    def _handle_add_conversation_to_folder(self, folder_id: str, payload: dict) -> dict:
        conversation_id = str(payload.get("conversation_id") or "").strip()
        if not conversation_id:
            raise ApiError("conversation_id required")
        with connect_db() as conn:
            assign_conversation_to_folder(conn, folder_id, conversation_id)
        return {"status": "ok"}

    def _get_folder_conversations(self, folder_id: str) -> dict:
        with connect_db() as conn:
            conversations = list_folder_conversations(conn, folder_id)
        return {"conversations": conversations}

    def _handle_delete_folder(self, folder_id: str) -> dict:
        with connect_db() as conn:
            deleted = delete_conversation_folder(conn, folder_id)
        return {"deleted": deleted}

    def _get_conversation_messages(self, conversation_id: str) -> dict:
        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            title = get_conversation_title(conn, conversation_id)
            messages = []
            rows = get_all_messages_with_ids(conn, conversation_id)
            inspected_attachment_message_ids = set()
            for row in rows:
                metadata = deserialize_message_metadata(row["metadata"])
                for raw_id in list(metadata.get("inspected_attachment_message_ids") or []):
                    try:
                        message_id = int(raw_id)
                    except (TypeError, ValueError):
                        continue
                    if message_id > 0:
                        inspected_attachment_message_ids.add(message_id)

            for row in rows:
                message = message_from_row(row)
                metadata = message.metadata or {}
                has_inspectable_attachments = raw_content_has_inspectable_attachments(
                    row["raw_content"],
                    row["content"],
                )
                attachment_status = None
                if row["role"] == "user" and has_inspectable_attachments:
                    inspected = int(row["id"]) in inspected_attachment_message_ids
                    attachment_status = {
                        "attached": True,
                        "inspected": inspected,
                        "suppressed_on_followups": inspected,
                        "reanalyze_available": True,
                    }
                messages.append(
                    {
                        "id": row["id"],
                        "role": row["role"],
                        "content": row["content"],
                        "tools_used": list(metadata.get("tools_used") or []),
                        "activity_log": list(metadata.get("activity_log") or []),
                        "model": str(metadata.get("model") or "").strip(),
                        "requested_model": str(metadata.get("requested_model") or "").strip(),
                        "reasoning_enabled": bool(metadata.get("reasoning_enabled", False)),
                        "reasoning_effort": str(metadata.get("reasoning_effort") or "").strip(),
                        "requested_reasoning_effort": str(
                            metadata.get("requested_reasoning_effort") or ""
                        ).strip(),
                        "has_inspectable_attachments": has_inspectable_attachments,
                        "attachments": extract_message_attachment_cards(message.content),
                        "attachment_status": attachment_status,
                        "file_search_status": get_message_file_search_status(conn, row["raw_content"]),
                        "created_at": row["created_at"],
                    }
                )

        return {"title": title, "messages": messages}
    
    def _safe_export_basename(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
        cleaned = cleaned.strip(" .-_")
        return cleaned or "conversation"

    def _render_conversation_markdown(self, title: str, messages: list[dict]) -> str:
        lines = [f"# {title or 'Untitled conversation'}", ""]

        for message in messages:
            role = str(message.get("role") or "unknown").strip().title()
            created_at = str(message.get("created_at") or "").strip()
            content = str(message.get("content") or "").rstrip()

            lines.append(f"## {role}")
            if created_at:
                lines.append(f"_Created: {created_at}_")
                lines.append("")
            lines.append(content or "(empty)")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _handle_export_conversation(self, conversation_id: str, export_format: str) -> None:
        normalized_format = (export_format or "md").strip().lower()

        if normalized_format not in {"md", "markdown", "json"}:
            raise ApiError("Invalid export format", HTTPStatus.BAD_REQUEST)

        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            title = get_conversation_title(conn, conversation_id) or "Untitled conversation"
            messages = [
                {
                    "id": row["id"],
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                }
                for row in get_all_messages_with_ids(conn, conversation_id)
            ]

        base_name = self._safe_export_basename(title)

        if normalized_format in {"md", "markdown"}:
            payload = self._render_conversation_markdown(title, messages).encode("utf-8")
            self._send_bytes(
                payload,
                "text/markdown; charset=utf-8",
                HTTPStatus.OK,
                extra_headers={
                    "Content-Disposition": f'attachment; filename="{base_name}.md"',
                    "Cache-Control": "no-store",
                },
            )
            return

        payload = json.dumps(
            {
                "id": conversation_id,
                "title": title,
                "messages": messages,
            },
            indent=2,
        ).encode("utf-8")

        self._send_bytes(
            payload,
            "application/json; charset=utf-8",
            HTTPStatus.OK,
            extra_headers={
                "Content-Disposition": f'attachment; filename="{base_name}.json"',
                "Cache-Control": "no-store",
            },
        )

    def _list_memories(self) -> dict:
        with connect_db() as conn:
            memories = [
                {
                    "id": memory.id,
                    "content": memory.content,
                    "kind": memory.kind,
                    "scope": memory.scope,
                    "scope_key": memory.scope_key,
                    "source": memory.source,
                    "confidence": memory.confidence,
                    "pinned": memory.pinned,
                    "created_at": memory.created_at,
                    "updated_at": memory.updated_at,
                }
                for memory in list_memories(conn)
            ]
            suggestions = [
                {
                    "id": suggestion.id,
                    "content": suggestion.content,
                    "kind": suggestion.kind,
                    "scope": suggestion.scope,
                    "scope_key": suggestion.scope_key,
                    "source_message_id": suggestion.source_message_id,
                    "source": suggestion.source,
                    "confidence": suggestion.confidence,
                    "pinned": suggestion.pinned,
                    "created_at": suggestion.created_at,
                    "updated_at": suggestion.updated_at,
                }
                for suggestion in list_memory_suggestions(conn)
            ]
        return {"memories": memories, "suggestions": suggestions}

    def _get_client_settings(self) -> dict:
        models = []
        for model in AVAILABLE_CHAT_MODELS:
            models.append(
                {
                    "id": model,
                    "label": model,
                    "supports_reasoning": reasoning_model_supported(model),
                }
            )
        return {
            "chat_models": models,
            "default_model": normalize_chat_model(None),
            "default_reasoning_model": DEFAULT_REASONING_MODEL,
            "default_reasoning_effort": DEFAULT_REASONING_EFFORT,
            "reasoning_efforts": list(REASONING_EFFORT_OPTIONS),
        }

    def _resolve_chat_options(
        self,
        payload: dict,
        prompt_text: str = "",
        attachment_count: int = 0,
    ) -> dict:
        return resolve_chat_settings(
            payload.get("model"),
            enable_reasoning=bool(payload.get("enable_reasoning", False)),
            reasoning_effort=payload.get("reasoning_effort"),
            prompt_text=prompt_text,
            attachment_count=attachment_count,
        )

    def _get_regenerate_instruction(self, payload: dict) -> str:
        mode = str(payload.get("regenerate_mode") or "same").strip().lower()
        if mode == "concise":
            return "For this regeneration, answer more concisely while preserving the useful substance."
        if mode == "detailed":
            return "For this regeneration, provide a more detailed answer with clearer reasoning and examples where useful."
        if mode == "higher_reasoning":
            return "For this regeneration, spend extra effort checking the answer and resolving tricky parts carefully."
        return ""

    def _parse_reinspect_message_ids(self, payload: dict) -> list[int]:
        parsed: list[int] = []
        seen = set()
        for raw_id in list(payload.get("reinspect_message_ids") or []):
            try:
                message_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if message_id <= 0 or message_id in seen:
                continue
            seen.add(message_id)
            parsed.append(message_id)
        return parsed

    def _assistant_metadata(self, response_text, chat_options: dict, inspected_attachment_message_ids: Optional[list[int]] = None) -> dict:
        metadata = {
            "tools_used": response_text.tools_used,
            "activity_log": response_text.activity_log,
            "model": response_text.model,
            "requested_model": chat_options.get("requested_model") or response_text.model,
            "reasoning_enabled": bool(chat_options.get("reasoning_enabled")),
            "reasoning_effort": response_text.reasoning_effort,
            "requested_reasoning_effort": chat_options.get("requested_reasoning_effort"),
        }
        if inspected_attachment_message_ids:
            metadata["inspected_attachment_message_ids"] = [
                int(message_id)
                for message_id in inspected_attachment_message_ids
                if int(message_id) > 0
            ]
        return metadata

    def _handle_create_conversation(self) -> dict:
        with connect_db() as conn:
            conversation_id = create_conversation(conn)
        return {"id": conversation_id}

    def _handle_add_memory(self, payload: dict) -> dict:
        content = (payload.get("content") or "").strip()
        if not content:
            raise ApiError("Missing memory content")

        kind = (payload.get("kind") or "note").strip().lower()
        scope = (payload.get("scope") or "global").strip().lower()
        source = (payload.get("source") or "user").strip().lower()
        pinned = bool(payload.get("pinned", False))
        confidence = payload.get("confidence", 1.0)
        conversation_id = (payload.get("conversation_id") or "").strip()
        scope_key = conversation_id if scope == "conversation" else (payload.get("scope_key") or "").strip()

        if scope == "conversation" and not scope_key:
            raise ApiError("Conversation-scoped memories require a conversation_id")

        with connect_db() as conn:
            if scope == "conversation" and not conversation_exists(conn, scope_key):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)
            add_memory(
                conn,
                content,
                kind=kind,
                scope=scope,
                scope_key=scope_key,
                source=source,
                confidence=confidence,
                pinned=pinned,
            )

        return {"status": "ok"}

    def _handle_accept_memory_suggestion(self, suggestion_id_str: str) -> dict:
        try:
            suggestion_id = int(suggestion_id_str)
        except ValueError as exc:
            raise ApiError("Invalid suggestion id") from exc

        with connect_db() as conn:
            accepted = accept_memory_suggestion(conn, suggestion_id)
        if accepted is None:
            raise ApiError("Suggestion not found", HTTPStatus.NOT_FOUND)

        return {
            "status": "ok",
            "memory": {
                "id": accepted.id,
                "content": accepted.content,
                "kind": accepted.kind,
                "scope": accepted.scope,
                "scope_key": accepted.scope_key,
                "source": accepted.source,
                "confidence": accepted.confidence,
                "pinned": accepted.pinned,
                "created_at": accepted.created_at,
                "updated_at": accepted.updated_at,
            },
        }

    def _handle_delete_memory_suggestion(self, suggestion_id_str: str) -> dict:
        try:
            suggestion_id = int(suggestion_id_str)
        except ValueError as exc:
            raise ApiError("Invalid suggestion id") from exc

        with connect_db() as conn:
            deleted = delete_memory_suggestion(conn, suggestion_id)
        return {"deleted": deleted}

    def _handle_update_memory_suggestion(self, suggestion_id_str: str, payload: dict) -> dict:
        try:
            suggestion_id = int(suggestion_id_str)
        except ValueError as exc:
            raise ApiError("Invalid suggestion id") from exc

        content = re.sub(r"\s+", " ", str(payload.get("content") or "")).strip()
        if not content:
            raise ApiError("Missing suggestion content")

        kind = (payload.get("kind") or "note").strip().lower()
        scope = (payload.get("scope") or "global").strip().lower()
        pinned = bool(payload.get("pinned", False))
        conversation_id = (payload.get("conversation_id") or "").strip()

        with connect_db() as conn:
            existing = next((item for item in list_memory_suggestions(conn) if item.id == suggestion_id), None)
            if existing is None:
                raise ApiError("Suggestion not found", HTTPStatus.NOT_FOUND)

            scope_key = ""
            if scope == "conversation":
                scope_key = conversation_id or existing.scope_key
                if not scope_key:
                    raise ApiError("Conversation-scoped suggestions require a conversation_id")
                if not conversation_exists(conn, scope_key):
                    raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            updated = update_memory_suggestion(
                conn,
                suggestion_id,
                content=content,
                kind=kind,
                scope=scope,
                scope_key=scope_key,
                pinned=pinned,
            )

        if updated is None:
            raise ApiError("Suggestion not found", HTTPStatus.NOT_FOUND)

        return {
            "status": "ok",
            "suggestion": {
                "id": updated.id,
                "content": updated.content,
                "kind": updated.kind,
                "scope": updated.scope,
                "scope_key": updated.scope_key,
                "source_message_id": updated.source_message_id,
                "source": updated.source,
                "confidence": updated.confidence,
                "pinned": updated.pinned,
                "created_at": updated.created_at,
                "updated_at": updated.updated_at,
            },
        }

    def _handle_update_title(self, payload: dict) -> dict:
        conversation_id = payload.get("conversation_id")
        title = (payload.get("title") or "").strip()

        if not conversation_id:
            raise ApiError("Missing conversation_id")
        if not title:
            raise ApiError("Missing title")

        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            updated = update_conversation_title(conn, conversation_id, title)

        return {"updated": updated}

    def _handle_send_message(self, payload: dict) -> dict:
        conversation_id = payload.get("conversation_id")
        content = (payload.get("content") or "").strip()
        attachments = self._validate_attachments(payload.get("attachments") or [])
        enable_edit_branching = bool(payload.get("enable_edit_branching", True))

        if not conversation_id:
            raise ApiError("Missing conversation_id")
        if not content and not attachments:
            raise ApiError("Message content or attachments required")

        if content.lower().startswith("/title"):
            new_title = content[len("/title"):].strip()
            if not new_title:
                raise ApiError("Missing title text")

            with connect_db() as conn:
                updated = update_conversation_title(conn, conversation_id, new_title)

            return {
                "status": "ok",
                "command": "title",
                "updated": updated,
                "title": new_title,
            }

        if content.lower().startswith("/image ") and enable_edit_branching:
            raw_image_command = content[7:].strip()
            with connect_db() as conn:
                if not conversation_exists(conn, conversation_id):
                    raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)
                try:
                    image_prompt, image_options = parse_image_command(raw_image_command)
                    add_message(conn, conversation_id, Message("user", content))
                    image_url = call_openai_image(image_prompt, **image_options)
                except ValueError as exc:
                    raise ApiError(str(exc), HTTPStatus.BAD_REQUEST) from exc
                except Exception as exc:
                    logger.exception("Image generation failed for conversation %s", conversation_id)
                    raise ApiError(
                        "Image generation failed", HTTPStatus.INTERNAL_SERVER_ERROR
                    ) from exc
                assistant_text = f"Generated image:\n\n![Generated image]({image_url})"
                add_message(conn, conversation_id, Message("assistant", assistant_text))
            return {
                "status": "ok",
                "assistant_message": {
                    "role": "assistant",
                    "content": assistant_text,
                },
            }

        web_search_mode = "off"
        enable_code_interpreter = bool(payload.get("enable_code_interpreter", True))
        chat_options = self._resolve_chat_options(payload, content, len(attachments))
        reinspect_message_ids = self._parse_reinspect_message_ids(payload)
        if content.lower().startswith("/web "):
            web_search_mode = "force"
            content = content[5:].strip()
        elif bool(payload.get("enable_web_search", True)):
            web_search_mode = "auto"

        user_message = self._build_user_message(content, attachments)
        if reinspect_message_ids:
            user_message.metadata = {"reinspect_message_ids": reinspect_message_ids}

        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            history_rows = get_recent_message_rows_with_ids(conn, conversation_id)
            history = build_replay_history_from_rows(
                history_rows,
                query=content or "Attachment upload",
                current_user_message=user_message,
                reinspect_message_ids=reinspect_message_ids,
            )
            system_prompt = build_system_prompt(
                conn,
                content or "Attachment upload",
                conversation_id=conversation_id,
                current_user_message=user_message,
            )
            messages = [Message("system", system_prompt), *history, user_message]

            user_message_id = add_message_returning_id(conn, conversation_id, user_message)
            auto_extract_memory_suggestions_from_user_text(
                conn,
                content,
                conversation_id=conversation_id,
                source_message_id=user_message_id,
            )

            try:
                response_text = call_openai(
                    messages,
                    web_search_mode=web_search_mode,
                    enable_code_interpreter=enable_code_interpreter,
                    model=chat_options["model"],
                    reasoning_effort=chat_options["reasoning_effort"],
                )
            except Exception as exc:
                logger.exception("Model call failed for conversation %s", conversation_id)
                raise ApiError(
                    "Assistant request failed",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc

            inspected_attachment_message_ids = infer_inspected_attachment_message_ids(
                history_rows,
                current_user_message_id=user_message_id,
                current_user_message=user_message,
                tools_used=response_text.tools_used,
                response_text=response_text.text,
            )

            add_message(
                conn,
                conversation_id,
                Message(
                    "assistant",
                    response_text.text,
                    self._assistant_metadata(
                        response_text,
                        chat_options,
                        inspected_attachment_message_ids,
                    ),
                ),
            )

        return {
            "status": "ok",
            "assistant_message": {
                "role": "assistant",
                "content": response_text.text,
                "tools_used": response_text.tools_used,
                "activity_log": response_text.activity_log,
                "model": response_text.model,
                "requested_model": chat_options["requested_model"],
                "reasoning_enabled": chat_options["reasoning_enabled"],
                "reasoning_effort": response_text.reasoning_effort,
                "requested_reasoning_effort": chat_options["requested_reasoning_effort"],
            },
        }

    def _handle_send_message_stream(self, payload: dict) -> None:
        conversation_id = payload.get("conversation_id")
        content = (payload.get("content") or "").strip()
        attachments = self._validate_attachments(payload.get("attachments") or [])

        if not conversation_id:
            raise ApiError("Missing conversation_id")
        if not content and not attachments:
            raise ApiError("Message content or attachments required")

        if content.lower().startswith("/title") or content.lower().startswith("/image "):
            # Fallback to non-stream path for command-like requests.
            result = self._handle_send_message(payload)
            self._send_sse_headers()
            self._send_sse_event({"type": "done", "assistant_message": result.get("assistant_message", {})})
            return

        web_search_mode = "off"
        enable_code_interpreter = bool(payload.get("enable_code_interpreter", True))
        chat_options = self._resolve_chat_options(payload, content, len(attachments))
        reinspect_message_ids = self._parse_reinspect_message_ids(payload)
        if content.lower().startswith("/web "):
            web_search_mode = "force"
            content = content[5:].strip()
        elif bool(payload.get("enable_web_search", True)):
            web_search_mode = "auto"

        user_message = self._build_user_message(content, attachments)
        if reinspect_message_ids:
            user_message.metadata = {"reinspect_message_ids": reinspect_message_ids}

        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            history_rows = get_recent_message_rows_with_ids(conn, conversation_id)
            history = build_replay_history_from_rows(
                history_rows,
                query=content or "Attachment upload",
                current_user_message=user_message,
                reinspect_message_ids=reinspect_message_ids,
            )
            system_prompt = build_system_prompt(
                conn,
                content or "Attachment upload",
                conversation_id=conversation_id,
                current_user_message=user_message,
            )
            messages = [Message("system", system_prompt), *history, user_message]
            user_message_id = add_message_returning_id(conn, conversation_id, user_message)
            auto_extract_memory_suggestions_from_user_text(
                conn,
                content,
                conversation_id=conversation_id,
                source_message_id=user_message_id,
            )

            self._send_sse_headers()

            if chat_options["reasoning_enabled"] and web_search_mode != "off":
                try:
                    self._send_sse_event({"type": "status", "status": "reasoning"})
                    response_text = call_openai(
                        messages,
                        web_search_mode=web_search_mode,
                        enable_code_interpreter=enable_code_interpreter,
                        model=chat_options["model"],
                        reasoning_effort=chat_options["reasoning_effort"],
                    )
                    inspected_attachment_message_ids = infer_inspected_attachment_message_ids(
                        history_rows,
                        current_user_message_id=user_message_id,
                        current_user_message=user_message,
                        tools_used=response_text.tools_used,
                        response_text=response_text.text,
                    )
                    add_message(
                        conn,
                        conversation_id,
                        Message(
                            "assistant",
                            response_text.text,
                            {
                                "tools_used": response_text.tools_used,
                                "activity_log": response_text.activity_log,
                                "model": response_text.model,
                                "requested_model": chat_options["requested_model"],
                                "reasoning_enabled": chat_options["reasoning_enabled"],
                                "reasoning_effort": response_text.reasoning_effort,
                                "requested_reasoning_effort": chat_options["requested_reasoning_effort"],
                                "inspected_attachment_message_ids": inspected_attachment_message_ids,
                            },
                        ),
                    )
                    self._send_sse_event(
                        {
                            "type": "done",
                            "assistant_message": {
                                "role": "assistant",
                                "content": response_text.text,
                                "tools_used": response_text.tools_used,
                                "activity_log": response_text.activity_log,
                                "model": response_text.model,
                                "requested_model": chat_options["requested_model"],
                                "reasoning_enabled": chat_options["reasoning_enabled"],
                                "reasoning_effort": response_text.reasoning_effort,
                                "requested_reasoning_effort": chat_options["requested_reasoning_effort"],
                            },
                        }
                    )
                except Exception:
                    logger.exception(
                        "Reasoning send fallback failed for conversation %s",
                        conversation_id,
                    )
                    self._send_sse_event({"type": "error", "error": "Assistant request failed"})
                return

            full_text = ""
            try:
                for event in stream_openai(
                    messages,
                    web_search_mode=web_search_mode,
                    enable_code_interpreter=enable_code_interpreter,
                    model=chat_options["model"],
                    reasoning_effort=chat_options["reasoning_effort"],
                ):
                    event_type = event.get("type")
                    if event_type == "text_delta":
                        delta = str(event.get("delta") or "")
                        if not delta:
                            continue
                        full_text += delta
                        self._send_sse_event({"type": "delta", "delta": delta})
                    elif event_type == "status":
                        status = str(event.get("status") or "").strip()
                        if status:
                            self._send_sse_event({"type": "status", "status": status})
                    elif event_type == "activity":
                        activity = event.get("activity")
                        if isinstance(activity, dict):
                            self._send_sse_event({"type": "activity", "activity": activity})
                    elif event_type == "done":
                        final_text = str(event.get("text") or full_text)
                        tools_used = list(event.get("tools_used") or [])
                        activity_log = list(event.get("activity_log") or [])
                        resolved_model = str(event.get("model") or chat_options["model"]).strip() or chat_options["model"]
                        response_reasoning_effort = str(
                            event.get("reasoning_effort") or chat_options["reasoning_effort"] or ""
                        ).strip() or None
                        inspected_attachment_message_ids = infer_inspected_attachment_message_ids(
                            history_rows,
                            current_user_message_id=user_message_id,
                            current_user_message=user_message,
                            tools_used=tools_used,
                            response_text=final_text,
                        )
                        add_message(
                            conn,
                            conversation_id,
                            Message(
                                "assistant",
                                final_text,
                                {
                                    "tools_used": tools_used,
                                    "activity_log": activity_log,
                                    "model": resolved_model,
                                    "requested_model": chat_options["requested_model"],
                                    "reasoning_enabled": chat_options["reasoning_enabled"],
                                    "reasoning_effort": response_reasoning_effort,
                                    "requested_reasoning_effort": chat_options["requested_reasoning_effort"],
                                    "inspected_attachment_message_ids": inspected_attachment_message_ids,
                                },
                            ),
                        )
                        self._send_sse_event(
                            {
                                "type": "done",
                                "assistant_message": {
                                    "role": "assistant",
                                    "content": final_text,
                                    "tools_used": tools_used,
                                    "activity_log": activity_log,
                                    "model": resolved_model,
                                    "requested_model": chat_options["requested_model"],
                                    "reasoning_enabled": chat_options["reasoning_enabled"],
                                    "reasoning_effort": response_reasoning_effort,
                                    "requested_reasoning_effort": chat_options["requested_reasoning_effort"],
                                },
                            }
                        )
            except Exception:
                logger.exception("Streaming model call failed for conversation %s", conversation_id)
                self._send_sse_event({"type": "error", "error": "Assistant request failed"})

    def _handle_edit_message(self, payload: dict) -> dict:
        conversation_id = payload.get("conversation_id")
        content = (payload.get("content") or "").strip()
        attachments = self._validate_attachments(payload.get("attachments") or [])
        enable_edit_branching = bool(payload.get("enable_edit_branching", True))

        try:
            message_id = int(payload.get("message_id"))
        except (TypeError, ValueError) as exc:
            raise ApiError("Invalid message_id") from exc

        if not conversation_id:
            raise ApiError("Missing conversation_id")
        if not content and not attachments:
            raise ApiError("Message content or attachments required")

        if content.lower().startswith("/image "):
            raw_image_command = content[7:].strip()
            with connect_db() as conn:
                if not conversation_exists(conn, conversation_id):
                    raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)
                try:
                    image_prompt, image_options = parse_image_command(raw_image_command)
                    target_conversation_id = conversation_id
                    if enable_edit_branching:
                        original_title = get_conversation_title(conn, conversation_id) or "Chat"
                        target_conversation_id = create_conversation(conn, f"{original_title} (branch)")
                        history_rows = get_all_messages_with_ids(conn, conversation_id)
                        for row in history_rows:
                            if row["id"] >= message_id:
                                break
                            add_message_returning_id(conn, target_conversation_id, message_from_row(row))
                        add_message_returning_id(conn, target_conversation_id, Message("user", content))
                        folder_id = create_conversation_folder(conn, f"{original_title} branches")
                        assign_conversation_to_folder(conn, folder_id, conversation_id)
                        assign_conversation_to_folder(conn, folder_id, target_conversation_id)
                    else:
                        replace_message_from_id(conn, conversation_id, message_id, Message("user", content))
                    image_url = call_openai_image(image_prompt, **image_options)
                except ValueError as exc:
                    raise ApiError(str(exc), HTTPStatus.BAD_REQUEST) from exc
                except Exception as exc:
                    logger.exception(
                        "Image generation failed while editing message %s in conversation %s",
                        message_id,
                        conversation_id,
                    )
                    raise ApiError(
                        "Image generation failed", HTTPStatus.INTERNAL_SERVER_ERROR
                    ) from exc
                assistant_text = f"Generated image:\n\n![Generated image]({image_url})"
                assistant_id = add_message_returning_id(
                    conn,
                    target_conversation_id,
                    Message("assistant", assistant_text),
                )
            return {
                "status": "ok",
                "conversation_id": target_conversation_id,
                "edited_message": {
                    "id": message_id,
                    "role": "user",
                    "content": content,
                },
                "assistant_message": {
                    "id": assistant_id,
                    "role": "assistant",
                    "content": assistant_text,
                },
            }

        web_search_mode = "off"
        enable_code_interpreter = bool(payload.get("enable_code_interpreter", True))
        chat_options = self._resolve_chat_options(payload, content, len(attachments))
        reinspect_message_ids = self._parse_reinspect_message_ids(payload)
        if content.lower().startswith("/web "):
            web_search_mode = "force"
            content = content[5:].strip()
        elif bool(payload.get("enable_web_search", True)):
            web_search_mode = "auto"

        user_message = self._build_user_message(content, attachments)
        if reinspect_message_ids:
            user_message.metadata = {"reinspect_message_ids": reinspect_message_ids}

        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            existing_row = get_message_row(conn, conversation_id, message_id)
            if existing_row is None:
                raise ApiError("Message not found", HTTPStatus.NOT_FOUND)
            if existing_row["role"] != "user":
                raise ApiError("Only user messages can be edited")

            original_title = get_conversation_title(conn, conversation_id) or "Chat"
            history_rows = get_all_messages_with_ids(conn, conversation_id)
            history_rows = [row for row in history_rows if row["id"] < message_id]
            history = build_replay_history_from_rows(
                history_rows,
                query=content or "Attachment upload",
                current_user_message=user_message,
                reinspect_message_ids=reinspect_message_ids,
            )

            system_prompt = build_system_prompt(
                conn,
                content or "Attachment upload",
                conversation_id=conversation_id,
                current_user_message=user_message,
            )
            messages = [Message("system", system_prompt), *history, user_message]

            try:
                target_conversation_id = conversation_id
                edited_user_message_id = message_id
                if enable_edit_branching:
                    target_conversation_id = create_conversation(conn, f"{original_title} (branch)")
                    for row in history_rows:
                        add_message_returning_id(conn, target_conversation_id, message_from_row(row))
                    edited_user_message_id = add_message_returning_id(
                        conn, target_conversation_id, user_message
                    )
                    folder_id = create_conversation_folder(conn, f"{original_title} branches")
                    assign_conversation_to_folder(conn, folder_id, conversation_id)
                    assign_conversation_to_folder(conn, folder_id, target_conversation_id)
                else:
                    replace_message_from_id(conn, conversation_id, message_id, user_message)
                auto_extract_memory_suggestions_from_user_text(
                    conn,
                    content,
                    conversation_id=target_conversation_id,
                    source_message_id=edited_user_message_id,
                )
                response_text = call_openai(
                    messages,
                    web_search_mode=web_search_mode,
                    enable_code_interpreter=enable_code_interpreter,
                    model=chat_options["model"],
                    reasoning_effort=chat_options["reasoning_effort"],
                )
            except ValueError as exc:
                raise ApiError(str(exc), HTTPStatus.BAD_REQUEST) from exc
            except Exception as exc:
                logger.exception(
                    "Model call failed while editing message %s in conversation %s",
                    message_id,
                    conversation_id,
                )
                raise ApiError(
                    "Assistant request failed",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc

            inspected_attachment_message_ids = infer_inspected_attachment_message_ids(
                history_rows,
                current_user_message_id=edited_user_message_id,
                current_user_message=user_message,
                tools_used=response_text.tools_used,
                response_text=response_text.text,
            )

            assistant_id = add_message_returning_id(
                conn,
                target_conversation_id,
                Message(
                    "assistant",
                    response_text.text,
                    self._assistant_metadata(
                        response_text,
                        chat_options,
                        inspected_attachment_message_ids,
                    ),
                ),
            )

        return {
            "status": "ok",
            "conversation_id": target_conversation_id,
            "edited_message": {
                "id": edited_user_message_id,
                "role": "user",
                "content": summarize_content(user_message.content),
            },
            "assistant_message": {
                "id": assistant_id,
                "role": "assistant",
                "content": response_text.text,
                "tools_used": response_text.tools_used,
                "activity_log": response_text.activity_log,
                "model": response_text.model,
                "requested_model": chat_options["requested_model"],
                "reasoning_enabled": chat_options["reasoning_enabled"],
                "reasoning_effort": response_text.reasoning_effort,
                "requested_reasoning_effort": chat_options["requested_reasoning_effort"],
            },
        }

    def _handle_regenerate_message(self, payload: dict) -> dict:
        conversation_id = payload.get("conversation_id")

        try:
            message_id = int(payload.get("message_id"))
        except (TypeError, ValueError) as exc:
            raise ApiError("Invalid message_id") from exc

        if not conversation_id:
            raise ApiError("Missing conversation_id")

        enable_code_interpreter = bool(payload.get("enable_code_interpreter", True))

        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            assistant_row = get_message_row(conn, conversation_id, message_id)
            if assistant_row is None:
                raise ApiError("Message not found", HTTPStatus.NOT_FOUND)
            if assistant_row["role"] != "assistant":
                raise ApiError("Only assistant messages can be regenerated")

            user_row = get_previous_user_message_row(conn, conversation_id, message_id)
            if user_row is None:
                raise ApiError(
                    "Could not find the user message for this assistant response",
                    HTTPStatus.BAD_REQUEST,
                )

            original_content = str(user_row["content"] or "").strip()
            original_user_message = message_from_row(user_row)
            attachment_count = 1 if raw_content_has_inspectable_attachments(
                user_row["raw_content"],
                user_row["content"],
            ) else 0
            chat_options = self._resolve_chat_options(payload, original_content, attachment_count)

            if original_content.lower().startswith("/image "):
                raw_image_command = original_content[7:].strip()
                try:
                    image_prompt, image_options = parse_image_command(raw_image_command)
                    image_url = call_openai_image(image_prompt, **image_options)
                except ValueError as exc:
                    raise ApiError(str(exc), HTTPStatus.BAD_REQUEST) from exc
                except Exception as exc:
                    logger.exception(
                        "Image regeneration failed for message %s in conversation %s",
                        message_id,
                        conversation_id,
                    )
                    raise ApiError(
                        "Image generation failed", HTTPStatus.INTERNAL_SERVER_ERROR
                    ) from exc

                conn.execute(
                    "DELETE FROM messages WHERE conversation_id = ? AND id >= ?",
                    (conversation_id, message_id),
                )
                cleanup_orphaned_attachments(conn)

                assistant_text = f"Generated image:\n\n![Generated image]({image_url})"
                assistant_id = add_message_returning_id(
                    conn,
                    conversation_id,
                    Message("assistant", assistant_text),
                )
                return {
                    "status": "ok",
                    "assistant_message": {
                        "id": assistant_id,
                        "role": "assistant",
                        "content": assistant_text,
                    },
                    "source_user_message": {
                        "id": user_row["id"],
                        "role": "user",
                        "content": original_content,
                    },
                }

            history_rows = get_all_messages_with_ids(conn, conversation_id)
            history_rows = [row for row in history_rows if row["id"] < user_row["id"]]
            history = build_replay_history_from_rows(
                history_rows,
                query=original_content or "Regenerate response",
                current_user_message=original_user_message,
            )

            web_search_mode = "off"
            if original_content.lower().startswith("/web "):
                web_search_mode = "force"
            elif bool(payload.get("enable_web_search", True)):
                web_search_mode = "auto"

            system_prompt = build_system_prompt(
                conn,
                original_content or "Regenerate response",
                conversation_id=conversation_id,
                current_user_message=original_user_message,
            )
            regenerate_instruction = self._get_regenerate_instruction(payload)
            if regenerate_instruction:
                system_prompt = f"{system_prompt}\n\n{regenerate_instruction}"
            messages = [Message("system", system_prompt), *history, original_user_message]

            try:
                response_text = call_openai(
                    messages,
                    web_search_mode=web_search_mode,
                    enable_code_interpreter=enable_code_interpreter,
                    model=chat_options["model"],
                    reasoning_effort=chat_options["reasoning_effort"],
                )
            except ValueError as exc:
                raise ApiError(str(exc), HTTPStatus.BAD_REQUEST) from exc
            except Exception as exc:
                logger.exception(
                    "Model call failed while regenerating message %s in conversation %s",
                    message_id,
                    conversation_id,
                )
                raise ApiError(
                    "Assistant request failed",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc

            inspected_attachment_message_ids = infer_inspected_attachment_message_ids(
                history_rows,
                current_user_message_id=int(user_row["id"]),
                current_user_message=original_user_message,
                tools_used=response_text.tools_used,
                response_text=response_text.text,
            )

            conn.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND id >= ?",
                (conversation_id, message_id),
            )
            delete_memory_suggestions_from_message_id(conn, conversation_id, message_id, include_current=True)
            cleanup_orphaned_attachments(conn)

            assistant_id = add_message_returning_id(
                conn,
                conversation_id,
                Message(
                    "assistant",
                    response_text.text,
                    self._assistant_metadata(
                        response_text,
                        chat_options,
                        inspected_attachment_message_ids,
                    ),
                ),
            )

        return {
            "status": "ok",
            "assistant_message": {
                "id": assistant_id,
                "role": "assistant",
                "content": response_text.text,
                "tools_used": response_text.tools_used,
                "activity_log": response_text.activity_log,
                "model": response_text.model,
                "requested_model": chat_options["requested_model"],
                "reasoning_enabled": chat_options["reasoning_enabled"],
                "reasoning_effort": response_text.reasoning_effort,
                "requested_reasoning_effort": chat_options["requested_reasoning_effort"],
            },
            "source_user_message": {
                "id": user_row["id"],
                "role": "user",
                "content": original_content,
            },
        }
    def _handle_regenerate_message_stream(self, payload: dict) -> None:
        conversation_id = payload.get("conversation_id")

        try:
            message_id = int(payload.get("message_id"))
        except (TypeError, ValueError) as exc:
            raise ApiError("Invalid message_id") from exc

        if not conversation_id:
            raise ApiError("Missing conversation_id")

        enable_code_interpreter = bool(payload.get("enable_code_interpreter", True))

        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            assistant_row = get_message_row(conn, conversation_id, message_id)
            if assistant_row is None:
                raise ApiError("Message not found", HTTPStatus.NOT_FOUND)
            if assistant_row["role"] != "assistant":
                raise ApiError("Only assistant messages can be regenerated")

            user_row = get_previous_user_message_row(conn, conversation_id, message_id)
            if user_row is None:
                raise ApiError(
                    "Could not find the user message for this assistant response",
                    HTTPStatus.BAD_REQUEST,
                )

            original_content = str(user_row["content"] or "").strip()
            original_user_message = message_from_row(user_row)
            attachment_count = 1 if raw_content_has_inspectable_attachments(
                user_row["raw_content"],
                user_row["content"],
            ) else 0
            chat_options = self._resolve_chat_options(payload, original_content, attachment_count)

            if original_content.lower().startswith("/image "):
                result = self._handle_regenerate_message(payload)
                self._send_sse_headers()
                self._send_sse_event(
                    {
                        "type": "done",
                        "assistant_message": result.get("assistant_message", {}),
                        "source_user_message": result.get("source_user_message", {}),
                    }
                )
                return

            history_rows = get_all_messages_with_ids(conn, conversation_id)
            history_rows = [row for row in history_rows if row["id"] < user_row["id"]]
            history = build_replay_history_from_rows(
                history_rows,
                query=original_content or "Regenerate response",
                current_user_message=original_user_message,
            )

            web_search_mode = "off"
            if original_content.lower().startswith("/web "):
                web_search_mode = "force"
            elif bool(payload.get("enable_web_search", True)):
                web_search_mode = "auto"

            system_prompt = build_system_prompt(
                conn,
                original_content or "Regenerate response",
                conversation_id=conversation_id,
                current_user_message=original_user_message,
            )
            regenerate_instruction = self._get_regenerate_instruction(payload)
            if regenerate_instruction:
                system_prompt = f"{system_prompt}\n\n{regenerate_instruction}"
            messages = [Message("system", system_prompt), *history, original_user_message]

            self._send_sse_headers()

            if chat_options["reasoning_enabled"] and web_search_mode != "off":
                try:
                    self._send_sse_event({"type": "status", "status": "reasoning"})
                    response_text = call_openai(
                        messages,
                        web_search_mode=web_search_mode,
                        enable_code_interpreter=enable_code_interpreter,
                        model=chat_options["model"],
                        reasoning_effort=chat_options["reasoning_effort"],
                    )

                    inspected_attachment_message_ids = infer_inspected_attachment_message_ids(
                        history_rows,
                        current_user_message_id=int(user_row["id"]),
                        current_user_message=original_user_message,
                        tools_used=response_text.tools_used,
                        response_text=response_text.text,
                    )

                    conn.execute(
                        "DELETE FROM messages WHERE conversation_id = ? AND id >= ?",
                        (conversation_id, message_id),
                    )
                    delete_memory_suggestions_from_message_id(conn, conversation_id, message_id, include_current=True)
                    cleanup_orphaned_attachments(conn)

                    assistant_id = add_message_returning_id(
                        conn,
                        conversation_id,
                        Message(
                            "assistant",
                            response_text.text,
                            self._assistant_metadata(
                                response_text,
                                chat_options,
                                inspected_attachment_message_ids,
                            ),
                        ),
                    )

                    self._send_sse_event(
                        {
                            "type": "done",
                            "assistant_message": {
                                "id": assistant_id,
                                "role": "assistant",
                                "content": response_text.text,
                                "tools_used": response_text.tools_used,
                                "activity_log": response_text.activity_log,
                                "model": response_text.model,
                                "requested_model": chat_options["requested_model"],
                                "reasoning_enabled": chat_options["reasoning_enabled"],
                                "reasoning_effort": response_text.reasoning_effort,
                                "requested_reasoning_effort": chat_options["requested_reasoning_effort"],
                            },
                            "source_user_message": {
                                "id": user_row["id"],
                                "role": "user",
                                "content": original_content,
                            },
                        }
                    )
                except Exception:
                    logger.exception(
                        "Reasoning regenerate fallback failed for message %s in conversation %s",
                        message_id,
                        conversation_id,
                    )
                    self._send_sse_event({"type": "error", "error": "Assistant request failed"})
                return

            full_text = ""
            try:
                for event in stream_openai(
                    messages,
                    web_search_mode=web_search_mode,
                    enable_code_interpreter=enable_code_interpreter,
                    model=chat_options["model"],
                    reasoning_effort=chat_options["reasoning_effort"],
                ):
                    event_type = event.get("type")

                    if event_type == "text_delta":
                        delta = str(event.get("delta") or "")
                        if not delta:
                            continue
                        full_text += delta
                        self._send_sse_event({"type": "delta", "delta": delta})
                        continue

                    if event_type == "status":
                        status = str(event.get("status") or "").strip()
                        if status:
                            self._send_sse_event({"type": "status", "status": status})
                        continue

                    if event_type == "activity":
                        activity = event.get("activity")
                        if isinstance(activity, dict):
                            self._send_sse_event({"type": "activity", "activity": activity})
                        continue

                    if event_type == "done":
                        final_text = str(event.get("text") or full_text)
                        tools_used = list(event.get("tools_used") or [])
                        activity_log = list(event.get("activity_log") or [])
                        resolved_model = str(event.get("model") or chat_options["model"]).strip() or chat_options["model"]
                        response_reasoning_effort = str(
                            event.get("reasoning_effort") or chat_options["reasoning_effort"] or ""
                        ).strip() or None
                        inspected_attachment_message_ids = infer_inspected_attachment_message_ids(
                            history_rows,
                            current_user_message_id=int(user_row["id"]),
                            current_user_message=original_user_message,
                            tools_used=tools_used,
                            response_text=final_text,
                        )

                        conn.execute(
                            "DELETE FROM messages WHERE conversation_id = ? AND id >= ?",
                            (conversation_id, message_id),
                        )
                        delete_memory_suggestions_from_message_id(conn, conversation_id, message_id, include_current=True)
                        cleanup_orphaned_attachments(conn)

                        assistant_id = add_message_returning_id(
                            conn,
                            conversation_id,
                            Message(
                                "assistant",
                                final_text,
                                {
                                    "tools_used": tools_used,
                                    "activity_log": activity_log,
                                    "model": resolved_model,
                                    "requested_model": chat_options["requested_model"],
                                    "reasoning_enabled": chat_options["reasoning_enabled"],
                                    "reasoning_effort": response_reasoning_effort,
                                    "requested_reasoning_effort": chat_options["requested_reasoning_effort"],
                                    "inspected_attachment_message_ids": inspected_attachment_message_ids,
                                },
                            ),
                        )

                        self._send_sse_event(
                            {
                                "type": "done",
                                "assistant_message": {
                                    "id": assistant_id,
                                    "role": "assistant",
                                    "content": final_text,
                                    "tools_used": tools_used,
                                    "activity_log": activity_log,
                                    "model": resolved_model,
                                    "requested_model": chat_options["requested_model"],
                                    "reasoning_enabled": chat_options["reasoning_enabled"],
                                    "reasoning_effort": response_reasoning_effort,
                                    "requested_reasoning_effort": chat_options["requested_reasoning_effort"],
                                },
                                "source_user_message": {
                                    "id": user_row["id"],
                                    "role": "user",
                                    "content": original_content,
                                },
                            }
                        )
            except Exception:
                logger.exception(
                    "Streaming regenerate failed for message %s in conversation %s",
                    message_id,
                    conversation_id,
                )
                self._send_sse_event({"type": "error", "error": "Assistant request failed"})

    def _handle_delete_conversation(self, conversation_id: str) -> dict:
        with connect_db() as conn:
            deleted = delete_conversation(conn, conversation_id)
        return {"deleted": deleted}

    def _handle_pin_conversation(self, conversation_id: str, payload: dict) -> dict:
        pinned = bool(payload.get("pinned", False))
        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)
            updated = update_conversation_pinned(conn, conversation_id, pinned)
        return {"updated": updated, "pinned": pinned}

    def _handle_delete_memory(self, memory_id_str: str) -> dict:
        try:
            memory_id = int(memory_id_str)
        except ValueError as exc:
            raise ApiError("Invalid memory id") from exc

        with connect_db() as conn:
            deleted = delete_memory(conn, memory_id)

        return {"deleted": deleted}

    def _handle_clear_memories(self) -> dict:
        with connect_db() as conn:
            clear_memories(conn)
        return {"status": "ok"}

    def _handle_container_file_download(
        self,
        container_id: str,
        file_id: str,
        filename_hint: str = "",
    ) -> None:
        safe_container_id = (container_id or "").strip()
        safe_file_id = (file_id or "").strip()
        safe_filename = (filename_hint or "").strip() or f"{safe_file_id}.bin"
        safe_filename = safe_filename.replace("/", "_").replace("\\", "_")

        if not safe_container_id or not safe_file_id:
            raise ApiError("Missing container_id or file_id")

        try:
            payload, content_type = fetch_container_file_content(safe_container_id, safe_file_id)
        except Exception as exc:
            logger.exception(
                "Failed to download container file %s from container %s",
                safe_file_id,
                safe_container_id,
            )
            raise ApiError("Unable to download generated file", HTTPStatus.BAD_GATEWAY) from exc

        self._send_bytes(
            payload,
            content_type,
            HTTPStatus.OK,
            extra_headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
                "Cache-Control": "no-store",
            },
        )

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            parts = self._path_parts()

            if parsed.path == "/login":
                if not auth_enabled() or self._is_authenticated():
                    self._send_redirect("/")
                    return

                self._send_file(STATIC_DIR / "login.html")
                return

            if parsed.path == "/logout":
                self._send_redirect(
                    "/login",
                    extra_headers={
                        "Set-Cookie": self._build_cookie_header(
                            WEB_COOKIE_NAME,
                            "",
                            max_age=0,
                            expires="Thu, 01 Jan 1970 00:00:00 GMT",
                        )
                    },
                )
                return

            if parsed.path == "/static/styles.css":
                self._send_file(STATIC_DIR / "styles.css")
                return

            if not self._require_auth():
                return

            if parsed.path == "/":
                self._send_file(STATIC_DIR / "index.html")
                return

            if len(parts) >= 2 and parts[0] == "static":
                relative_path = Path(*parts[1:])
                file_path = (STATIC_DIR / relative_path).resolve()
                static_root = STATIC_DIR.resolve()

                if static_root not in file_path.parents and file_path != static_root:
                    self._send_api_error("Not found", HTTPStatus.NOT_FOUND)
                    return

                self._send_file(file_path)
                return

            if parts == ["api", "conversations"]:
                params = parse_qs(parsed.query)
                query = (params.get("q", [""])[0] or "").strip()
                self._send_json(self._list_conversations(query))
                return

            if parts == ["api", "settings"]:
                self._send_json(self._get_client_settings())
                return
            
            if len(parts) == 4 and parts[:2] == ["api", "conversations"] and parts[3] == "export":
                params = parse_qs(parsed.query)
                export_format = (params.get("format", ["md"])[0] or "md").strip()
                self._handle_export_conversation(parts[2], export_format)
                return
            
            if len(parts) == 3 and parts[:2] == ["api", "conversations"]:
                self._send_json(self._get_conversation_messages(parts[2]))
                return
            if len(parts) == 4 and parts[:2] == ["api", "folders"] and parts[3] == "conversations":
                self._send_json(self._get_folder_conversations(parts[2]))
                return

            if len(parts) >= 4 and parts[:2] == ["api", "container-files"]:
                filename = unquote(parts[4]) if len(parts) >= 5 else ""
                self._handle_container_file_download(parts[2], parts[3], filename)
                return

            if parts == ["api", "memories"]:
                self._send_json(self._list_memories())
                return

            self._send_api_error("Not found", HTTPStatus.NOT_FOUND)

        except ApiError as exc:
            self._send_api_error(exc.message, exc.status)
        except Exception:
            logger.exception("Unhandled GET error")
            self._send_api_error("Internal server error", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            parts = self._path_parts()

            if parts == ["login"]:
                payload = self._read_json()
                password = str(payload.get("password") or "")

                if not auth_enabled():
                    self._send_json({"status": "ok", "auth_enabled": False})
                    return

                if not password_matches(password):
                    raise ApiError("Invalid password", HTTPStatus.UNAUTHORIZED)

                expires_at = int(time.time()) + WEB_SESSION_TTL
                session_token = make_session_token(expires_at)

                self._send_json(
                    {"status": "ok"},
                    extra_headers={
                        "Set-Cookie": self._build_cookie_header(
                            WEB_COOKIE_NAME,
                            session_token,
                            max_age=WEB_SESSION_TTL,
                        )
                    },
                )
                return

            if not self._require_auth():
                return

            if parts == ["api", "conversations"]:
                self._send_json(self._handle_create_conversation(), HTTPStatus.CREATED)
                return
            if parts == ["api", "folders"]:
                payload = self._read_json()
                self._send_json(self._handle_create_folder(payload), HTTPStatus.CREATED)
                return

            if len(parts) == 4 and parts[:2] == ["api", "conversations"] and parts[3] == "pin":
                payload = self._read_json()
                self._send_json(self._handle_pin_conversation(parts[2], payload))
                return
            if len(parts) == 4 and parts[:2] == ["api", "folders"] and parts[3] == "pin":
                payload = self._read_json()
                self._send_json(self._handle_pin_folder(parts[2], payload))
                return
            if len(parts) == 4 and parts[:2] == ["api", "folders"] and parts[3] == "conversations":
                payload = self._read_json()
                self._send_json(self._handle_add_conversation_to_folder(parts[2], payload))
                return

            if parts == ["api", "send"]:
                payload = self._read_json()
                self._send_json(self._handle_send_message(payload))
                return

            if parts == ["api", "send-stream"]:
                payload = self._read_json()
                self._handle_send_message_stream(payload)
                return

            if parts == ["api", "edit"]:
                payload = self._read_json()
                self._send_json(self._handle_edit_message(payload))
                return

            if parts == ["api", "regenerate"]:
                payload = self._read_json()
                self._send_json(self._handle_regenerate_message(payload))
                return

            if parts == ["api", "regenerate-stream"]:
                payload = self._read_json()
                self._handle_regenerate_message_stream(payload)
                return
            
            if parts == ["api", "memories"]:
                payload = self._read_json()
                self._send_json(self._handle_add_memory(payload), HTTPStatus.CREATED)
                return

            if len(parts) == 4 and parts[:2] == ["api", "memory-suggestions"] and parts[3] == "update":
                payload = self._read_json()
                self._send_json(self._handle_update_memory_suggestion(parts[2], payload))
                return

            if len(parts) == 4 and parts[:2] == ["api", "memory-suggestions"] and parts[3] == "accept":
                self._send_json(self._handle_accept_memory_suggestion(parts[2]))
                return

            if parts == ["api", "title"]:
                payload = self._read_json()
                self._send_json(self._handle_update_title(payload))
                return

            self._send_api_error("Not found", HTTPStatus.NOT_FOUND)

        except ApiError as exc:
            self._send_api_error(exc.message, exc.status)
        except Exception:
            logger.exception("Unhandled POST error")
            self._send_api_error("Internal server error", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        try:
            parts = self._path_parts()

            if not self._require_auth():
                return

            if len(parts) == 3 and parts[:2] == ["api", "conversations"]:
                self._send_json(self._handle_delete_conversation(parts[2]))
                return

            if parts == ["api", "memories"]:
                self._send_json(self._handle_clear_memories())
                return

            if len(parts) == 3 and parts[:2] == ["api", "memories"]:
                self._send_json(self._handle_delete_memory(parts[2]))
                return
            if len(parts) == 3 and parts[:2] == ["api", "folders"]:
                self._send_json(self._handle_delete_folder(parts[2]))
                return

            if len(parts) == 3 and parts[:2] == ["api", "memory-suggestions"]:
                self._send_json(self._handle_delete_memory_suggestion(parts[2]))
                return

            self._send_api_error("Not found", HTTPStatus.NOT_FOUND)

        except ApiError as exc:
            self._send_api_error(exc.message, exc.status)
        except Exception:
            logger.exception("Unhandled DELETE error")
            self._send_api_error("Internal server error", HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load_env_file(ENV_PATH)

    if not STATIC_DIR.exists():
        raise RuntimeError(f"Missing static directory: {STATIC_DIR}")

    with connect_db() as conn:
        init_db(conn)

    if auth_enabled() and not WEB_SESSION_SECRET:
        logger.warning(
            "CHATBOT_WEB_SESSION_SECRET is not set; existing sessions will reset on every restart."
        )

    server = ThreadingHTTPServer((HOST, PORT), ChatHandler)
    logger.info("Web app running on http://%s:%s", HOST, PORT)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
