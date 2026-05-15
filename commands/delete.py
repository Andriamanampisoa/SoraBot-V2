##
## SORABOT, 2026
## delete.py
## File description:
## The "delete" command to delete a specified number of messages from the channel.
##

import discord
from discord import app_commands
from discord.ext import commands
from models.sorabot_class import sora_bot

# /delete command
@sora_bot.tree.command(name = "delete", description = "Delete n messages")
@app_commands.checks.has_permissions(manage_messages = True)
async def delete_command(interaction: discord.Interaction, number_of_message: int):
    """Delete n messages"""
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        deleted = await interaction.channel.purge(limit=number_of_message)
        deleted_count = len(deleted)
        plural = "s" if deleted_count != 1 else ""
        await interaction.followup.send(f"{deleted_count} message{plural} deleted.", ephemeral=True)

    except discord.Forbidden:
        message = "I can't delete messages here because I don't have access to the channel or the required permissions."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    except commands.MissingPermissions:
        message = "You do not have permission to use this command."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

@delete_command.error
async def delete_command_perm_handling(interaction: discord.Interaction, error):
    message = "An error occurred while running /delete."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
