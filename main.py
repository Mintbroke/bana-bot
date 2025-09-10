import random
import discord
from discord import app_commands
from discord.ext import commands
import psycopg2
import os
from dotenv import load_dotenv
load_dotenv()

# Load environment variables or set your credentials here
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
# Set up database connection
def get_db_connection():
    return psycopg2.connect(
        os.getenv("DB_URL")
    )

# Set up Discord bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)
guild = discord.Object(id=GUILD_ID)
num_tickets = 10  # Example user data

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.tree.clear_commands(guild=guild)
    await bot.tree.sync(guild=guild)

class ImageButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.likes = 0

    @discord.ui.button(label="Draw", style=discord.ButtonStyle.success)
    async def draw(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"You are drawing!", ephemeral=True
        )

    @discord.ui.button(label="View Rates", style=discord.ButtonStyle.primary)
    async def rate(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "This is an example button under an image embed.", ephemeral=True
        )


@bot.tree.command(name="gacha", description="You can spend rare ticket to draw cats in a random banner")
async def gacha(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Gacha",
        description="""You can spend rare ticket to draw cats in a random banner\n\nYour Rare Tickets: 10\n\n
        **[Rarity]**\n- Uber Rare Rate: 5%\n- Super Rare Rate: 25%\n- Rare Rate: 70%\n\n
        **[Quality]**\n- C: 49%\n- B: 35%\n- A: 15%\n- S: 0.9%\n- SS: 0.1%\n
        """,
        color=0x5865F2,
    )
    file = discord.File("assets/gacha.png", filename="gacha.png")
    
    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)


    view = ImageButtons()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="daily", description="Get your daily rewards")
async def daily(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Daily",
        description="You can claim your daily rewards here!\n**[Rewards]**\n- 10x Rare Ticket\n- 1000x Coins\n\n",
        color=0x5865F2,
    )
    file = discord.File("assets/rare_ticket.png", filename="rare_ticket.png")

    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)


    view = ImageButtons()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="gamble", description="Gamble your coins for a chance to win rare tickets")
async def gamble(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Gamble",
        description="You can gamble your coins for a chance to win rare tickets!\n**[Gamble]**\n- Cost: 1000 Coins\n- Chance to win: 50%\n\n",
        color=0x5865F2,
    )
    file = discord.File("assets/rare_ticket.png", filename="rare_ticket.png")

    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)

    view = ImageButtons()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="deck", description="Gamble your coins for a chance to win rare tickets")
async def deck(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Gamble",
        description="You can gamble your coins for a chance to win rare tickets!\n**[Gamble]**\n- Cost: 1000 Coins\n- Chance to win: 50%\n\n",
        color=0x5865F2,
    )
    file = discord.File("assets/rare_ticket.png", filename="rare_ticket.png")

    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)

    view = ImageButtons()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="stats", description="View your current stats")
async def stats(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Stats",
        description="Here are your current stats:\n**[Stats]**\n- Rare Tickets: 10\n- Coins: 1000\n\n",
        color=0x5865F2,
    )
    file = discord.File("assets/rare_ticket.png", filename="rare_ticket.png")

    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)

    view = ImageButtons()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="inventory", description="View your current inventory")
async def inventory(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Inventory",
        description="Here are the items in your inventory:\n**[Inventory]**\n- Rare Ticket: 5\n- Common Ticket: 10\n\n",
        color=0x5865F2,
    )
    file = discord.File("assets/rare_ticket.png", filename="rare_ticket.png")

    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)

    view = ImageButtons()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="upgrade", description="Upgrade your inventory")
async def upgrade(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Upgrade",
        description="You can upgrade your inventory here!\n**[Upgrade]**\n- Cost: 5000 Coins\n- Benefits: More slots for items\n\n",
        color=0x5865F2,
    )
    file = discord.File("assets/rare_ticket.png", filename="rare_ticket.png")

    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)

    view = ImageButtons()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="map", description="View your current map")
async def map(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Map",
        description="Here is your current map:\n**[Map]**\n- Location: Town Center\n- Area: Forest\n\n",
        color=0x5865F2,
    )
    file = discord.File("assets/rare_ticket.png", filename="rare_ticket.png")

    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)

    view = ImageButtons()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="test")
async def test(interaction: discord.Interaction):
    await interaction.response.send_message("Hello World!")

@bot.command()
async def dbtest(ctx):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT version();")
        db_version = cur.fetchone()
        await ctx.send(f"DB Version: {db_version[0]}")
        cur.close()
        conn.close()
    except Exception as e:
        await ctx.send(f"DB Error: {e}")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)