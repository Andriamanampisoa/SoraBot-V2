##
## SORABOT, 2026
## config_commands.py
## File description:
## Slash commands for server setup and configuration management.
##

from __future__ import annotations

import re
import discord

from discord import app_commands
from discord.ext import commands
from models.guild_config_store import GuildConfigStore

def _extract_snowflake(value: str) -> str | None:
    """
    Extract a snowflake ID from a string.
    """
    match = re.search(r"\d{15,20}", value)
    if match:
        return match.group(0)
    return None

def _display_channel(guild: discord.Guild | None, value: str | None) -> str:
    """
    Convert a stored channel ID into a human-readable format with mention.
    """
    if not value:
        return "Not set"

    if guild is not None:
        channel_id = _extract_snowflake(str(value))
        if channel_id:
            channel = guild.get_channel(int(channel_id))
            if channel is not None:
                return f"{channel.mention} ({channel.id})"

    return str(value)

def _display_role(guild: discord.Guild | None, value: str | None) -> str:
    """
    Convert a stored role ID into a human-readable format with mention.
    """
    if not value:
        return "Not set"

    if guild is not None:
        role_id = _extract_snowflake(str(value))
        if role_id:
            role = guild.get_role(int(role_id))
            if role is not None:
                return f"{role.mention} ({role.id})"

    return str(value)

CONFIG_REMOVABLE_SETTINGS = [
    app_commands.Choice(name="Welcome channel", value="welcome_channel_id"),
    app_commands.Choice(name="Bot chat channel", value="bot_chat_channel_id"),
    app_commands.Choice(name="Startup announcement channel", value="startup_announcement_channel_id"),
    app_commands.Choice(name="Welcome role", value="welcome_role_id"),
    app_commands.Choice(name="OpenRouter API key", value="openrouter_api_key"),
]

def build_config_embed(guild: discord.Guild | None, config: dict[str, str]) -> discord.Embed:
    """
    Build an embed displaying the server's configuration.
    """
    embed = discord.Embed(
        title="Server Configuration",
        description="The values below are stored in an encrypted SQLite database.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Welcome channel",
        value=_display_channel(guild, config.get("welcome_channel_id")),
        inline=False,
    )
    embed.add_field(
        name="Bot chat channel",
        value=_display_channel(guild, config.get("bot_chat_channel_id")),
        inline=False,
    )
    embed.add_field(
        name="Startup announcement channel",
        value=_display_channel(guild, config.get("startup_announcement_channel_id")),
        inline=False,
    )
    embed.add_field(
        name="Welcome role",
        value=_display_role(guild, config.get("welcome_role_id")),
        inline=False,
    )
    embed.add_field(
        name="OpenRouter key",
        value=("Set" if config.get("openrouter_api_key") else "Not set"),
        inline=False,
    )
    return embed

class ConfigChannelSelect(discord.ui.ChannelSelect):
    """
    A select menu for choosing a channel for configuration.
    """
    def __init__(
        self,
        *,
        cog: "ServerConfigCommands",
        setting_key: str,
        placeholder: str,
    ):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, channel_types=[discord.ChannelType.text])
        self.cog = cog
        self.setting_key = setting_key

    async def callback(self, interaction: discord.Interaction) -> None:
        """
        Handle the callback when a channel is selected.
        """
        if interaction.guild is None or not self.values:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        channel = self.values[0]
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            await interaction.response.send_message(
                "Could not resolve this channel.",
                ephemeral=True,
            )
            return

        success = self.cog.config_store.update_setting(str(interaction.guild.id), self.setting_key, str(channel_id))
        if not success:
            await interaction.response.send_message(
                "Configuration could not be saved.",
                ephemeral=True,
            )
            return

        config = self.cog.config_store.get_guild_config(str(interaction.guild.id))
        embed = build_config_embed(interaction.guild, config)
        await interaction.response.edit_message(embed=embed, view=self.view)

class ConfigRoleSelect(discord.ui.RoleSelect):
    """
    A select menu for choosing a role for configuration.
    """
    def __init__(self, *, cog: "ServerConfigCommands", placeholder: str):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction) -> None:
        """
        Handle the callback when a role is selected.
        """
        if interaction.guild is None or not self.values:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        role = self.values[0]
        if not isinstance(role, discord.Role):
            await interaction.response.send_message("Could not resolve this role.", ephemeral=True)
            return

        success = self.cog.config_store.update_setting(str(interaction.guild.id), "welcome_role_id", str(role.id))
        if not success:
            await interaction.response.send_message(
                "Configuration could not be saved.",
                ephemeral=True,
            )
            return

        config = self.cog.config_store.get_guild_config(str(interaction.guild.id))
        embed = build_config_embed(interaction.guild, config)
        await interaction.response.edit_message(embed=embed, view=self.view)

