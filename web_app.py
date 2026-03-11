import json
import logging
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

MAX_BODY_SIZE = 5 * 1024 * 1024
MAX_ATTACHMENTS = 8
MAX_TEXT_ATTACHMENT_CHARS = 200_000

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.message = message
        self.status = status


class ChatHandler(BaseHTTPRequestHandler):
    server_version = "ChatServer/1.0"

    def log_message(self, format: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    def _send_bytes(self, payload: bytes, content_type: str, status: HTTPStatus) -> None:
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
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data).encode("utf-8")
        self._send_bytes(payload, "application/json; charset=utf-8", status)

    def _send_text(self, text: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = text.encode("utf-8")
        self._send_bytes(payload, "text/plain; charset=utf-8", status)

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
            title = get_conversation_title(conn, conversation_id)
            if title is None:
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            messages = [
                {"role": msg.role, "content": msg.content}
                for msg in get_all_messages(conn, conversation_id)
            ]

        return {"title": title, "messages": messages}

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
            new_title = content[len("/title") :].strip()
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

        use_web_search = False
        if content.lower().startswith("/web "):
            use_web_search = True
            content = content[5:].strip()

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
            title = get_conversation_title(conn, conversation_id)
            if title is None:
                raise ApiError("Conversation not found", HTTPStatus.NOT_FOUND)

            history = get_recent_messages(conn, conversation_id)
            system_prompt = build_system_prompt(conn, content or "Attachment upload")
            messages = [Message("system", system_prompt), *history, user_message]

            add_message(conn, conversation_id, user_message)

            try:
                response_text = call_openai(messages, use_web_search=use_web_search)
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

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            parts = self._path_parts()

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

            if len(parts) == 3 and parts[:2] == ["api", "conversations"]:
                self._send_json(self._get_conversation_messages(parts[2]))
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

            if parts == ["api", "conversations"]:
                self._send_json(self._handle_create_conversation(), HTTPStatus.CREATED)
                return

            if parts == ["api", "send"]:
                payload = self._read_json()
                self._send_json(self._handle_send_message(payload))
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
