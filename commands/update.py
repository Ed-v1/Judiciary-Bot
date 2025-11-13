"""

There is ONE "update case" command. You select from a list of cases, 
if the case is a PT:

- Reassign case, in-case of like a COI mid PT;
- Edit case name or whatever;
- Finish case;
- Delete case
- Move case to trial, ask if they would like to continue the trial or reassign case.


if the case is a trial:

- Reassign case;
- edit case;
- finish case;
- delete case.



what to do for each thing:

- reassign case:
run assign_case()


- edit case
open dialouge to edit all case info, same one as in the docket_entry one where the casae info can be edited before it is submitted

- finish case
open dialouge to enter from a dropdown of option of case endings: dismissed, pleadeal, dropped and a place to enter a link for what happened. run finish_case() as a fake function for now, pass all the case info with the stuff you just got.

- delete case
just delete case from docket

important:
make sure all interactions are done by the same person, and from list of eithere judges or registrars

"""

import discord
from discord.ext import commands
from discord.ui import View, Select, Button, Modal, TextInput
import yaml
import os
import sys
import asyncio

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.google_requests import get_all_cases, edit_docket, get_case_info_from_number, delete_case_row, finish_case
from commands.docket_entry import assign_case
from bot import log

# ------------------------ CONFIG ------------------------
with open("./config.yaml", "r") as f:
    config = yaml.safe_load(f)

REVIEWER_IDS = set(config.get("reviewer_ids") or [])

# ------------------------ HELPER FUNCTIONS ------------------------
def create_update_embed(case: dict, actions: list[str]) -> discord.Embed:
    """Creates a standardized embed for displaying case information and update actions."""
    status = case.get("case_status", "N/A")
    color = discord.Color.blue()
    # Normalize status and avoid using a red color for trial states.
    try:
        s_lower = status.lower() if isinstance(status, str) else ""
    except Exception:
        s_lower = ""
    if "pre-trial" in s_lower or "pretrial" in s_lower:
        color = discord.Color.orange()
    elif "trial" in s_lower:
        # Do not use red for trial statuses; use orange to indicate active proceedings.
        color = discord.Color.orange()

    embed = discord.Embed(
        title=f"Updating Case: {case.get('case_name', 'N/A')}",
        description=f"**Case Number:** `{case.get('case_number', 'N/A')}`",
        color=color
    )
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Judge", value=case.get('judge', 'N/A'), inline=True)
    embed.add_field(name="Filing Link", value=f"[View Document]({case.get('filing_link', '#')})", inline=False)

    if actions:
        action_log = "\n".join(f"- {action}" for action in actions)
        embed.add_field(name="Actions Taken", value=action_log, inline=False)

    embed.set_footer(text="Select an action to perform on this case.")
    return embed

