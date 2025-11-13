"""
Docket Entry Cog
----------------
Listens for Google Docs submissions in a submission channel.
Processes the first link from each message and sends an internal review embed.
If submission is unknown or sc petition it just notes it in the internal channel
with buttons: Accept / Deny / Edit.

Accept: Adds case to docket, notifies internal + submission channels.
Deny: Marks the review closed internally.
Edit: Opens a modal to edit case name/number and updates internal message.

Logging is done in terminal. Only approved reviewer IDs can interact.
Embeds have neutral tone and footers linking to original message and Google Doc.
"""

import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import datetime
import re
import yaml
import sys
import os
import asyncio
import uuid
from bot import log

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from services.google_requests import (
    extract_google_docs_links,
    add_to_docket,
    get_gdoccase_info,
    edit_docket,
    get_case_info_from_number,
    increment_available_case_number,
    get_available_case_number,
)






# ------------------------ CONFIG ------------------------
with open("./config.yaml", "r") as f:
    config = yaml.safe_load(f)

submission_channel_id = config["channels"]["submission_channel_id"]
internal_review_channel_id = config["channels"]["internal_review_channel_id"]


# List of Discord user IDs allowed to interact with buttons
# Normalize to an empty set when not configured to avoid TypeError on "in" checks
REVIEWER_IDS = set(config.get("reviewer_ids") or [])

# ------------------------ LOGGING ------------------------


# ------------------------ EMBED CREATOR ------------------------
def create_review_embed(case_info, gdoc_link, filing_date, message_url, edited=False):
    """
    Builds the internal review embed for a docket submission.
    """
    title = "Docket Entry Review" if not edited else "Docket Entry Review (Edited)"
    
    # Use correct Discord.py v2 color syntax
    if not edited:
        color = 0xFFFFFF  # White
    else:
        color = 0x000080  # Dark Blue
    
    embed = discord.Embed(title=title, color=color)
    
    # If case info unknown
    if not case_info.get("success", False):
        embed.description = "Received submission but could not extract case details."
        embed.add_field(name="Filing Link", value=f"[View Google Doc]({gdoc_link})", inline=False)
        embed.add_field(name="Original Message", value=message_url, inline=False)

    else:
        embed.add_field(name="**Case Name:**", value=case_info.get("case_name", "N/A"), inline=True)
        embed.add_field(name="Case Number", value=case_info.get("case_number", "N/A"), inline=False)
        embed.add_field(name="Filing Date", value=filing_date, inline=False)
        embed.add_field(name="Filing Link", value=f"[View Google Doc]({gdoc_link})", inline=False)
        embed.add_field(name="Original Message", value=f"[Jump to Original Message]({message_url})", inline=False)
    

    return embed

# ------------------------ EDIT MODAL CLASS ------------------------
class EditCaseModal(Modal, title="Edit Case Information"):
    """
    Modal for editing case information
    """
    
    def __init__(self, case_info: dict, gdoc_link: str, filing_date: str, message_url: str, view: View):
        super().__init__()
        self.case_info = case_info
        self.gdoc_link = gdoc_link
        self.filing_date = filing_date
        self.message_url = message_url
        self.view = view
        
        # Create text inputs with current values
        self.case_name_input = TextInput(
            label="Case Name",
            placeholder="Enter the case name",
            default=case_info.get('case_name', ''),
            required=True,
            max_length=100
        )
        
        self.case_number_input = TextInput(
            label="Case Number", 
            placeholder="Enter the case number",
            default=case_info.get('case_number', ''),
            required=True,
            max_length=50
        )
        
        # Add inputs to modal
        self.add_item(self.case_name_input)
        self.add_item(self.case_number_input)

        
    async def on_submit(self, interaction: discord.Interaction):
        """
        Handle modal submission
        Update case info and refresh the embed
        """
        try:
            # Update the case information
            self.case_info['case_name'] = self.case_name_input.value.strip()
            self.case_info['case_number'] = self.case_number_input.value.strip()
            
            log(f"Case updated to: {self.case_info['case_name']} ({self.case_info['case_number']}) by {interaction.user}")
            
            # Create updated embed
            updated_embed = create_review_embed(
                self.case_info, 
                self.gdoc_link, 
                self.filing_date, 
                self.message_url, 
                edited=True
            )
            
            # Properly respond to the modal interaction by editing the originating message
            # This both updates the embed and acknowledges the modal (prevents "interaction failed")
            await interaction.response.edit_message(embed=updated_embed, view=self.view)

        except Exception as e:
            log(f"Error updating case in modal: {e}")
            # Always send a response to prevent interaction failed
            try:
                await interaction.response.send_message(
                    f"❌ Error updating case: {str(e)}",
                    ephemeral=True
                )
            except:
                # If response fails, try to send a followup
                try:
                    await interaction.followup.send(
                        f"❌ Error updating case: {str(e)}",
                        ephemeral=True
                    )
                except:
                    log("Could not send any response to modal interaction")
