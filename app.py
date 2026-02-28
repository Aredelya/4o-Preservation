#!/usr/bin/env python3
import os
import sqlite3
import sys
from typing import List, Optional

from core import (
    ENV_PATH,
    Message,
    add_memory,
    add_message,
    build_system_prompt,
    call_openai,
    clear_memories,
    connect_db,
    conversation_exists,
    create_conversation,
    create_user_message,
    delete_memory,
    get_conversation_title,
    get_recent_messages,
    init_db,
    list_conversations,
    list_memories,
    load_env_file,
    update_conversation_title,
)

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_BLUE = "\033[34m"
ANSI_GREEN = "\033[32m"
ANSI_DIM = "\033[2m"
TEXT_FILE_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".log"}


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
  /new                      Start a new conversation.
  /conversations            List saved conversations.
  /open <id>                Resume a conversation by id.
  /title <text>             Rename the current conversation.
  /memory add <text>        Add a long-term memory.
  /memory list              List stored memories.
  /memory delete <id>       Delete a memory by id.
  /memory clear             Delete all memories.
  /history [n]              Show previous messages from current conversation.
  /image <path> [prompt]    Send an image.
  /file <path> [prompt]     Send a text file plus optional prompt.
  /help                     Show this help message.
  /exit                     Exit the app.
"""
    )


def print_recent_history(
    conn: sqlite3.Connection, conversation_id: str, limit: int = 10
) -> None:
    messages = get_recent_messages(conn, conversation_id)
    if not messages:
        print("No messages in this conversation yet.")
        return

    shown = messages[-max(1, limit) :]
    print(style("\nRecent messages:", ANSI_BOLD, ANSI_GREEN))
    for message in shown:
        role_label = "You" if message.role == "user" else "Assistant"
        print(f"{role_label}: {message.content}")


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


def send_user_message(conn: sqlite3.Connection, conversation_id: str, user_message: Message, query: str) -> None:
    history = get_recent_messages(conn, conversation_id)
    system_prompt = build_system_prompt(conn, query)
    messages = [Message("system", system_prompt), *history, user_message]

    response_text = call_openai(messages)
    add_message(conn, conversation_id, user_message)
    add_message(conn, conversation_id, Message("assistant", response_text))
    print(f"\nAssistant: {response_text}")


def main() -> int:
    load_env_file(ENV_PATH)
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
                    print_recent_history(conn, conversation_id, limit=10)
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
            if command == "/history":
                limit = 10
                if args:
                    try:
                        limit = int(args[0])
                    except ValueError:
                        print("Usage: /history [number]")
                        continue
                print_recent_history(conn, conversation_id, limit=limit)
                continue
            if command == "/image":
                if not args:
                    print("Usage: /image <path> [prompt]")
                    continue
                image_path = args[0]
                prompt = " ".join(args[1:]).strip() or "Please analyze this image."
                try:
                    user_message = create_user_message(text=prompt, image_paths=[image_path])
                    send_user_message(conn, conversation_id, user_message, prompt)
                except Exception as exc:
                    print(f"Error: {exc}")
                continue
            if command == "/file":
                if not args:
                    print("Usage: /file <path> [prompt]")
                    continue
                file_path = args[0]
                ext = os.path.splitext(file_path)[1].lower()
                if ext not in TEXT_FILE_EXTENSIONS:
                    print("Only text-like files are supported via /file (.txt, .md, .csv, .json, .py, .log).")
                    continue
                prompt = " ".join(args[1:]).strip() or "Please read and summarize this file."
                try:
                    user_message = create_user_message(text=prompt, text_file_paths=[file_path])
                    send_user_message(conn, conversation_id, user_message, prompt)
                except Exception as exc:
                    print(f"Error: {exc}")
                continue

            print("Unknown command. Type /help for help.")
            continue

        user_message = create_user_message(text=user_input)
        try:
            send_user_message(conn, conversation_id, user_message, user_input)
        except RuntimeError as exc:
            print(f"Error: {exc}")
            continue

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
