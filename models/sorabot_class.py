##
## SORABOT, 2026
## sorabot_class.py
## File description:
## The class sorabot
##

import asyncio
import discord
import os
import importlib
import sys
import inspect
from pathlib import Path

from discord.ext import commands
from easy_pil import Editor, load_image_async, Font

from models.chat_agent import DiscordChatAgent
from models.guild_config_store import GuildConfigStore
from models.llm import LLMClient
from models.token_store import TokenStore

# Max replies we will send to other bots in a row (per channel) before requiring a human message.
MAX_CONSECUTIVE_BOT_REPLIES = 3
# Max consecutive bot authors when walking up a reply chain (including the incoming message).
MAX_BOT_REPLY_CHAIN_DEPTH = 3

class Sorabot(commands.Bot):
    def __init__(self, version):
        super().__init__(command_prefix="!", intents = discord.Intents.all())
        self.version = version
        self.llm_client = LLMClient()
        self.chat_agent = DiscordChatAgent(self.llm_client)
        self.token_store = TokenStore()
        self.config_store = GuildConfigStore()
        # channel_id -> how many times we have replied to a bot since the last human message
        self._bot_reply_streak: dict[int, int] = {}

    async def setup_hook(self):
        """
        Load all commands/cogs before on_ready is called.
        """
        await self.load_commands()

    async def load_commands(self):
        """
        Load all command cogs from the commands directory.
        """
        commands_dir = Path(__file__).resolve().parents[1] / "commands"

        for command_file in commands_dir.glob("*.py"):
            if command_file.name.startswith("_"):
                continue
            module_name = f"commands.{command_file.stem}"
            try:
                if module_name in sys.modules:
                    print(f"Module already imported, skipping: {command_file.stem}")
                    module = sys.modules[module_name]
                else:
                    module = importlib.import_module(module_name)
                    print(f"Imported command: {command_file.stem}")

                setup = getattr(module, "setup", None)
                if setup is not None:
                    result = setup(self)
                    if inspect.isawaitable(result):
                        await result
            except Exception as e:
                print(f"Error importing {module_name}: {e}")

    def get_bot_version(self):
        """
        Get the bot's version.
        """
        return self.version

    def _get_guild_setting(self, guild_id: int | str | None, key: str, fallback_env_var: str | None = None) -> str | None:
        """
        Retrieve a guild-specific setting, with an optional fallback to an environment variable.
        """
        if guild_id is None:
            if fallback_env_var:
                return os.getenv(fallback_env_var)
            return None

        config_value = self.config_store.get_setting(str(guild_id), key)
        if config_value not in (None, ""):
            return str(config_value)

        if fallback_env_var:
            return os.getenv(fallback_env_var)

        return None

    def _get_guild_setting_int(self, guild_id: int | str | None, key: str, fallback_env_var: str | None = None) -> int | None:
        value = self._get_guild_setting(guild_id, key, fallback_env_var)
        if not value:
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _get_openrouter_api_key(self, guild_id: int | str | None) -> str | None:
        """
        Return the effective OpenRouter API key for a guild, or the global fallback.
        """
        key = self._get_guild_setting(guild_id, "openrouter_api_key", "OPENROUTER_API_KEY")
        if key is None:
            return None

        stripped_key = str(key).strip()
        return stripped_key or None

    async def _announce_startup(self) -> None:
        """
        Announce the bot's startup to configured channels.
        """
        message = f"SoraBot is ready. Version: **{self.get_bot_version()}**"
        announced = False

        for guild in self.guilds:
            channel_id = self._get_guild_setting_int(guild.id, "startup_announcement_channel_id")
            if channel_id is None:
                continue

            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(message)
                announced = True

        if announced:
            return

        fallback_channel_id = os.getenv("BOT_CHANNEL_ID")
        if not fallback_channel_id:
            return

        try:
            channel = self.get_channel(int(fallback_channel_id))
        except ValueError:
            return

        if isinstance(channel, discord.TextChannel):
            await channel.send(message)

    async def on_ready(self):
        """
        Called when the bot is ready.
        """
        try:
            synced = await self.tree.sync()
            print(f"Synced {synced} commands")
        except Exception as e:
            print(e)

        print(f"{self.user.display_name} is ready.")
        await self._announce_startup()

    async def _resolve_referenced_message(self, message: discord.Message) -> discord.Message | None:
        """
        Resolve the parent message of a reply, from cache or via fetch.
        """
        if not message.reference or not message.reference.message_id:
            return None

        resolved = message.reference.resolved
        if isinstance(resolved, discord.Message):
            return resolved

        try:
            return await message.channel.fetch_message(message.reference.message_id)
        except discord.HTTPException:
            return None

    async def _bot_reply_chain_depth(self, message: discord.Message) -> int:
        """
        Count consecutive bot authors walking up the reply chain, including this message.
        """
        depth = 0
        current: discord.Message | None = message
        seen: set[int] = set()

        while current is not None and current.author.bot:
            if current.id in seen:
                break
            seen.add(current.id)
            depth += 1
            if depth > MAX_BOT_REPLY_CHAIN_DEPTH:
                break
            current = await self._resolve_referenced_message(current)

        return depth

    async def _should_reply_to_bot_message(self, message: discord.Message) -> bool:
        """
        Decide whether we may reply to another bot without risking an infinite loop.
        """
        if not (message.content or "").strip():
            return False

        streak = self._bot_reply_streak.get(message.channel.id, 0)
        if streak >= MAX_CONSECUTIVE_BOT_REPLIES:
            return False

        depth = await self._bot_reply_chain_depth(message)
        if depth > MAX_BOT_REPLY_CHAIN_DEPTH:
            return False

        return True

    def _build_environment_context(self, message: discord.Message) -> str:
        """
        Build a compact description of the bot's identity and Discord surroundings for the LLM.
        """
        bot_user = self.user
        lines = [
            "=== Identité ===",
            f"Nom affiché: {bot_user.display_name if bot_user else 'SoraBot'}",
            f"Username: {bot_user.name if bot_user else 'SoraBot'}",
            f"ID Discord: {bot_user.id if bot_user else 'inconnu'}",
            f"Version: {self.version}",
            "Nature: bot Discord conversationnel et agent engineering/GitHub",
        ]

        guild = message.guild
        if guild is not None:
            lines.extend(
                [
                    "",
                    "=== Serveur ===",
                    f"Nom: {guild.name}",
                    f"ID: {guild.id}",
                    f"Membres (approx.): {guild.member_count if guild.member_count is not None else 'inconnu'}",
                ]
            )
            other_bots = [
                member.display_name
                for member in guild.members
                if member.bot and (bot_user is None or member.id != bot_user.id)
            ]
            if other_bots:
                shown = other_bots[:15]
                suffix = f" (+{len(other_bots) - len(shown)} autres)" if len(other_bots) > len(shown) else ""
                lines.append(f"Autres bots présents: {', '.join(shown)}{suffix}")

        channel = message.channel
        lines.extend(["", "=== Salon ===", f"Nom: #{getattr(channel, 'name', 'DM')}"])
        topic = getattr(channel, "topic", None)
        if topic:
            lines.append(f"Sujet: {topic}")
        category = getattr(channel, "category", None)
        if category is not None:
            lines.append(f"Catégorie: {category.name}")

        author = message.author
        lines.extend(
            [
                "",
                "=== Interlocuteur ===",
                f"Nom affiché: {author.display_name}",
                f"Username: {getattr(author, 'name', author.display_name)}",
                f"ID: {author.id}",
                f"Type: {'bot' if author.bot else 'humain'}",
            ]
        )
        if isinstance(author, discord.Member):
            role_names = [role.name for role in author.roles if role.name != "@everyone"]
            if role_names:
                lines.append(f"Rôles: {', '.join(role_names[-8:])}")

        if message.reference and message.reference.message_id:
            lines.append("Contexte: ce message est une réponse à un autre message")

        return "\n".join(lines)

    async def on_message(self, message):
        """
        Handle incoming messages.

        Replies to humans and other bots in the dedicated chat channel.
        Own messages are ignored. Bot-to-bot exchanges are capped to avoid loops.
        """
        if self.user and message.author.id == self.user.id:
            return

        guild_id = message.guild.id if message.guild else None
        bot_chat_channel_id = self._get_guild_setting_int(guild_id, "bot_chat_channel_id", "BOT_CHAT_CHANNEL_ID")
        in_bot_chat = bool(bot_chat_channel_id and message.channel.id == bot_chat_channel_id)

        if not in_bot_chat:
            if not message.author.bot:
                await self.process_commands(message)
            return

        if message.author.bot:
            if not await self._should_reply_to_bot_message(message):
                return
        else:
            self._bot_reply_streak[message.channel.id] = 0

        openrouter_key = self._get_openrouter_api_key(guild_id)

        if not openrouter_key and not os.getenv("OPENAI_API_KEY"):
            guild_label = f"guild `{message.guild.name}` ({message.guild.id})" if message.guild else "this server"
            await message.reply(
                f"The OpenRouter API key is not configured for {guild_label}.",
                mention_author=False,
            )
            if not message.author.bot:
                await self.process_commands(message)
            return

        environment_context = self._build_environment_context(message)

        async with message.channel.typing():
            response = await asyncio.to_thread(
                self.chat_agent.handle_message,
                message.content,
                message.author.display_name,
                message.channel.name,
                str(message.author.id),
                openrouter_key,
                environment_context,
            )

        if response:
            await message.reply(response[:2000], mention_author=False)
            if message.author.bot:
                self._bot_reply_streak[message.channel.id] = (
                    self._bot_reply_streak.get(message.channel.id, 0) + 1
                )

        if not message.author.bot:
            await self.process_commands(message)

    async def on_member_join(self, member):
        """
        Called when a member joins the server.
        """
        welcome_channel_id = self._get_guild_setting_int(member.guild.id, "welcome_channel_id", "WELCOME_CHANNEL_ID")
        welcome_channel = self.get_channel(welcome_channel_id) if welcome_channel_id else None
        background = Editor("assets/wel.jpg")
        profile_image = await load_image_async(str(member.display_avatar.url))
        profile = Editor(profile_image).resize((250, 250)).circle_image()
        poppins = Font.poppins(size=70, variant="bold")
        pseudo_size = len(member.name)

        guild = member.guild
        role_id = self._get_guild_setting_int(guild.id, "welcome_role_id", "ROLE_ID")
        role = guild.get_role(role_id) if role_id else None

        if pseudo_size > 15:
            poppins = Font.poppins(size=43, variant="bold")
        background.paste(profile, (517, 130))
        background.ellipse((517, 130), 250, 250, outline="white", stroke_width=5)
        background.text((645, 420), f"Welcome {member.name}", color="white", font=poppins, align="center")

        file = discord.File(fp=background.image_bytes, filename="welcome.png")
        if isinstance(welcome_channel, discord.TextChannel):
            await welcome_channel.send(content=f"Welcome {member.mention} to the server.")

        if welcome_channel is not None:
            await welcome_channel.send(file=file)

        if role is not None:
            await member.add_roles(role)

sora_bot = Sorabot("v1.1.1")