# ...existing code...

# ------------------------ COG ------------------------


# ...existing code...
class DocketEntry(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------------- LISTENER ----------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bot messages or messages outside submission channel
        if message.author == self.bot.user or message.channel.id != submission_channel_id:
            return

        log(f"Received message from {message.author} in submission channel.")

        # Extract first Google Doc link
        links = extract_google_docs_links(message.content)
        if not links:
            log("No valid Google Doc link found.")
            return
        gdoc_link = links[0]
        log(f"Processing Google Doc link: {gdoc_link}")

        testing_mode = config.get("AI", {}).get("testing_result", False)

        if testing_mode:
            log("Testing mode enabled - using mock case data")
            case_info = {
                "success": True,
                "case_name": "SD v. Ed",
                "case_number": "Crim 193",
                "case_type": "Criminal",
                "errors": []
            }
        else:
            try:
                loop = asyncio.get_event_loop()
                case_info = await loop.run_in_executor(None, get_gdoccase_info, gdoc_link)
                log(f"Case info: {case_info}")
            except Exception as e:
                log(f"Error getting case info: {e}")
                case_info = {"success": False, "errors": [str(e)]}

        # SC petition short-circuit
        if case_info.get("case_type", "").upper() == "SC":
            log(f"SC petition detected: {case_info.get('case_name', 'Unknown')}")
            internal_channel = self.bot.get_channel(internal_review_channel_id)
            if internal_channel is None:
                log(f"Error: Could not find internal review channel with ID {internal_review_channel_id}")
                return
            sc_embed = discord.Embed(title="SC Petition Received", color=0xFFFF00)
            sc_embed.add_field(name="", value=f"[Jump to original message]({message.jump_url})", inline=False)
            await internal_channel.send(embed=sc_embed)
            log("SC petition acknowledged internally")
            return

        filing_date = datetime.datetime.now().strftime("%m/%d/%Y")
        message_url = message.jump_url

        # Post the same internal review as manual_add will
        await self._post_internal_review(case_info, gdoc_link, filing_date, message_url, message)

    # ---------------- helper to post the internal review + buttons ----------------
    async def _post_internal_review(self, case_info, gdoc_link, filing_date, message_url, original_message: discord.Message | None):
        """
        Post the internal review embed with Accept / Deny / Edit buttons.
        original_message is the source message (may be None for manual flows).
        """
        internal_channel = self.bot.get_channel(internal_review_channel_id)
        if internal_channel is None:
            log(f"Error: Could not find internal review channel with ID {internal_review_channel_id}")
            return

        try:
            review_embed = create_review_embed(case_info, gdoc_link, filing_date, message_url)
        except Exception as e:
            log(f"Error creating embed for review: {e}")
            try:
                await internal_channel.send(f"Error creating review embed: {e}")
            except Exception:
                log("Failed to send error to internal channel")
            return

        unique_id = str(uuid.uuid4())[:8]
        accept_btn = Button(label="Accept", style=discord.ButtonStyle.success, custom_id=f"accept_{unique_id}")
        deny_btn = Button(label="Deny", style=discord.ButtonStyle.danger, custom_id=f"deny_{unique_id}")
        edit_btn = Button(label="Edit", style=discord.ButtonStyle.primary, custom_id=f"edit_{unique_id}")

        view = View()
        view.add_item(accept_btn)
        view.add_item(deny_btn)
        view.add_item(edit_btn)

        # ---------------- BUTTON CALLBACKS ----------------
        async def accept_callback(interaction: discord.Interaction):
            # Authorization guard
            if REVIEWER_IDS and interaction.user.id not in REVIEWER_IDS:
                await interaction.response.send_message("You are not authorized to interact with this message.", ephemeral=True)
                return

            log(f"{interaction.user} clicked Accept on case {case_info.get('case_number', 'UNKNOWN')}")

            if not case_info.get("success", False):
                await interaction.response.send_message("Cannot accept: case info unknown.", ephemeral=True)
                log("Cannot accept: case info unknown.")
                return

            # Prepare record for sheet
            case_info_to_add = case_info.copy()
            case_info_to_add["case_status"] = "PT Not assigned"
            case_info_to_add["filing_date"] = filing_date
            case_info_to_add["filing_link"] = gdoc_link
            case_info_to_add["judge"] = "NA"

            # Acknowledge and perform blocking IO in executor
            try:
                await interaction.response.defer()
            except Exception:
                pass

            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, add_to_docket, case_info_to_add)
            except Exception as e:
                log(f"Error adding to docket: {e}")
                result = {"success": False, "message": str(e)}

            if result.get("success"):
                log(f"Case {case_info.get('case_number')} added to docket by {interaction.user}")

                accepted_embed = create_review_embed(case_info, gdoc_link, filing_date, message_url, edited=True)
                accepted_embed.color = 0x00FF00
                accepted_embed.title = "Docket Entry Review - ACCEPTED"

                # Update the review message to accepted state
                try:
                    await interaction.followup.edit_message(interaction.message.id, embed=accepted_embed, view=None)
                except Exception:
                    try:
                        await interaction.message.edit(embed=accepted_embed, view=None)
                    except Exception as e:
                        log(f"Failed to edit review message after accept: {e}")

                # Reply to original submission if available
                try:
                    if original_message:
                        await original_message.reply(embed=discord.Embed(
                            title="Case Entered into Docket",
                            description=f"Entered as: **{case_info.get('case_name')} {case_info.get('case_number')}**.",
                            color=0x00FF00
                        ))
                except Exception as e:
                    log(f"Failed to notify submitter: {e}")

                # Increment available case number in background
                try:
                    await asyncio.get_event_loop().run_in_executor(None, increment_available_case_number, case_info.get("case_type", "").lower())
                except Exception as e:
                    log(f"Failed to increment available case number: {e}")

                # Trigger judge assignment using known case info (avoid re-query)
                new_case_lookup = {
                    "success": True,
                    "case_name": case_info.get("case_name"),
                    "case_status": case_info_to_add.get("case_status"),
                    "filing_date": filing_date,
                    "filing_link": gdoc_link,
                }
                try:
                    await assign_case(self.bot, case_info.get('case_number'), case_lookup=new_case_lookup)
                except Exception as e:
                    log(f"Error triggering case assignment: {e}")

            else:
                msg = result.get("message", "Unknown error")
                error_embed = discord.Embed(
                    title="Error Adding Case",
                    description=f"Failed to add case by {interaction.user.name}: {msg}",
                    color=0xFF0000
                )
                try:
                    await internal_channel.send(embed=error_embed)
                except Exception:
                    log("Failed to report error to internal channel")
                log(f"Error adding case to docket by {interaction.user}: {msg}")
                try:
                    await interaction.followup.send("❌ Failed to add case. See internal logs.", ephemeral=True)
                except Exception:
                    pass

        async def deny_callback(interaction: discord.Interaction):
            if REVIEWER_IDS and interaction.user.id not in REVIEWER_IDS:
                await interaction.response.send_message("You are not authorized to interact with this message.", ephemeral=True)
                return

            log(f"{interaction.user} clicked Deny on case {case_info.get('case_number', 'UNKNOWN')}")

            denied_embed = create_review_embed(case_info, gdoc_link, filing_date, message_url, edited=True)
            denied_embed.color = 0xFFFF00
            denied_embed.title = "Docket Entry Review - DENIED"
            denied_embed.description = f"Denied by {interaction.user.mention}"

            try:
                await interaction.response.edit_message(embed=denied_embed, view=None)
            except Exception as e:
                log(f"Failed to edit message on deny: {e}")
                try:
                    await interaction.response.send_message("Denied, but failed to update the message UI.", ephemeral=True)
                except Exception:
                    log("Could not acknowledge deny interaction")

            log(f"Case denied by {interaction.user.mention}")

        async def edit_callback(interaction: discord.Interaction):
            if REVIEWER_IDS and interaction.user.id not in REVIEWER_IDS:
                await interaction.response.send_message("You are not authorized to interact with this message.", ephemeral=True)
                return

            log(f"{interaction.user} clicked Edit on case {case_info.get('case_number', 'UNKNOWN')}")
            edit_modal = EditCaseModal(case_info, gdoc_link, filing_date, message_url, view)
            await interaction.response.send_modal(edit_modal)

        accept_btn.callback = accept_callback
        deny_btn.callback = deny_callback
        edit_btn.callback = edit_callback

        # Send the internal review message
        try:
            await internal_channel.send(embed=review_embed, view=view)
            log(f"Internal review sent successfully for case {case_info.get('case_number', 'UNKNOWN')}.")
        except Exception as e:
            log(f"Error sending internal review message: {e}")
            try:
                await internal_channel.send(f"Error creating review embed: {e}")
            except Exception:
                log("Failed to send any message to internal channel")

    # ---------------- manual add command ----------------
    @commands.command(name="add")
    async def manual_add_case(self, ctx: commands.Context, gdoc_link: str = None):
        """
        Manual add command inside the Cog.
        Usage: ;add https://docs.google.com/...
        Posts the same internal-review flow as on_message (does NOT add immediately).
        """


        if ctx.channel.id != internal_review_channel_id:
            await ctx.send("This command can only be used in the internal review channel.", delete_after=10)
            return
        if REVIEWER_IDS and ctx.author.id not in REVIEWER_IDS:
            await ctx.send("You are not authorized to use this command.", delete_after=10)
            return
        if not gdoc_link or not gdoc_link.startswith("https://docs.google.com/"):
            await ctx.send("Please provide a valid Google Doc link.", delete_after=10)
            return

        log(f"Manual add requested by {ctx.author} for link: {gdoc_link}")

        loop = asyncio.get_running_loop()
        try:
            case_info = await loop.run_in_executor(None, get_gdoccase_info, gdoc_link)
        except Exception as e:
            log(f"Error extracting case info: {e}")
            await ctx.send(f"Error extracting case info: {str(e)}", delete_after=10)
            return
        

        if not case_info.get("success", False):
            await ctx.send("Could not extract case details from the provided link.", delete_after=10)
            return

        # Prepare filing date and message url and post the internal review (do NOT add to docket now)
        filing_date = datetime.datetime.now().strftime("%m/%d/%Y")
        message_url = ctx.message.jump_url
        await self._post_internal_review(case_info, gdoc_link, filing_date, message_url, ctx.message)


