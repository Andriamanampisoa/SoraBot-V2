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
from github import Github, GithubException
from models.token_store import TokenStore

RECOMMENDED_GITHUB_SCOPES = {"repo", "workflow"}


def _inspect_github_token(token: str) -> tuple[str, list[str]]:
    """
    Validate a GitHub PAT and return (login, scopes).

    Raises ValueError if the token is invalid or unusable.
    """
    token = (token or "").strip()
    if not token:
        raise ValueError("Token is empty.")

    try:
        gh = Github(token)
        user = gh.get_user()
        login = user.login
        # oauth_scopes is populated after an authenticated request
        scopes = list(gh.oauth_scopes or [])
        return login, scopes
    except GithubException as exc:
        status = getattr(exc, "status", None)
        if status in (401, 403):
            raise ValueError("Invalid or unauthorized GitHub token.") from exc
        raise ValueError(f"GitHub rejected this token: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"Could not validate GitHub token: {exc}") from exc


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
        """Link GitHub token securely after validating it with GitHub."""
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        user_name = interaction.user.display_name

        try:
            login, scopes = _inspect_github_token(token)
            scope_set = set(scopes)
            missing = sorted(RECOMMENDED_GITHUB_SCOPES - scope_set)

            self.token_store.save_token(
                user_id=user_id,
                service="github",
                token=token.strip(),
                scopes=scopes,
            )

            lines = [
                f"GitHub token linked successfully, {user_name}!",
                f"Authenticated as **{login}**.",
                f"Detected scopes: `{', '.join(scopes) if scopes else 'none (fine-grained token?)'}`",
                "Your token is encrypted and stored securely.",
            ]
            if missing:
                lines.extend(
                    [
                        "",
                        f"Warning: missing recommended scopes: `{', '.join(missing)}`.",
                        "For PRs on private repos, check the full **repo** scope.",
                        "Add **workflow** if you need to touch GitHub Actions files.",
                        "Create a new classic PAT, then run `/link-github` again.",
                    ]
                )
            else:
                lines.append("SoraBot will now use this token for your GitHub operations.")

            await interaction.followup.send("\n".join(lines), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"Error: {str(e)}\n"
                f"Please check your token and try again.",
                ephemeral=True
            )

    @app_commands.command(name="unlink-github", description="Remove your GitHub token from SoraBot")
    async def unlink_github(self, interaction: discord.Interaction):
        """Remove GitHub token."""
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
        """View linked tokens metadata (without showing actual tokens)."""
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
