##
## SORABOT, 2026
## purge.py
## File description:
## The "purge" command to delete all messages from the current channel.
## Requires Administrator permission and an explicit confirmation.
##

import discord
from discord import app_commands
from models.sorabot_class import sora_bot


class PurgeConfirmView(discord.ui.View):
    """Confirmation view for the /purge command."""

    def __init__(self, author_id: int, channel: discord.abc.Messageable):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.channel = channel
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who ran /purge can confirm or cancel.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(content="Purge cancelled (timed out).", view=self)
            except discord.HTTPException:
                pass

    def _disable_buttons(self) -> None:
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._disable_buttons()
        await interaction.response.edit_message(content="Purging all messages…", view=self)

        try:
            deleted = await self.channel.purge(limit=None)
            deleted_count = len(deleted)
            plural = "s" if deleted_count != 1 else ""
            await interaction.edit_original_response(
                content=f"{deleted_count} message{plural} deleted.",
                view=self,
            )
        except discord.Forbidden:
            await interaction.edit_original_response(
                content=(
                    "I can't purge messages here because I don't have access "
                    "to the channel or the required permissions."
                ),
                view=self,
            )
        except discord.HTTPException:
            await interaction.edit_original_response(
                content="An error occurred while purging messages.",
                view=self,
            )
        finally:
            self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._disable_buttons()
        await interaction.response.edit_message(content="Purge cancelled.", view=self)
        self.stop()


@sora_bot.tree.command(name="purge", description="Delete all messages in this channel")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def purge_command(interaction: discord.Interaction):
    """Delete all messages in the current channel after confirmation."""
    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message(
            "This command can only be used in a text channel.",
            ephemeral=True,
        )
        return

    me = interaction.guild.me if interaction.guild else None
    if me is not None and not channel.permissions_for(me).manage_messages:
        await interaction.response.send_message(
            "I need the **Manage Messages** permission in this channel to purge it.",
            ephemeral=True,
        )
        return

    view = PurgeConfirmView(interaction.user.id, channel)
    await interaction.response.send_message(
        (
            f"⚠️ You are about to delete **all** messages in {channel.mention}.\n"
            "This cannot be undone. Do you want to continue?"
        ),
        view=view,
        ephemeral=True,
    )
    view.message = await interaction.original_response()


@purge_command.error
async def purge_command_error_handling(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need the **Administrator** permission to use /purge."
    elif isinstance(error, app_commands.NoPrivateMessage):
        message = "This command can only be used in a server."
    else:
        message = "An error occurred while running /purge."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
