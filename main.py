from datetime import datetime, timezone, timedelta
import random
import logging, sys
import discord
from discord import app_commands
from discord.ext import commands
import psycopg2
import os
from dotenv import load_dotenv
from functions import claim_daily, get_db_connection, scrape
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,  # <- stdout so Railway won’t flag as error
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
# Optional: let discord.py set up its own handlers *without* attaching to root
discord.utils.setup_logging(level=logging.INFO, root=False)
log = logging.getLogger("bot")

# Load environment variables or set your credentials here
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

# Set up Discord bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)
guild = discord.Object(id=GUILD_ID)
num_tickets = 10  # Example user data

def _ensure_schema():
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS test_table1 (
                      guild_id   BIGINT NOT NULL,
                      user_id    BIGINT NOT NULL,
                      balance    BIGINT NOT NULL DEFAULT 0,
                      num_tickets BIGINT NOT NULL DEFAULT 0,
                      last_daily TIMESTAMPTZ,
                      PRIMARY KEY (guild_id, user_id)
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS test_table2 (
                      guild_id   BIGINT NOT NULL,
                      user_id    BIGINT NOT NULL,
                      scraped    BIGINT NOT NULL DEFAULT 0,
                      date       TIMESTAMPTZ,
                      last_scraped TIMESTAMPTZ,
                      PRIMARY KEY (guild_id, user_id)
                    );
                """)
                log.info("Ensured schema")
    except Exception as e:
        log.info(f"Error ensuring schema: {e}")
    finally:
        conn.close()

@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user}")
    await bot.tree.sync(guild=guild)
    _ensure_schema()

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

class GeneralView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)


@bot.tree.command(name="gacha", description="You can spend rare ticket to draw cats in a random banner", guild=guild)
async def gacha(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Gacha",
        description="""You can spend rare ticket to draw cats in a random banner\n\nYour Rare Tickets: 10\n\n
        **[Rarity]**\n- Bana Rare Rate: 0.1%\n- Uber Rare Rate: 4.9%\n- Super Rare Rate: 25%\n- Rare Rate: 70%\n\n
        **[Quality]**\n- C: 49%\n- B: 35%\n- A: 15%\n- S: 0.9%\n- SS: 0.1%\n
        """,
        color=0x5865F2,
    )
    file = discord.File("assets/gacha.png", filename="gacha.png")
    
    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)


    view = ImageButtons()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="daily", description="Get your daily rewards", guild=guild)
async def daily(interaction: discord.Interaction):

    file = discord.File("assets/rare_ticket.png", filename="rare_ticket.png")
    view = ImageButtons()
    daily, bal, ticket, cooldown = claim_daily(GUILD_ID, interaction.user.id, datetime.now(timezone.utc), 10000, timedelta(hours=24))
    if daily:
        embed = discord.Embed(
            title="Daily",
            description=f"You can claim your daily rewards here!\n**[Rewards]**\n- 10x Rare Ticket\n- 10000x Coins\n\n\n You have {bal} coins and {ticket} rare tickets.\n\n",
            color=0x5865F2,
        )
        embed.set_image(url=file.uri)
        await interaction.response.send_message(embed=embed, file=file, view=view)
    else:
        embed = discord.Embed(
            title="Daily",
            description=f"You have already claimed your daily rewards! Please wait {cooldown//3600} hours and {(cooldown%3600)//60} minutes before claiming again.\n**[Rewards]**\n- 10x Rare Ticket\n- 10000x Coins\n\nn You have {bal} coins and {ticket} rare tickets.\n\n",
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="gamble", description="Gamble your coins for a chance to win rare tickets", guild=guild)
async def gamble(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Gamble",
        description="You can gamble your coins for a chance to win rare tickets!\n**[Gamble]**\n- Cost: 1000 Coins\n- Chance to win: 50%\n\n",
        color=0x5865F2,
    )
    file = discord.File("assets/rare_ticket.png", filename="rare_ticket.png")

    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)

    view = GeneralView()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="deck", description="Gamble your coins for a chance to win rare tickets", guild=guild)
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

@bot.tree.command(name="stats", description="View your current stats", guild=guild)
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

@bot.tree.command(name="inventory", description="View your current inventory", guild=guild)
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

@bot.tree.command(name="upgrade", description="Upgrade your inventory", guild=guild)
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

@bot.tree.command(name="map", description="View your current map", guild=guild)
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

def rand1to100():
    rand_bytes = os.urandom(4)
    rand_int = int.from_bytes(rand_bytes, "big")  # convert to integer
    return (rand_int % 100)

@bot.tree.command(name="ssal_muck", description="Chance to ssal muck free resources", guild=guild)
async def ssal_muck(interaction: discord.Interaction):
    scraped, left, cooldown = scrape(GUILD_ID, interaction.user.id, datetime.now(timezone.utc), timedelta(minutes=10))
    if scraped:
        num = rand1to100()
        reward = "500 coins"
        if num == 1:
            reward = "5 rare ticket"
        elif num < 6:
            reward = "1 rare ticket"

        embed = discord.Embed(
            title="SSAL MUCK",
            description=f"You searched nearby rice field to find resources.\n\n - You found {reward} from nearby rice. \n\n You have {left} more ssal muck left.",
            color=0x5865F2,
        )
    else:
        embed = discord.Embed(
            title="SSAL MUCK",
            description=f"You need to wait {cooldown // 60} minutes and {cooldown % 60} seconds before next ssal muck. Be more patient to become king of ssal muck!\n\n",
            color=0x5865F2,
        )
    file = discord.File("assets/ssal.png", filename="ssal.png")

    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)

    view = GeneralView()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="cats", description="View your current map", guild=guild)
async def cats(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Cats",
        description="Here is your current map:\n**[Map]**\n- Location: Town Center\n- Area: Forest\n\n",
        color=0x5865F2,
    )
    file = discord.File("assets/rare_ticket.png", filename="rare_ticket.png")

    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)

    view = ImageButtons()
    await interaction.response.send_message(embed=embed, file=file, view=view)

@bot.tree.command(name="test", guild=guild)
async def test(interaction: discord.Interaction):
    await interaction.response.send_message("Hello World!")

@bot.tree.command(name="test2", guild=guild)
async def test2(interaction: discord.Interaction):
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