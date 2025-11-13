import discord
from discord.ext import commands
import time

class Ping(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="ping")
    async def ping_command(self, ctx):
        """
        Pings the bot and shows the latency.
        """
        start_time = time.time()
        message = await ctx.send("Pinging...")
        end_time = time.time()
        latency = round((end_time - start_time) * 1000)

        embed = discord.Embed(
            title="🏓 Pong!",
            description=f"Latency: `{latency}ms`",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.avatar.url)

        await message.edit(content=None, embed=embed)

async def setup(bot):
    await bot.add_cog(Ping(bot))
