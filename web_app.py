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
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from core import (
    ENV_PATH,
    Message,
    add_memory,
    add_message,
    add_message_returning_id,
    build_system_prompt,
    build_user_content,
    call_openai,
    call_openai_image,
    clear_memories,
    connect_db,
    conversation_exists,
    create_conversation,
    delete_conversation,
    delete_memory,
    fetch_container_file_content,
    get_all_messages_with_ids,
    get_conversation_title,
    get_message_row,
    get_previous_user_message_row,
    get_recent_messages,
    init_db,
    list_conversations,
    list_memories,
    load_env_file,
    replace_message_from_id,
    search_conversations,
    stream_openai,
    update_conversation_title,
)

load_env_file(ENV_PATH)

HOST = os.environ.get("CHATBOT_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("CHATBOT_WEB_PORT", "8000"))

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

MAX_BODY_SIZE = 5 * 1024 * 1024
MAX_ATTACHMENTS = 8
MAX_TEXT_ATTACHMENT_CHARS = 200_000

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

            else:
                raise ApiError(f"Unsupported attachment kind for '{name}'")

        return validated
    
    def _list_conversations(self, query: str) -> dict:
        with connect_db() as conn:
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
                    {
                        "id": convo_id,
                        "title": title,
                        "created_at": created_at,
                    }
                    for convo_id, title, created_at in list_conversations(conn)
                ]

        return {"conversations": conversations}

    def _get_conversation_messages(self, conversation_id: str) -> dict:
        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            title = get_conversation_title(conn, conversation_id)
            messages = [
                {
                    "id": row["id"],
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                }
                for row in get_all_messages_with_ids(conn, conversation_id)
            ]

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
                {"id": mem_id, "content": content, "created_at": created_at}
                for mem_id, content, created_at in list_memories(conn)
            ]
        return {"memories": memories}

    def _handle_create_conversation(self) -> dict:
        with connect_db() as conn:
            conversation_id = create_conversation(conn)
        return {"id": conversation_id}

    def _handle_add_memory(self, payload: dict) -> dict:
        content = (payload.get("content") or "").strip()
        if not content:
            raise ApiError("Missing memory content")

        with connect_db() as conn:
            add_memory(conn, content)

        return {"status": "ok"}

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

        if content.lower().startswith("/image "):
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
        if content.lower().startswith("/web "):
            web_search_mode = "force"
            content = content[5:].strip()
        elif bool(payload.get("enable_web_search", True)):
            web_search_mode = "auto"

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

        user_content = build_user_content(content or None, image_data_urls, file_texts)
        user_message = Message("user", user_content)

        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            history = get_recent_messages(conn, conversation_id)
            system_prompt = build_system_prompt(conn, content or "Attachment upload")
            messages = [Message("system", system_prompt), *history, user_message]

            add_message(conn, conversation_id, user_message)

            try:
                response_text = call_openai(
                    messages,
                    web_search_mode=web_search_mode,
                    enable_code_interpreter=enable_code_interpreter,
                )
            except Exception as exc:
                logger.exception("Model call failed for conversation %s", conversation_id)
                raise ApiError(
                    "Assistant request failed",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc

            add_message(conn, conversation_id, Message("assistant", response_text))

        return {
            "status": "ok",
            "assistant_message": {
                "role": "assistant",
                "content": response_text,
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
        if content.lower().startswith("/web "):
            web_search_mode = "force"
            content = content[5:].strip()
        elif bool(payload.get("enable_web_search", True)):
            web_search_mode = "auto"

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

        user_content = build_user_content(content or None, image_data_urls, file_texts)
        user_message = Message("user", user_content)

        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            history = get_recent_messages(conn, conversation_id)
            system_prompt = build_system_prompt(conn, content or "Attachment upload")
            messages = [Message("system", system_prompt), *history, user_message]
            add_message(conn, conversation_id, user_message)

            self._send_sse_headers()

            full_text = ""
            try:
                for event in stream_openai(
                    messages,
                    web_search_mode=web_search_mode,
                    enable_code_interpreter=enable_code_interpreter,
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
                    elif event_type == "done":
                        final_text = str(event.get("text") or full_text)
                        add_message(conn, conversation_id, Message("assistant", final_text))
                        self._send_sse_event(
                            {
                                "type": "done",
                                "assistant_message": {
                                    "role": "assistant",
                                    "content": final_text,
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
                    replace_message_from_id(
                        conn,
                        conversation_id,
                        message_id,
                        Message("user", content),
                    )
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
                    conversation_id,
                    Message("assistant", assistant_text),
                )
            return {
                "status": "ok",
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
        if content.lower().startswith("/web "):
            web_search_mode = "force"
            content = content[5:].strip()
        elif bool(payload.get("enable_web_search", True)):
            web_search_mode = "auto"

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

        user_content = build_user_content(content or None, image_data_urls, file_texts)
        user_message = Message("user", user_content)

        with connect_db() as conn:
            if not conversation_exists(conn, conversation_id):
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            existing_row = get_message_row(conn, conversation_id, message_id)
            if existing_row is None:
                raise ApiError("Message not found", HTTPStatus.NOT_FOUND)
            if existing_row["role"] != "user":
                raise ApiError("Only user messages can be edited")

            history_rows = get_all_messages_with_ids(conn, conversation_id)
            history = []
            for row in history_rows:
                if row["id"] >= message_id:
                    break
                history.append(Message(row["role"], row["content"]))

            system_prompt = build_system_prompt(conn, content or "Attachment upload")
            messages = [Message("system", system_prompt), *history, user_message]

            try:
                replace_message_from_id(conn, conversation_id, message_id, user_message)
                response_text = call_openai(
                    messages,
                    web_search_mode=web_search_mode,
                    enable_code_interpreter=enable_code_interpreter,
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

            assistant_id = add_message_returning_id(
                conn,
                conversation_id,
                Message("assistant", response_text),
            )

        return {
            "status": "ok",
            "edited_message": {
                "id": message_id,
                "role": "user",
                "content": user_message.content if isinstance(user_message.content, str) else "",
            },
            "assistant_message": {
                "id": assistant_id,
                "role": "assistant",
                "content": response_text,
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
            history = []
            for row in history_rows:
                if row["id"] >= user_row["id"]:
                    break
                history.append(Message(row["role"], row["content"]))

            web_search_mode = "off"
            if original_content.lower().startswith("/web "):
                web_search_mode = "force"
            elif bool(payload.get("enable_web_search", True)):
                web_search_mode = "auto"

            system_prompt = build_system_prompt(conn, original_content or "Regenerate response")
            user_message = Message("user", user_row["content"])
            messages = [Message("system", system_prompt), *history, user_message]

            try:
                response_text = call_openai(
                    messages,
                    web_search_mode=web_search_mode,
                    enable_code_interpreter=enable_code_interpreter,
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

            conn.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND id >= ?",
                (conversation_id, message_id),
            )

            assistant_id = add_message_returning_id(
                conn,
                conversation_id,
                Message("assistant", response_text),
            )

        return {
            "status": "ok",
            "assistant_message": {
                "id": assistant_id,
                "role": "assistant",
                "content": response_text,
            },
            "source_user_message": {
                "id": user_row["id"],
                "role": "user",
                "content": original_content,
            },
        }

    def _handle_delete_conversation(self, conversation_id: str) -> dict:
        with connect_db() as conn:
            deleted = delete_conversation(conn, conversation_id)
        return {"deleted": deleted}

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
            
            if len(parts) == 4 and parts[:2] == ["api", "conversations"] and parts[3] == "export":
                params = parse_qs(parsed.query)
                export_format = (params.get("format", ["md"])[0] or "md").strip()
                self._handle_export_conversation(parts[2], export_format)
                return
            
            if len(parts) == 3 and parts[:2] == ["api", "conversations"]:
                self._send_json(self._get_conversation_messages(parts[2]))
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

            if parts == ["api", "memories"]:
                payload = self._read_json()
                self._send_json(self._handle_add_memory(payload), HTTPStatus.CREATED)
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
