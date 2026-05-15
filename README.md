# SoraBot-V2

SoraBot-V2 is intended to serve as a Discord entry point to an AI agent ecosystem (MCP server) and automated workflows. It provides an interactive per-server setup assistant, token management utilities, and modular command handlers while acting as a conversational gateway to backend agents and integrations.

## Main features

- Interactive server configuration assistant (`/setup`) with Discord selectors for channels and roles
- Fine-grained `config` commands to view, set, remove, and reset server settings
- Token management utilities for secure storage and access control of tokens
- Conversation memory support to persist lightweight session state per user or guild
- Modular command structure to make adding or updating commands straightforward

## Primary commands (examples)

The bot exposes several slash commands and helper commands. Main commands include:

- `/setup` — open the interactive configuration assistant
- `/config show` — display the server's current configuration
- `/config set-welcome-channel` — set the welcome channel
- `/config set-bot-chat-channel` — set the bot chat channel
- `/config set-startup-channel` — set the startup announcement channel
- `/config set-welcome-role` — set the welcome role
- `/config remove` — remove a specific configuration key
- `/config reset` — reset the server configuration to defaults
- `token` commands — import, list, and remove user tokens (see commands/token_commands.py)
- `delete` command — utility command(s) to remove stored artifacts or user data (see commands/delete.py)

Note: The exact command names and options are implemented in the `commands/` directory and may be extended.

## Quick start

Prerequisites:

- Python 3.10+ (or compatible)
- A Discord bot token and appropriate bot permissions
- An `ENCRYPTION_KEY` environment variable for secure storage

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run locally (recommended: use a `.env` file)

Create a `.env` file in the project root and add your secret values:

```env
# .env (keep this file secret and add it to .gitignore)
ENCRYPTION_KEY=your-encryption-key
DISCORD_TOKEN=your-bot-token
```

Recommended run (using `python-dotenv`):

```bash
python -m pip install python-dotenv
python -m dotenv run -- python main.py
```

Alternative (POSIX shells):

```bash
# load variables from .env into the environment for this session
export $(grep -v '^#' .env | xargs)
python main.py
```

See `create_env.sh` for a convenience script to set up a virtual environment and install dependencies.

## Configuration overview

- Server configuration is managed per-guild via the interactive `/setup` flow or the `config` subcommands. Settings are selected using Discord channel/role selectors when possible.
- Configuration is persisted to a local SQLite database located in the `data/` directory.
- Sensitive values are encrypted using the `ENCRYPTION_KEY` environment variable. Keep this key secret and rotate it if compromised.

## Token and data management

- The project includes tools to store and manage user tokens (`user_tokens/`) and conversation memory (`conversation_memory/`).
- Tokens are encrypted on disk and should never be committed to source control.
- Conversation memory is stored as JSON files for lightweight persistence between sessions.

## Extending and developing

- Commands are located in the `commands/` folder — add new command modules following the existing patterns.
- Core logic is organized under `models/` and `models/chat_agent.py` for agent interactions.
- Use the existing code style and follow the commit guidelines in `CONTRIBUTING.MD` when contributing.

## Basic troubleshooting

- If the bot doesn't start, verify `ENCRYPTION_KEY` and `DISCORD_TOKEN` are set and valid.
- Check `requirements.txt` to ensure all dependencies are installed.
- Inspect the `data/` and `user_tokens/` folders for permission or encryption issues.

## Contributing and license

- Follow the branching and commit message guidelines in `CONTRIBUTING.MD`.
- This repository is licensed under the terms found in `LICENSE`.

---
