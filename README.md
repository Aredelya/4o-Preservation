# 4o Preservation Chatbot

A minimal command-line chatbot that recreates the ChatGPT 4o-style experience using the OpenAI API, with:

- **Persistent conversations** stored in SQLite
- **Long-term memories** that are injected into every request
- **No external dependencies** (uses Python standard library)

## Prerequisites

- Python 3.9+
- An OpenAI API key

## Setup

```bash
export OPENAI_API_KEY="your-key-here"
```

Alternatively, create a `.env` file in the same directory:

```
OPENAI_API_KEY=your-key-here
```

Optional environment variables:

- `OPENAI_MODEL` (default: `gpt-4o-2024-11-20`)
- `OPENAI_API_URL` (default: `https://api.openai.com/v1/responses`)
- `CHATBOT_DB` (default: `chatbot.db`)
- `CHATBOT_MAX_HISTORY` (default: `50`)
- `CHATBOT_MAX_OUTPUT_TOKENS` (default: `800`)
- `CHATBOT_ENV_FILE` (default: `.env`)
- `CHATBOT_USE_EMBEDDINGS` (default: `1`)
- `CHATBOT_EMBEDDING_MODEL` (default: `text-embedding-3-small`)
- `CHATBOT_EMBEDDINGS_TOP_K` (default: `6`)
- `CHATBOT_ENABLE_WEB_SEARCH` (default: `1`)

## Run

```bash
python3 app.py
```

## Web app (iOS-friendly)

Run a lightweight local web server that shares the same SQLite database as the CLI:

```bash
python3 web_app.py
```

Then open `http://<your-pc-ip>:8000` on your iPhone or iPad (same Wi-Fi) to access the synced chats
and memories. The web UI reads and writes to the same `chatbot.db`, so both the CLI and web app stay
in sync.
You can delete conversations directly from the conversation list using the `✕` button.

Optional web environment variables:

- `CHATBOT_WEB_HOST` (default: `0.0.0.0`)
- `CHATBOT_WEB_PORT` (default: `8000`)

## Hosting on a VPS (public access)

If you want access outside your home network, run the web app on a VPS and expose it with HTTPS.
A minimal flow:

1. Copy the project to your VPS (or `git clone` the repo).
2. Set your `OPENAI_API_KEY` (or `.env`) on the VPS.
3. Start the server so it listens on all interfaces:

```bash
CHATBOT_WEB_HOST=0.0.0.0 CHATBOT_WEB_PORT=8000 python3 web_app.py
```

4. Put a reverse proxy (nginx/Caddy) in front to add TLS and a stable domain:

```text
https://chat.example.com  ->  http://127.0.0.1:8000
```

Once that’s running, you can open the HTTPS URL from iOS anywhere and it will stay synced with the
same database on the VPS.

## Commands

- `/new` — start a new conversation
- `/conversations` — list saved conversations
- `/search <text>` — search conversations by title/message text
- `/open <id>` — resume a conversation by id
- `/delete <id>` — delete a conversation by id
- `/title <text>` — rename the current conversation
- `/memory add <text>` — save a long-term memory
- `/memory list` — list saved memories
- `/memory delete <id>` — delete a memory
- `/memory clear` — remove all memories
- `/history [n]` — show previous messages from the current conversation
- `/image [options] [prompt]` — generate an image in the web UI, or send an image from CLI
- `/file <path> [prompt]` — send a text-like file from CLI
- `/web <query>` — run a web search-backed query (CLI)
- `/help` — show help
- `/exit` — quit

## Notes

- Memories are injected into the system prompt at every turn.
- Conversations are saved to the SQLite file specified by `CHATBOT_DB`.
- Use `/conversations` to list past chats and `/open <id>` to continue them.
- The CLI uses simple ANSI colors when run in a TTY.
- The web app and CLI share the same database for syncing.



## Web search support

- **Conversation search**
  - **CLI**: `/search <text>` searches conversation titles and message text.
  - **Web UI**: use the **Search chats...** field above the conversation list.

Yes — web search is now supported.

- **CLI**: use `/web <query>` to run a search-backed response (example: `/web latest OpenAI API updates`).
- **Web UI**: start your message with `/web ` (example: `/web latest AI news`) to enable search for that turn.
- You can disable this feature globally with `CHATBOT_ENABLE_WEB_SEARCH=0`.

## Sending images and files
Yes—this project supports file inputs and image generation:

- **CLI**
  - `/image ./photo.jpg What is in this image?`
  - `/file ./notes.md Summarize this`
  - `/file` currently supports text-like files (`.txt`, `.md`, `.csv`, `.json`, `.py`, `.log`).

- **Web UI**
  - Use the file picker next to the message box to attach images or text files before sending. Images are sent as multimodal `input_image` blocks and text files are included as `input_text` blocks.
  - You can also generate an image by starting a message with `/image`.
    
/image in the Web UI

The web app supports inline image-generation options at the start of the command:

/image size=1536x1024 quality=high background=transparent A glass spaceship in a desert

Supported options:

model=gpt-image-1.5|gpt-image-1|gpt-image-1-mini|dall-e-3|dall-e-2
size=1024x1024|1024x1536|1536x1024|auto
quality=low|medium|high|auto
background=transparent|opaque|auto
output_format=png|webp|jpeg

Examples:

/image A neon owl in the rain
/image size=1536x1024 quality=high A neon owl in the rain
/image background=transparent output_format=webp A sticker-style fox
/image model=dall-e-3 A castle at sunset

Only leading key=value pairs are treated as options. Once normal prompt text starts, the rest is treated as the prompt.

And immediately after that, your next line should be:

FAQ: Are embeddings useful for chatbot memory?

Also delete this stray text if it’s still in the file:

FAQ: Are embeddings useful for chatbot memory?

So the issue is not subtle — that section did not actually get replaced yet.
[oaicite:

FAQ: Are embeddings useful for chatbot memory?

So the issue is not subtle — that section did not actually get replaced yet. :contentReference[oaicite:

Yes, and this project now supports them. When embeddings are enabled, each memory is vectorized and the
app retrieves the top-k most semantically relevant memories for each user message before building the
prompt.

1. Store memory embeddings in `memory_embeddings`.
2. Embed the incoming user query.
3. Retrieve top-k similar memories (configured by `CHATBOT_EMBEDDINGS_TOP_K`).
4. Inject only those memories into context.

Set `CHATBOT_USE_EMBEDDINGS=0` to fall back to injecting all memories.

## Syncing the database between PC and VPS

SQLite isn’t multi-writer across machines, so you can’t live-sync the same file at once. The simplest
approach is to **sync snapshots** periodically (PC -> VPS or VPS -> PC):

### 1) Create a safe snapshot

```bash
python3 sync_db.py --source chatbot.db --dest chatbot-backup.db
```

### 2) Copy it to the other machine

```bash
# PC -> VPS
scp chatbot-backup.db user@your-vps:/path/to/chatbot.db

# VPS -> PC
scp user@your-vps:/path/to/chatbot.db ./chatbot.db
```

### 3) Restart the app on the target machine

Make sure the CLI/web app is stopped while swapping the file, then restart it.

> Tip: If you want near-real-time syncing, a hosted database (Postgres) or a small API service is
> a better long-term option. I can help you migrate if you want.