# ----------------------- #

def get_judge_name(judge_id):
    """
    place holder function to get judge discord id from name
    """
    return "Ed" # placeholder

# --------------------- Case Assignment -------------------- # 




def get_free_judge(last_denied:list) -> str:
    """
    Filler function until we have real judge assignment logic.
    Returns the next available judge not in last_denied list.
    Cycles through a predefined list of judge ids.
    """
    judges = [
        "1272553776154411103", # Ed

    ]

    """
    for judge in judges:
        if judge not in last_denied:
            return judge
    return "No Judges Available"
    """

    return 1272553776154411103



# ...existing code...
async def assign_case(bot, case_number: str, case_lookup: dict = None, last_denied: list = None, update_notify: dict = None) -> dict:
    """
    Post a judge-assignment request to the internal review channel.

    Fixes:
    - Use judge Discord ID (string) as the canonical identifier everywhere.
    - Convert to int once for authorization checks.
    - Append the judge ID string to last_denied when denying.
    - Call edit_docket(case_number, update_fields) with "judge" set to the judge ID string.
    - Defer early, handle followup/edit robustly.
    """
    if last_denied is None:
        last_denied = []

    # Ensure we have case info (use provided or fetch)
    if not case_lookup:
        try:
            loop = asyncio.get_event_loop()
            case_info = await loop.run_in_executor(None, get_case_info_from_number, case_number)
        except Exception as e:
            log(f"Error getting case info for assignment: {e}")
            return {"success": False, "error": str(e)}
    else:
        case_info = case_lookup

    # Choose a judge id (string). get_free_judge returns string ids in this repo.
    judge_id_str = get_free_judge(last_denied)
    if judge_id_str == "No Judges Available":
        log(f"No judges available to assign case {case_number}")
        internal_channel = bot.get_channel(internal_review_channel_id)
        if internal_channel:
            await internal_channel.send(f"⚠️ No judges available to assign case {case_number}.")
        return {"success": False, "error": "No judges available"}

    # Keep int form for comparisons, but keep string form for sheet writes / mentions.
    try:
        judge_id_int = int(judge_id_str)
    except Exception:
        judge_id_int = None

    judge_name = get_judge_name(judge_id_str)

    # Build embed for assignment
    embed = discord.Embed(
        title="Judge Assignment Request",
        description=f"You have been assigned the case of **{case_info.get('case_name','N/A')}** ({case_number}) \n Accept or deny the assignment below.",
        color=0x87CEEB
    )

    if case_info.get("case_status") == "PT Not assigned":
        case_stage = "Pre-Trial"
    else:
        case_stage = case_info.get("case_status", "N/A")

    embed.add_field(name="Case Stage", value=case_stage, inline=True)
    embed.add_field(name="Filing Date", value=case_info.get("filing_date", "N/A"), inline=True)
    embed.add_field(name="Filing Link", value=f"[View Google Doc]({case_info.get('filing_link','')})", inline=False)
    embed.set_footer(text="Judge assignment required.")

    # Buttons and view
    accept_btn = Button(label="Accept Assignment", style=discord.ButtonStyle.success)
    deny_btn = Button(label="Deny Assignment", style=discord.ButtonStyle.danger)
    view = View()
    view.add_item(accept_btn)
    view.add_item(deny_btn)

    # Accept callback
    async def accept_callback(interaction: discord.Interaction):
        # authorize: only the judge with the assigned discord id may accept
        try:
            caller_id = interaction.user.id
        except Exception:
            caller_id = None

        if judge_id_int is not None:
            authorized = caller_id == judge_id_int
        else:
            authorized = str(caller_id) == str(judge_id_str)

        if not authorized:
            try:
                await interaction.response.send_message("You are not authorized to accept this assignment.", ephemeral=True)
            except Exception:
                log("Failed to notify unauthorized accept attempt")
            return

        # Defer early for IO
        try:
            await interaction.response.defer()
        except Exception:
            pass

        log(f"Judge {judge_name} ({judge_id_str}) accepted assignment for case {case_number}")

        # update_fields: use judge id string so sheet has canonical id (matches repo usage)
        update_fields = {
            "judge": judge_id_str,
            "case_status": "In Pre-Trial"
        }
        # Use judge name instead of judge ID for docket update
        update_fields = {
            "judge": judge_name,
            "case_status": "In Pre-Trial"
        }
        # Preserve filing link explicitly to avoid it being cleared by partial row updates
        filing_link_value = case_info.get('filing_link') or case_info.get('link')
        if filing_link_value:
            update_fields['filing_link'] = filing_link_value

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: edit_docket(case_number, update_fields))
        except Exception as e:
            log(f"Error updating docket: {e}")
            result = {"success": False, "message": str(e)}

        if not result.get("success"):
            msg = result.get("message", "Unknown error updating docket")
            log(f"Failed to update docket for {case_number}: {msg}")
            try:
                await interaction.followup.send(f"❌ Error updating docket: {msg}", ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message(f"❌ Error updating docket: {msg}", ephemeral=True)
                except Exception:
                    log("Could not notify judge of update failure")
            return

        # success: update UI
        accepted_embed = discord.Embed(
            title="Judge Assignment - ACCEPTED",
            description=f"Accepted by {interaction.user.mention}",
            color=0x00FF00
        )
        accepted_embed.add_field(name="Case", value=f"{case_info.get('case_name','N/A')} ({case_number})", inline=False)

        # try to edit the original message with the accepted state; fall back to followup
        try:
            if interaction.message:
                await interaction.message.edit(embed=accepted_embed, view=None)
            else:
                await interaction.followup.send(embed=accepted_embed)
        except Exception as e:
            log(f"Failed to update assignment message after accept: {e}")
            try:
                await interaction.followup.send(embed=accepted_embed)
            except Exception:
                log("Could not post acceptance embed via followup")
        # If the caller provided an update_notify context, update that original message's embed
        try:
            if update_notify:
                loop = asyncio.get_event_loop()
                case_result = await loop.run_in_executor(None, get_case_info_from_number, case_number)
                if case_result.get('success'):
                    judge_display = judge_name
                    status = case_result.get('case_status') or case_info.get('case_status')
                    updated_embed = discord.Embed(
                        title=f"Updating Case: {case_result.get('case_name')}",
                        description=f"**Case Number:** `{case_result.get('case_number', case_number)}`",
                        color=discord.Color.blue()
                    )
                    updated_embed.add_field(name="Status", value=status, inline=True)
                    updated_embed.add_field(name="Judge", value=judge_display or "N/A", inline=True)
                    updated_embed.add_field(name="Filing Link", value=f"[View Document]({case_result.get('link') or case_info.get('filing_link','')})", inline=False)

                    origin_channel = update_notify.get('origin_channel')
                    origin_message_id = update_notify.get('origin_message_id')
                    try:
                        if isinstance(origin_channel, int):
                            ch = bot.get_channel(origin_channel)
                        else:
                            ch = origin_channel
                        if ch and origin_message_id:
                            msg = await ch.fetch_message(origin_message_id)
                            await msg.edit(embed=updated_embed)
                    except Exception:
                        pass
        except Exception:
            pass
            log("Could not post acceptance embed via followup")

    # Deny callback
    async def deny_callback(interaction: discord.Interaction):
        try:
            caller_id = interaction.user.id
        except Exception:
            caller_id = None

        if judge_id_int is not None:
            authorized = caller_id == judge_id_int
        else:
            authorized = str(caller_id) == str(judge_id_str)

        if not authorized:
            try:
                await interaction.response.send_message("You are not authorized to deny this assignment.", ephemeral=True)
            except Exception:
                log("Failed to notify unauthorized deny attempt")
            return

        log(f"Judge {judge_name} ({judge_id_str}) denied assignment for case {case_number}")

        denied_embed = discord.Embed(
            title="Judge Assignment - DENIED",
            description=f"Denied by {interaction.user.mention}",
            color=0xFFAA00
        )
        denied_embed.add_field(name="Case", value=f"{case_info.get('case_name','N/A')} ({case_number})", inline=False)

        try:
            await interaction.response.edit_message(embed=denied_embed, view=None)
        except Exception:
            try:
                await interaction.response.send_message("Denied (could not update UI).", ephemeral=True)
            except Exception:
                log("Could not acknowledge deny interaction")

        # mark the judge id string as denied and try next judge
        last_denied.append(judge_id_str)
        log(f"Case {case_number} denied. Excluding judges: {last_denied}")

        # Small delay to avoid immediate race
        await asyncio.sleep(1)
        await assign_case(bot, case_number, case_lookup=case_info, last_denied=last_denied)

    accept_btn.callback = accept_callback
    deny_btn.callback = deny_callback

    # Send message
    internal_channel = bot.get_channel(internal_review_channel_id)
    if internal_channel is None:
        log(f"Error: Internal review channel {internal_review_channel_id} not found")
        return {"success": False, "error": "Internal review channel not found"}

    try:
        # mention the judge by id string
        await internal_channel.send(content=f"<@{judge_id_str}>", embed=embed, view=view)
        log(f"Posted judge assignment request in internal channel for {judge_name} ({judge_id_str}) (case {case_number})")
        return {"success": True}
    except Exception as e:
        log(f"Error sending judge assignment: {e}")
        return {"success": False, "error": str(e)}


# ------------------------ SETUP ------------------------
async def setup(bot):
    await bot.add_cog(DocketEntry(bot))
