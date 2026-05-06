##
## SORABOT, 2026
## token_commands.py
## File description:
## Commands for managing user service tokens, such as linking/unlinking GitHub tokens and viewing linked tokens metadata.
##

from __future__ import annotations

import discord

from discord import app_commands
from discord.ext import commands
from models.token_store import TokenStore


class TokenCommands(commands.Cog):
    """
    Commands for managing user service tokens.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.token_store = TokenStore()

    @app_commands.command(name="link-github", description="Link your GitHub token to SoraBot")
    @app_commands.describe(token="Your GitHub personal access token (PAT)")
    async def link_github(self, interaction: discord.Interaction, token: str):
        """Link GitHub token securely.

        Args:
            interaction: Discord interaction
            token: GitHub personal access token (will be encrypted)
        """
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        user_name = interaction.user.display_name

        try:
            # Save token (encrypted)
            self.token_store.save_token(
                user_id=user_id,
                service="github",
                token=token,
                scopes=["repo", "workflow"],
            )

            await interaction.followup.send(
                f"GitHub token linked successfully, {user_name}!\n"
                f"Your token is encrypted and stored securely.\n"
                f"SoraBot will now use your token for GitHub operations.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"Error: {str(e)}\n"
                f"Please check your token and try again.",
                ephemeral=True
            )

    @app_commands.command(name="unlink-github", description="Remove your GitHub token from SoraBot")
    async def unlink_github(self, interaction: discord.Interaction):
        """Remove GitHub token.

        Args:
            interaction: Discord interaction
        """
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        user_name = interaction.user.display_name

        try:
            if self.token_store.delete_token(user_id, "github"):
                await interaction.followup.send(
                    f"GitHub token removed successfully, {user_name}!\n"
                    f"SoraBot will fall back to its own token for GitHub operations.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"No GitHub token found for you, {user_name}.",
                    ephemeral=True
                )
        except Exception as e:
            await interaction.followup.send(
                f"Error: {str(e)}",
                ephemeral=True
            )

    @app_commands.command(name="my-tokens", description="View your linked service tokens (metadata only)")
    async def my_tokens(self, interaction: discord.Interaction):
        """View linked tokens metadata (without showing actual tokens).

        Args:
            interaction: Discord interaction
        """
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        user_name = interaction.user.display_name

        try:
            services = self.token_store.list_services(user_id)

            if not services:
                await interaction.followup.send(
                    f"No linked tokens, {user_name}.\n"
                    f"Use `/link-github` to link your GitHub token!",
                    ephemeral=True
                )
                return

            lines = [f"**Your Linked Services, {user_name}:**"]
            for service, metadata in services.items():
                status = "Valid" if metadata["valid"] else "Expired/Invalid"
                created = metadata.get("created_at", "Unknown")[:10]
                scopes = ", ".join(metadata.get("scopes", [])) if metadata.get("scopes") else "N/A"
                expires_at = metadata.get("expires_at", "Never")
                if expires_at and expires_at != "Never":
                    expires_at = expires_at[:10]  # Format YYYY-MM-DD

                lines.append(
                    f"\n**{service.upper()}**\n"
                    f"  Status: {status}\n"
                    f"  Created: {created}\n"
                    f"  Scopes: {scopes}"
                )

            await interaction.followup.send(
                "\n".join(lines),
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"Error: {str(e)}",
                ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    """Load the TokenCommands cog."""
    await bot.add_cog(TokenCommands(bot))