# ------------------------ EDIT MODAL ------------------------
class EditCaseModal(Modal, title="Edit Case Information"):
    """A modal for editing the name of a case."""
    def __init__(self, case: dict, parent_view: View):
        super().__init__()
        self.case = case
        self.parent_view = parent_view

        self.case_name_input = TextInput(
            label="Case Name",
            default=case.get('case_name', ''),
            required=True,
            max_length=100
        )
        self.add_item(self.case_name_input)
        
        self.case_number_input = TextInput(
            label="Case Number",
            default=case.get('case_number', ''),
            required=True,
            max_length=50
        )
        self.add_item(self.case_number_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handles the submission of the modal."""
        # Only the initiator may submit changes
        try:
            initiator = getattr(self.parent_view, 'initiator_id', None)
            if initiator is not None and interaction.user.id != initiator:
                await interaction.response.send_message("Only the user who started the update may submit edits.", ephemeral=True)
                return
        except Exception:
            pass

        await interaction.response.defer()
        updated_case_name = self.case_name_input.value.strip()
        updated_case_number = self.case_number_input.value.strip()
        original_case_name = self.case.get('case_name')
        original_case_number = self.case.get('case_number')

        if original_case_name == updated_case_name and original_case_number == updated_case_number:
            await interaction.followup.send("No changes were made.", ephemeral=True)
            return

        changes = {}
        if original_case_name != updated_case_name:
            changes["case_name"] = updated_case_name
        if original_case_number != updated_case_number:
            changes["case_number"] = updated_case_number

        loop = asyncio.get_event_loop()
        # Use the original case number to find the row, then update values (including case_number)
        result = await loop.run_in_executor(None, lambda: edit_docket(original_case_number, changes))

        if result.get("success"):
            log(f"Case {original_case_number} updated by {interaction.user}: name='{updated_case_name}', number='{updated_case_number}'")
            action = f"Updated case: name '{original_case_name}' → '{updated_case_name}', number '{original_case_number}' → '{updated_case_number}' by {interaction.user.mention}."
            # update parent view's local copy so refresh uses the new number
            try:
                self.parent_view.case['case_name'] = updated_case_name
                self.parent_view.case['case_number'] = updated_case_number
            except Exception:
                pass
            await self.parent_view.refresh_view(interaction, action, fetch=True)
        else:
            log(f"Failed to update case {self.case['case_number']}: {result.get('message')}")
            await interaction.followup.send(f"❌ Error updating case: {result.get('message')}", ephemeral=True)

# ------------------------ ACTION VIEW ------------------------
class ActionView(View):
    """A view that displays action buttons for a selected case."""
    def __init__(self, case: dict, initiator_id: int = None, actions: list[str] = None):
        super().__init__(timeout=300)
        self.case = case
        self.actions = actions or []
        self.bot = None # Will be set in the callback
        self.initiator_id = initiator_id

        # Add buttons based on case status (use a toggle for trial/pre-trial)
        edit_btn = Button(label="Edit", style=discord.ButtonStyle.primary, custom_id="edit_case")
        reassign_btn = Button(label="Reassign", style=discord.ButtonStyle.secondary, custom_id="reassign_case")

        status = (self.case.get("case_status") or "").lower()
        show_toggle = any(k in status for k in ("pt", "pre-trial", "in trial", "trial"))
        if "in trial" in status:
            toggle_label = "Move to Pre-Trial"
            toggle_style = discord.ButtonStyle.secondary
        else:
            toggle_label = "Move to Trial"
            toggle_style = discord.ButtonStyle.primary

        toggle_btn = Button(label=toggle_label, style=toggle_style, custom_id="toggle_trial") if show_toggle else None

        finish_btn = Button(label="Finish", style=discord.ButtonStyle.success, custom_id="finish_case")
        delete_btn = Button(label="Delete", style=discord.ButtonStyle.danger, custom_id="delete_case")
        close_btn = Button(label="Close", style=discord.ButtonStyle.secondary, custom_id="close_dialog")

        self.add_item(edit_btn)
        self.add_item(reassign_btn)
        if toggle_btn:
            self.add_item(toggle_btn)
        self.add_item(finish_btn)
        self.add_item(delete_btn)
        self.add_item(close_btn)

        # Assign callbacks dynamically
        for item in list(self.children):
            if isinstance(item, Button):
                item.callback = self.button_callback

    async def refresh_view(self, interaction: discord.Interaction, action_log: str, fetch: bool = True):
        """Refreshes the case data and updates the message by rebuilding the ActionView so buttons reflect new status.

        If fetch is False, the view will not re-query the sheet and will use the current self.case values
        (useful after we already applied an edit locally).
        """
        self.actions.append(action_log)

        if fetch:
            loop = asyncio.get_event_loop()
            case_result = await loop.run_in_executor(None, get_case_info_from_number, self.case["case_number"])

            if case_result.get("success"):
                # Prefer authoritative values from the sheet (case_result). Fallback to local values when missing.
                refreshed = {
                    "case_name": case_result.get("case_name") or self.case.get("case_name"),
                    "case_number": case_result.get("case_number") or self.case.get("case_number"),
                    "case_status": case_result.get("case_status") or self.case.get("case_status"),
                    "judge": case_result.get("judge") or self.case.get("judge"),
                    "filing_link": case_result.get("link") or self.case.get("filing_link")
                }
                self.case = refreshed
            else:
                self.actions.append("⚠️ Failed to refresh case data after the last action.")

        embed = create_update_embed(self.case, self.actions)

        # Rebuild a fresh ActionView so button labels/styles reflect updated status
        new_view = ActionView(self.case, initiator_id=getattr(self, 'initiator_id', None), actions=self.actions)
        # preserve origin info
        try:
            new_view.origin_message_id = getattr(self, 'origin_message_id', None)
            new_view.origin_channel = getattr(self, 'origin_channel', None)
            new_view.bot = getattr(self, 'bot', None)
        except Exception:
            pass

        # Try editing the original action message if available
        try:
            if hasattr(self, 'origin_channel') and hasattr(self, 'origin_message_id') and self.origin_channel and self.origin_message_id:
                msg = await self.origin_channel.fetch_message(self.origin_message_id)
                await msg.edit(embed=embed, view=new_view)
                return
        except Exception:
            pass

        # fall back to interaction-based editing
        if interaction.is_done():
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=new_view)
        else:
            await interaction.response.edit_message(embed=embed, view=new_view)


    async def button_callback(self, interaction: discord.Interaction):
        """Generic callback for all buttons."""
        custom_id = interaction.data["custom_id"]
        user = interaction.user
        self.bot = interaction.client # Store bot instance

        # Only the user who initiated the `update` command may interact with this action view
        if getattr(self, 'initiator_id', None) is not None and user.id != self.initiator_id:
            await interaction.response.send_message("Only the user who started the update can interact with these controls.", ephemeral=True)
            return

        if user.id not in REVIEWER_IDS:
            await interaction.response.send_message("You are not authorized to perform this action.", ephemeral=True)
            return

        if custom_id == "edit_case":
            modal = EditCaseModal(self.case, self)
            await interaction.responses.send_modal(modal)
            return

        loop = asyncio.get_event_loop()
        action_log = ""

        if custom_id == "reassign_case":
            log(f"Reassigning case {self.case['case_number']} by {user}")
            await interaction.response.defer()
            # pass origin info so assign_case can notify/update this view's original message when assignment is accepted
            update_notify = {
                'origin_channel': getattr(self, 'origin_channel', None),
                'origin_message_id': getattr(self, 'origin_message_id', None),
                'initiator_id': getattr(self, 'initiator_id', None)
            }
            # Store previous judge for the action log
            old_judge = self.case.get('judge', 'N/A')
            
            # Initiate reassignment
            await assign_case(self.bot, self.case['case_number'], update_notify=update_notify)
            
            # Try to fetch the updated case info after reassignment
            loop = asyncio.get_event_loop()
            case_result = await loop.run_in_executor(None, get_case_info_from_number, self.case.get('case_number'))
            new_judge = None
            
            if case_result.get('success'):
                # update local case using authoritative sheet values
                try:
                    new_judge = case_result.get('judge')
                    self.case.update({
                        'case_name': case_result.get('case_name') or self.case.get('case_name'),
                        'case_number': case_result.get('case_number') or self.case.get('case_number'),
                        'case_status': case_result.get('case_status') or self.case.get('case_status'),
                        'judge': new_judge or self.case.get('judge'),
                        'filing_link': case_result.get('link') or self.case.get('filing_link')
                    })
                except Exception:
                    pass
            else:
                # Fallback: re-scan all cases by name/number to find any moved row
                all_cases_result = await loop.run_in_executor(None, get_all_cases)
                if all_cases_result.get('success'):
                    
                    match = next((c for c in all_cases_result.get('cases', []) if c.get('case_name') == self.case.get('case_name') or c.get('case_number') == self.case.get('case_number')), None)
                    if match:
                        try:
                            new_judge = match.get('judge')
                            self.case.update({
                                'case_name': match.get('case_name') or self.case.get('case_name'),
                                'case_number': match.get('case_number') or self.case.get('case_number'),
                                'case_status': match.get('case_status') or self.case.get('case_status'),
                                'judge': new_judge or self.case.get('judge'),
                                'filing_link': match.get('link') or match.get('filing_link') or self.case.get('filing_link')
                            })
                        except Exception:
                            pass

            # Create an informative action log that shows the judge change
            if new_judge and new_judge != old_judge:
                action_log = f"Case reassigned from {old_judge} → {new_judge} by {user.mention}."
            else:
                action_log = f"Case reassignment initiated by {user.mention}."

        elif custom_id == "toggle_trial":
            # Toggle between In Trial and In Pre-Trial
            current_status = (self.case.get('case_status') or "").lower()
            await interaction.response.defer()
            if "in trial" in current_status:
                update_fields = {"case_status": "In Pre-Trial"}
                log(f"Moving case {self.case['case_number']} to Pre-Trial by {user}")
            else:
                update_fields = {"case_status": "In Trial"}
                log(f"Moving case {self.case['case_number']} to Trial by {user}")

            result = await loop.run_in_executor(None, lambda: edit_docket(self.case['case_number'], update_fields))
            if result.get("success"):
                action_log = f"Case status updated to '{update_fields['case_status']}' by {user.mention}."
            else:
                action_log = f"⚠️ Failed to update case status: {result.get('message')}"

        elif custom_id == "finish_case":
            # Open a dropdown View to select the case ending, then collect optional link
            view = EndingSelectView(self.case, self)
            # send ephemeral selection and keep it deletable by the select callback
            await interaction.response.send_message("Select how the case ended:", view=view, ephemeral=True)
            try:
                # attach the original ephemeral message to the view so it can be removed later
                sel_msg = await interaction.original_response()
                view.selection_message = sel_msg
            except Exception:
                view.selection_message = None
            return

        elif custom_id == "close_dialog":
            await interaction.response.defer()
            # Create a disabled gray view with action history preserved
            disabled_view = View(timeout=0)
            for child in self.children:
                child.disabled = True
                disabled_view.add_item(child)
            embed = create_update_embed(self.case, self.actions)
            embed.color = discord.Color.light_grey()
            embed.set_footer(text="This dialog has been closed.")
            await interaction.message.edit(embed=embed, view=disabled_view)
            return

        elif custom_id == "delete_case":
            # Ask for confirmation before deleting
            confirm_view = DeleteConfirmView(self.case, self)
            await interaction.response.send_message(f"Are you sure you want to permanently delete case `{self.case.get('case_number')}`? This cannot be undone.", view=confirm_view, ephemeral=True)
            action_log = f"Delete confirmation requested by {user.mention}."

        await self.refresh_view(interaction, action_log, fetch=True)


class EndingLinkModal(Modal, title="Finish Case - Link"):
    """Modal to collect an optional link after the ending has been selected."""
    def __init__(self, case: dict, parent_view: View, ending: str):
        super().__init__()
        self.case = case
        self.parent_view = parent_view
        self.ending = ending

        # Optional link field
        self.link_input = TextInput(
            label="Optional link (e.g., verdict, plea deal, etc.)",
            placeholder="https://...",
            required=False,
            style=discord.TextStyle.short
        )
        self.add_item(self.link_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        link = self.link_input.value.strip()

        # Only the initiator may submit the finish modal
        try:
            initiator = getattr(self.parent_view, 'initiator_id', None)
            if initiator is not None and interaction.user.id != initiator:
                await interaction.followup.send("Only the user who started the update may finish the case.", ephemeral=True)
                return
        except Exception:
            pass

        loop = asyncio.get_event_loop()
        case_info = {
            "case_number": self.case.get('case_number'),
            "case_name": self.case.get('case_name'),
            "filing_date": self.case.get('filing_date'),
            "filing_link": self.case.get('filing_link'),
            "ending_type": self.ending,
            "ending_link": link
        }

        result = await loop.run_in_executor(None, lambda: finish_case(case_info))

        if result.get("success"):
            log(f"Case {self.case['case_number']} finished as '{self.ending}' by {interaction.user}")
            action = f"Finished case as '{self.ending}' (link: {link if link else 'none'}) by {interaction.user.mention}."
            # Update the parent view's local case data so refresh doesn't need to re-query the sheet
            try:
                self.parent_view.case['case_status'] = f"Finished - {self.ending}"
                if link:
                    self.parent_view.case['filing_link'] = link
            except Exception:
                pass

            # Refresh the main action view without fetching (the pending row was removed)
            await self.parent_view.refresh_view(interaction, action, fetch=False)
            # Acknowledge to the user and remove the modal response if any
            try:
                await interaction.followup.send("✅ Case recorded and moved to the case log.", ephemeral=True)
            except Exception:
                pass
        else:
            log(f"Failed to finish case {self.case['case_number']}: {result.get('message')}")
            await interaction.followup.send(f"❌ Error finishing case: {result.get('message')}", ephemeral=True)


class EndingSelectView(View):
    """View to select how the case ended; opens a link-modal after selection."""
    def __init__(self, case: dict, parent_view: View):
        super().__init__(timeout=120)
        self.case = case
        self.parent_view = parent_view

        options = [
            discord.SelectOption(label="Verdict", value="Verdict"),
            discord.SelectOption(label="Plea Deal", value="Plea Deal"),
            discord.SelectOption(label="Dismissal", value="Dismissal"),
            discord.SelectOption(label="Mistrial", value="Mistrial"),
            discord.SelectOption(label="Dropped", value="Dropped"),
            discord.SelectOption(label="Other", value="Other"),
        ]

        select = Select(placeholder="Select case ending...", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        # Only the initiator may use this select
        try:
            initiator = getattr(self.parent_view, 'initiator_id', None)
            if initiator is not None and interaction.user.id != initiator:
                await interaction.response.send_message("Only the user who started the update may select an ending.", ephemeral=True)
                return
        except Exception:
            pass

        # Get selected ending
        values = interaction.data.get('values') or []
        if not values:
            await interaction.response.send_message("No ending selected.", ephemeral=True)
            return
        ending = values[0]

        # Open a modal to collect an optional link and then finish the case.
        # Send the modal first, then attempt to delete the ephemeral selection message.
        modal = EndingLinkModal(self.case, self.parent_view, ending)
        try:
            await interaction.response.send_modal(modal)
        except Exception as e:
            # Provide a visible error so the user knows something went wrong.
            try:
                log(f"Failed to open finish modal for {self.case.get('case_number')}: {e}")
                await interaction.response.send_message(f"❌ Could not open finish dialog: {e}", ephemeral=True)
            except Exception:
                # If responding fails (rare), try a followup
                try:
                    await interaction.followup.send(f"❌ Could not open finish dialog: {e}", ephemeral=True)
                except Exception:
                    pass
            return

        # Attempt to delete the ephemeral selection message so UI is clean (non-blocking)
        try:
            if hasattr(self, 'selection_message') and self.selection_message:
                # fire-and-forget deletion so it doesn't interfere with the modal response
                async def _del_msg(msg):
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                try:
                    asyncio.create_task(_del_msg(self.selection_message))
                except Exception:
                    # fallback to synchronous attempt
                    try:
                        await self.selection_message.delete()
                    except Exception:
                        pass
        except Exception:
            pass


class DeleteConfirmView(View):
    """A simple confirm/cancel view for deleting a case."""
    def __init__(self, case: dict, parent_view: View):
        super().__init__(timeout=120)
        self.case = case
        self.parent_view = parent_view
        # create explicit buttons instead of decorator-based handlers (avoids runtime decorator issues)
        confirm_btn = Button(label="Confirm Delete", style=discord.ButtonStyle.danger, custom_id="confirm_delete")
        cancel_btn = Button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel_delete")

        # assign callbacks
        confirm_btn.callback = self._on_confirm
        cancel_btn.callback = self._on_cancel

        self.add_item(confirm_btn)
        self.add_item(cancel_btn)

    async def _on_confirm(self, interaction: discord.Interaction):
        user = interaction.user
        # Only the initiator may confirm deletion
        try:
            initiator = getattr(self.parent_view, 'initiator_id', None)
            if initiator is not None and user.id != initiator:
                await interaction.response.send_message("Only the user who started the update can confirm deletion.", ephemeral=True)
                return
        except Exception:
            pass

        if user.id not in REVIEWER_IDS:
            await interaction.response.send_message("You are not authorized to perform this action.", ephemeral=True)
            return

        await interaction.response.defer()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: delete_case_row(self.case.get('case_name', ''), self.case.get('case_number', '')))

        if result.get('success'):
            log(f"Case {self.case.get('case_number')} deleted by {user}")
            # attempt to edit the original message to disable the view
            try:
                # Prefer editing the original action message if we have it recorded
                if hasattr(self.parent_view, 'origin_channel') and hasattr(self.parent_view, 'origin_message_id') and self.parent_view.origin_channel and self.parent_view.origin_message_id:
                    msg = await self.parent_view.origin_channel.fetch_message(self.parent_view.origin_message_id)
                    disabled_view = View(timeout=0)
                    for child in self.parent_view.children:
                        child.disabled = True
                        disabled_view.add_item(child)
                    # create a red "deleted" embed with action history
                    deleted_embed = discord.Embed(
                        title=f"Case Deleted: {self.case.get('case_name', 'N/A')}",
                        description=f"**Case Number:** `{self.case.get('case_number', 'N/A')}`\n\n**Status:** Case Permanently Deleted",
                        color=discord.Color.red()
                    )
                    # Copy over the action log
                    if self.parent_view.actions:
                        action_log = "\n".join(f"- {action}" for action in self.parent_view.actions)
                        deleted_embed.add_field(name="Actions History", value=action_log, inline=False)
                    deleted_embed.set_footer(text="This case has been permanently deleted.")
                    await msg.edit(content=None, embed=deleted_embed, view=disabled_view)
                elif interaction.message:
                    disabled_view = View(timeout=0)
                    for child in self.parent_view.children:
                        child.disabled = True
                        disabled_view.add_item(child)
                    deleted_embed = discord.Embed(
                        title=f"Case Deleted: {self.case.get('case_name', 'N/A')}",
                        description=f"**Case Number:** `{self.case.get('case_number', 'N/A')}`\n\n**Status:** Case Permanently Deleted",
                        color=discord.Color.red()
                    )
                    # Copy over the action log
                    if self.parent_view.actions:
                        action_log = "\n".join(f"- {action}" for action in self.parent_view.actions)
                        deleted_embed.add_field(name="Actions History", value=action_log, inline=False)
                    deleted_embed.set_footer(text="This case has been permanently deleted.")
                    await interaction.message.edit(content=None, embed=deleted_embed, view=disabled_view)
            except Exception:
                pass

            await interaction.followup.send(f"✅ Case {self.case.get('case_number')} deleted successfully.", ephemeral=True)
            # Also refresh parent view if possible
            try:
                # Original message has been edited to show deletion; skip fetching/refresh to avoid overwriting it.
                pass
            except Exception:
                pass
        else:
            await interaction.followup.send(f"❌ Could not delete case: {result.get('message')}", ephemeral=True)

    async def _on_cancel(self, interaction: discord.Interaction):
        # Delete the confirmation message (if possible)
        try:
            if interaction.message:
                await interaction.message.delete()
            else:
                try:
                    await interaction.delete_original_response()
                except Exception:
                    pass
        except Exception:
            pass

        # Inform the user that deletion was cancelled and suggest finishing instead
        try:
            await interaction.followup.send("Deletion cancelled. Alternatively, you can 'Finish' the case to record an outcome instead of deleting.", ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message("Deletion cancelled. Alternatively, you can 'Finish' the case to record an outcome instead of deleting.", ephemeral=True)
            except Exception:
                pass


# ------------------------ CASE SELECTION VIEW ------------------------
class CaseSelectView(View):
    """A view that displays a dropdown of cases for selection."""
    def __init__(self, cases: list, initiator_id: int = None):
        super().__init__(timeout=180)
        self.initiator_id = initiator_id
        
        options = [
            discord.SelectOption(
                label=f"{case.get('case_name', 'N/A')} ({case.get('case_number', 'N/A')}) - {case.get('judge', 'N/A')}",
                value=case.get('case_number')
            )
            for case in cases if case.get('case_number')
        ][:25] # Discord select menu option limit

        if not options:
            self.add_item(Select(placeholder="No cases found.", disabled=True))
            return

        select = Select(placeholder="Select a case to update...", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        """Callback for when a case is selected from the dropdown."""
        # Only the initiator may pick from this selection
        if getattr(self, 'initiator_id', None) is not None and interaction.user.id != self.initiator_id:
            await interaction.response.send_message("Only the user who started the update may select a case.", ephemeral=True)
            return
        await interaction.response.defer()
        case_number = interaction.data["values"][0]
        
        loop = asyncio.get_event_loop()
        all_cases_result = await loop.run_in_executor(None, get_all_cases)

        if not all_cases_result.get("success"):
             await interaction.followup.send("Failed to fetch case details.", ephemeral=True)
             return
        
        full_case_data = next((c for c in all_cases_result["cases"] if c["case_number"] == case_number), None)

        if not full_case_data:
            await interaction.followup.send("Could not retrieve full details for the selected case.", ephemeral=True)
            return

        action_view = ActionView(full_case_data, initiator_id=getattr(self, 'initiator_id', None))
        # store origin message/channel so child views can update the original dialog later
        try:
            action_view.origin_message_id = interaction.message.id
            action_view.origin_channel = interaction.channel
            action_view.bot = interaction.client
        except Exception:
            pass
        embed = create_update_embed(full_case_data, [])
        await interaction.followup.edit_message(interaction.message.id, content=None, embed=embed, view=action_view)

# ------------------------ COG ------------------------
class Update(commands.Cog):
    """A cog for updating docket entries."""
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="update")
    async def update_case(self, ctx: commands.Context):
        """The main command to initiate a case update."""
        if ctx.author.id not in REVIEWER_IDS:
            await ctx.send("You are not authorized to use this command.", delete_after=10)
            return

        await ctx.defer()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, get_all_cases)

        if not result.get("success"):
            await ctx.send(f"❌ Error fetching cases: {result.get('message')}", delete_after=10)
            return

        cases = result.get("cases", [])
        if not cases:
            await ctx.send("No cases found in the docket.", delete_after=10)
            return

        view = CaseSelectView(cases, initiator_id=ctx.author.id)
        await ctx.send("Please select a case to update from the list below.", view=view)

# ------------------------ SETUP ------------------------
async def setup(bot):
    await bot.add_cog(Update(bot))