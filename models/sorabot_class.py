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
from models.discord_event_tools import parse_iso_datetime

# Max replies we will send to other bots in a row (per channel) before requiring a human message.
MAX_CONSECUTIVE_BOT_REPLIES = 3
# Max consecutive bot authors when walking up a reply chain (including the incoming message).
MAX_BOT_REPLY_CHAIN_DEPTH = 3

ENTITY_TYPE_MAP = {
    "external": discord.EntityType.external,
    "voice": discord.EntityType.voice,
    "stage": discord.EntityType.stage_instance,
}

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
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            lines.append("Type: post forum" if isinstance(parent, discord.ForumChannel) else "Type: fil")
            lines.append(f"ID du fil: {channel.id}")
            if parent is not None:
                lines.append(f"Salon parent: #{parent.name} ({parent.id})")
            lines.append("Mémoire: conversation scopée à ce post/fil (partagée entre participants)")
        topic = getattr(channel, "topic", None)
        if topic:
            lines.append(f"Sujet: {topic}")
        category = getattr(channel, "category", None)
        if category is not None:
            lines.append(f"Catégorie: {category.name}")
        elif isinstance(channel, discord.Thread) and channel.parent is not None:
            parent_category = getattr(channel.parent, "category", None)
            if parent_category is not None:
                lines.append(f"Catégorie: {parent_category.name}")

        if guild is not None:
            voice_channels = [
                f"#{voice_channel.name} ({voice_channel.id})"
                for voice_channel in guild.voice_channels[:12]
            ]
            stage_channels = [
                f"#{stage_channel.name} ({stage_channel.id})"
                for stage_channel in guild.stage_channels[:8]
            ]
            if voice_channels or stage_channels:
                lines.append("")
                lines.append("=== Salons pour events vocaux/stage ===")
                if voice_channels:
                    lines.append(f"Vocaux: {', '.join(voice_channels)}")
                if stage_channels:
                    lines.append(f"Stages: {', '.join(stage_channels)}")
            lines.append("")
            lines.append("=== Capacités événement ===")
            lines.append(
                "Tu peux préparer des Scheduled Events Discord (externe avec lieu, vocal, ou stage). "
                "La création réelle est effectuée par le bot si les infos sont complètes."
            )

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

    async def _create_scheduled_event(
        self,
        guild: discord.Guild,
        payload: dict,
        *,
        reason: str | None = None,
    ) -> discord.ScheduledEvent:
        """
        Create a Discord guild scheduled event from an agent payload.
        """
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("Event name is required.")

        start_time = parse_iso_datetime(payload.get("start_time"))
        if start_time is None:
            raise ValueError("Event start_time is invalid.")

        end_time = parse_iso_datetime(payload.get("end_time"))
        entity_key = str(payload.get("entity_type") or "external").lower()
        entity_type = ENTITY_TYPE_MAP.get(entity_key, discord.EntityType.external)
        description = payload.get("description") or None
        location = (payload.get("location") or "").strip() or None
        channel = None

        if entity_type in (discord.EntityType.voice, discord.EntityType.stage_instance):
            channel_id = payload.get("channel_id")
            if not channel_id:
                raise ValueError("A voice/stage channel is required for this event type.")
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                raise ValueError(f"Channel `{channel_id}` was not found on this server.")
            location = None
        else:
            if not location:
                raise ValueError("A location is required for external events.")
            if end_time is None:
                raise ValueError("An end time is required for external events.")
            channel = None

        me = guild.me
        if me is None or not me.guild_permissions.manage_events:
            raise PermissionError(
                "I need the **Manage Events** permission to create Discord scheduled events."
            )

        return await guild.create_scheduled_event(
            name=name[:100],
            description=(description[:1000] if description else discord.utils.MISSING),
            start_time=start_time,
            end_time=end_time if end_time is not None else discord.utils.MISSING,
            entity_type=entity_type,
            privacy_level=discord.PrivacyLevel.guild_only,
            location=location if location else discord.utils.MISSING,
            channel=channel if channel is not None else discord.utils.MISSING,
            reason=reason,
        )

    async def _append_event_creation_result(
        self,
        message: discord.Message,
        response: str,
        pending_event: dict | None,
    ) -> str:
        """
        Create a pending scheduled event on the guild and append the outcome to the reply.
        """
        if not pending_event or message.guild is None:
            return response

        try:
            event = await self._create_scheduled_event(
                message.guild,
                pending_event,
                reason=f"Requested by {message.author} via SoraBot chat",
            )
        except PermissionError as exc:
            return f"{response}\n\n{exc}"
        except (ValueError, discord.HTTPException, discord.Forbidden) as exc:
            return f"{response}\n\nCould not create the Discord event: {exc}"

        event_url = getattr(event, "url", None) or (
            f"https://discord.com/events/{message.guild.id}/{event.id}"
        )
        return (
            f"{response}\n\n"
            f"Discord event created: **{event.name}**\n"
            f"{event_url}"
        )

    async def on_message(self, message):
        """
        Handle incoming messages.

        Replies to humans and other bots inside posts of the configured bot chat forum.
        Own messages are ignored. Bot-to-bot exchanges are capped to avoid loops.
        """
        if self.user and message.author.id == self.user.id:
            return

        guild_id = message.guild.id if message.guild else None
        bot_chat_channel_id = self._get_guild_setting_int(guild_id, "bot_chat_channel_id", "BOT_CHAT_CHANNEL_ID")
        channel = message.channel
        in_bot_chat = bool(
            bot_chat_channel_id
            and isinstance(channel, discord.Thread)
            and channel.parent_id == bot_chat_channel_id
        )

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
        conversation_id = f"thread:{channel.id}"
        prefixed_content = f"[{message.author.display_name}]: {message.content}"

        async with message.channel.typing():
            agent_result = await asyncio.to_thread(
                self.chat_agent.handle_message,
                prefixed_content,
                message.author.display_name,
                message.channel.name,
                conversation_id,
                openrouter_key,
                environment_context,
                str(message.author.id),
            )

        if isinstance(agent_result, dict):
            response = agent_result.get("response") or ""
            pending_event = agent_result.get("pending_discord_event")
        else:
            response = agent_result or ""
            pending_event = None

        if response or pending_event:
            response = await self._append_event_creation_result(message, response, pending_event)

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

sora_bot = Sorabot("v1.1.2")
