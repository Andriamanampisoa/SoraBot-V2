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

class Sorabot(commands.Bot):
    def __init__(self, version):
        super().__init__(command_prefix="!", intents = discord.Intents.all())
        self.version = version
        self.llm_client = LLMClient()
        self.chat_agent = DiscordChatAgent(self.llm_client)
        self.token_store = TokenStore()
        self.config_store = GuildConfigStore()

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

    async def on_message(self, message):
        """
        Handle incoming messages.
        """
        if message.author.bot:
            return

        guild_id = message.guild.id if message.guild else None
        bot_chat_channel_id = self._get_guild_setting_int(guild_id, "bot_chat_channel_id", "BOT_CHAT_CHANNEL_ID")
        if not bot_chat_channel_id or message.channel.id != bot_chat_channel_id:
            await self.process_commands(message)
            return

        async with message.channel.typing():
            response = await asyncio.to_thread(
                self.chat_agent.handle_message,
                message.content,
                message.author.display_name,
                message.channel.name,
                str(message.author.id),
            )

        if response:
            await message.reply(response[:2000], mention_author=False)

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

sora_bot = Sorabot("v1.1.0")
