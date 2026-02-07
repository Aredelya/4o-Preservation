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

- `OPENAI_MODEL` (default: `gpt-4o`)
- `OPENAI_API_URL` (default: `https://api.openai.com/v1/responses`)
- `CHATBOT_DB` (default: `chatbot.db`)
- `CHATBOT_MAX_HISTORY` (default: `50`)
- `CHATBOT_MAX_OUTPUT_TOKENS` (default: `800`)
- `CHATBOT_ENV_FILE` (default: `.env`)

## Run

```bash
python3 app.py
```

## Commands

- `/new` — start a new conversation
- `/conversations` — list saved conversations
- `/open <id>` — resume a conversation by id
- `/title <text>` — rename the current conversation
- `/memory add <text>` — save a long-term memory
- `/memory list` — list saved memories
- `/memory delete <id>` — delete a memory
- `/memory clear` — remove all memories
- `/help` — show help
- `/exit` — quit

## Notes

- Memories are injected into the system prompt at every turn.
- Conversations are saved to the SQLite file specified by `CHATBOT_DB`.
- Use `/conversations` to list past chats and `/open <id>` to continue them.
- The CLI uses simple ANSI colors when run in a TTY.
