#!/usr/bin/env bash

cat > .env <<'EOF'
# This .env file contains environment variables for the SoraBot application.

# Environment configuration
ID=bot_id
PUBLIC_KEY=bot_public_key
TOKEN=your_bot_token_here
OPENROUTER_API_KEY=your_openrouter_api_key_here
ENCRYPTION_KEY=your_fernet_encryption_key_here

#Channel and role IDs
WELCOME_CHANNEL_ID=welcome_channel_id_here
BOT_CHANNEL_ID=bot_channel_id_here
ROLE_ID=role_id_here
# Forum channel ID used for bot chat posts (each post = one conversation)
BOT_CHAT_CHANNEL_ID=bot_chat_forum_channel_id_here

# GitHub configuration
GITHUB_TOKEN=your_github_token_here
GITHUB_REPO_OWNER=your_github_username_or_org_here
GITHUB_REPO_NAME=your_github_repo_name_here
EOF
