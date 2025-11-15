import os
import sys, os, datetime
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

# ---- Load Environment ----
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN is not set in the environment.")

# ---- Event Loop Policy for Windows ----
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ---- Bot Setup ----
intents = discord.Intents.default()
intents.message_content = True
# Comment this out if you don't need members, otherwise enable it in Dev Portal
# intents.members = True

bot = commands.Bot(command_prefix=";", intents=intents)


# ---- Load Cogs ----
initial_extensions = [
    # "commands.assign",
    "commands.docket_entry",
    "commands.ping",
    "commands.update",
    
]

@bot.event
async def on_ready():
    print("=" * 40)
    print(f"Bot Online: {bot.user} (ID: {bot.user.id})")
    print(f"Discord.py version: {discord.__version__}")
    print(f"Connected to {len(bot.guilds)} guild(s):")
    for guild in bot.guilds:
        print(f" - {guild.name} (ID: {guild.id})")

    from commands.docket_entry import DocketEntry
    await bot.add_cog(DocketEntry(bot))
    print("=" * 40)

async def main():
    for ext in initial_extensions:
        try:
            await bot.load_extension(ext)
            print(f"Loaded extension: {ext}")
        except Exception as e:
            print(f"Skipped {ext}: {e}")

    await bot.start(TOKEN)
    print("=" * 40)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, ValueError) as e:
        print(f"Bot shutting down: {e}")


def log(msg: str, error: bool = False):
    print(f"[LOG {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


    if error:
        with open("", "a") as f:
            f.write(f"[ERROR {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

