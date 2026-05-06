##
## SORABOT, 2026
## version.py
## File description:
## The "version" command to displays the bot's version.
##

import discord
from models.sorabot_class import sora_bot

# /version command
@sora_bot.tree.command(name = "version", description = "Displays the bot version")
async def version(interaction: discord.Interaction):
    """Displays the bot version"""
    await interaction.response.send_message(f"SoraBot version **{sora_bot.get_bot_version()}**")