class ConfigSetupView(discord.ui.View):
    """
    A view for the interactive server setup assistant.
    """
    def __init__(self, cog: "ServerConfigCommands", guild_id: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id

        self.add_item(
            ConfigChannelSelect(
                cog=self.cog,
                setting_key="welcome_channel_id",
                placeholder="Select welcome channel",
            )
        )
        self.add_item(
            ConfigChannelSelect(
                cog=self.cog,
                setting_key="bot_chat_channel_id",
                placeholder="Select bot chat channel",
            )
        )
        self.add_item(
            ConfigChannelSelect(
                cog=self.cog,
                setting_key="startup_announcement_channel_id",
                placeholder="Select startup announcement channel",
            )
        )
        self.add_item(
            ConfigRoleSelect(
                cog=self.cog,
                placeholder="Select welcome role",
            )
        )

    @discord.ui.button(label="Show config", style=discord.ButtonStyle.secondary)
    async def show_config_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """
        Button callback to show the current configuration."""
        guild = interaction.guild
        config = self.cog.config_store.get_guild_config(self.guild_id)
        embed = build_config_embed(guild, config)
        await interaction.response.send_message("Here is the current configuration.", embed=embed, ephemeral=True)

class ServerConfigCommands(commands.Cog):
    """
    A cog for managing server configuration settings.
    """
    config = app_commands.Group(name="config", description="Manage the server configuration")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config_store = GuildConfigStore()

    @app_commands.command(name="setup", description="Launch the interactive server setup assistant")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
            return

        config = self.config_store.get_guild_config(str(interaction.guild.id))
        embed = build_config_embed(interaction.guild, config)
        embed.title = "Server Setup Assistant"
        embed.description = (
            "Use the selectors below to configure the bot channels and role.\n"
            "Changes are saved immediately to the encrypted SQLite database."
        )

        view = ConfigSetupView(self, str(interaction.guild.id))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @config.command(name="show", description="Display the current server configuration")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_show(self, interaction: discord.Interaction):
        """
        Display the current server configuration.
        """
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
            return

        config = self.config_store.get_guild_config(str(interaction.guild.id))
        embed = build_config_embed(interaction.guild, config)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.command(name="set-welcome-channel", description="Set the welcome channel")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="The welcome channel for the server")
    async def config_set_welcome_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """
        Set the welcome channel for the server.
        """
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        self.config_store.update_setting(str(interaction.guild.id), "welcome_channel_id", str(channel.id))
        embed = build_config_embed(interaction.guild, self.config_store.get_guild_config(str(interaction.guild.id)))
        await interaction.response.send_message("Welcome channel updated.", embed=embed, ephemeral=True)

    @config.command(name="set-bot-chat-channel", description="Set the bot chat channel")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="The bot chat channel for the server")
    async def config_set_bot_chat_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """
        Set the bot chat channel for the server.
        """
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        self.config_store.update_setting(str(interaction.guild.id), "bot_chat_channel_id", str(channel.id))
        embed = build_config_embed(interaction.guild, self.config_store.get_guild_config(str(interaction.guild.id)))
        await interaction.response.send_message("Bot chat channel updated.", embed=embed, ephemeral=True)

    @config.command(name="set-startup-channel", description="Set the startup announcement channel")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="The channel where the bot announces it is ready")
    async def config_set_startup_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """
        Set the startup announcement channel for the server.
        """
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        self.config_store.update_setting(str(interaction.guild.id), "startup_announcement_channel_id", str(channel.id))
        embed = build_config_embed(interaction.guild, self.config_store.get_guild_config(str(interaction.guild.id)))
        await interaction.response.send_message("Startup announcement channel updated.", embed=embed, ephemeral=True)

    @config.command(name="set-openrouter-key", description="Set the OpenRouter API key for this server (encrypted)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(key="The OpenRouter API key to use for this server")
    async def config_set_openrouter_key(self, interaction: discord.Interaction, key: str):
        """
        Store the OpenRouter API key for the server in encrypted storage.
        """
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        success = self.config_store.update_setting(str(interaction.guild.id), "openrouter_api_key", key)
        if not success:
            await interaction.response.send_message("Could not save the API key.", ephemeral=True)
            return

        embed = build_config_embed(interaction.guild, self.config_store.get_guild_config(str(interaction.guild.id)))
        await interaction.response.send_message("OpenRouter API key saved (encrypted).", embed=embed, ephemeral=True)

    @config.command(name="set-welcome-role", description="Set the welcome role")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(role="The role to give to new members")
    async def config_set_welcome_role(self, interaction: discord.Interaction, role: discord.Role):
        """
        Set the welcome role for the server.
        """
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        self.config_store.update_setting(str(interaction.guild.id), "welcome_role_id", str(role.id))
        embed = build_config_embed(interaction.guild, self.config_store.get_guild_config(str(interaction.guild.id)))
        await interaction.response.send_message("Welcome role updated.", embed=embed, ephemeral=True)

    @config.command(name="reset", description="Clear the stored server configuration")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_reset(self, interaction: discord.Interaction):
        """
        Clear the stored server configuration.
        """
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        self.config_store.clear_guild_config(str(interaction.guild.id))
        embed = build_config_embed(interaction.guild, {})
        await interaction.response.send_message("Server configuration reset.", embed=embed, ephemeral=True)

    @config.command(name="remove", description="Remove one stored configuration value")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(setting=CONFIG_REMOVABLE_SETTINGS)
    async def config_remove(self, interaction: discord.Interaction, setting: app_commands.Choice[str]):
        """
        Remove one stored configuration value.
        """
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        removed = self.config_store.remove_setting(str(interaction.guild.id), setting.value)
        if not removed:
            await interaction.response.send_message("Could not remove this value.", ephemeral=True)
            return

        embed = build_config_embed(interaction.guild, self.config_store.get_guild_config(str(interaction.guild.id)))
        await interaction.response.send_message(f"{setting.name} removed.", embed=embed, ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerConfigCommands(bot))
