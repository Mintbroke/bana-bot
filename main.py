from datetime import timedelta, datetime
import random
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands
import psycopg2
import os
import asyncio, signal
from dotenv import load_dotenv
load_dotenv()

# Load environment variables or set your credentials here
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
print(DISCORD_TOKEN[:10])
GUILD_ID = os.getenv("GUILD_ID")
# Set up database connection
def get_db_connection():
    return psycopg2.connect(
        os.getenv("DB_URL")
    )

async def runner():
    token = DISCORD_TOKEN
    stop = asyncio.Event()

    def _stop():
        asyncio.get_running_loop().create_task(bot.close())
        stop.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, _stop)

    # start the bot
    print("Starting bot")
    bot_task = asyncio.create_task(bot.run(token))
    await run_health_server()
    await stop.wait()
    bot_task

async def health(_): 
    return web.Response(text="ok")

async def run_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


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
                print("Ensured schema")
    except Exception as e:
        print(f"Error ensuring schema: {e}")
    finally:
        conn.close()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
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
    _claim_daily(GUILD_ID, interaction.user.id, datetime.utcnow(), 1000, timedelta(hours=24))
    await interaction.response.send_message(
            "Trying to claim daily.", ephemeral=True
    )

def _claim_daily(guild_id: int, user_id: int,
                 now_utc: datetime, amount: int, cooldown: timedelta):
    """
    Returns: (granted: bool, balance: int, remaining_seconds: int | None)
    """
    cooldown_secs = int(cooldown.total_seconds())
    con = get_db_connection()
    try:
        with con:
            with con.cursor() as cur:
                # 1) Try to grant if row exists AND cooldown passed
                cur.execute(
                    """
                    UPDATE test_table1
                       SET balance = balance + %s,
                           num_tickets = num_tickets + 10,
                           last_daily = %s
                     WHERE guild_id = %s
                       AND user_id  = %s
                       AND (last_daily IS NULL
                            OR EXTRACT(EPOCH FROM (%s - last_daily)) >= %s)
                    RETURNING balance;
                    """,
                    (amount, now_utc, guild_id, user_id, now_utc, cooldown_secs)
                )
                row = cur.fetchone()
                if row:
                    return True, int(row[0]), int(row[1]), None

                # 2) If not granted, try to INSERT first-time claim
                cur.execute(
                    """
                    INSERT INTO test_table1 (guild_id, user_id, balance, num_tickets, last_daily)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (guild_id, user_id) DO NOTHING
                    RETURNING balance;
                    """,
                    (guild_id, user_id, amount, 10, now_utc)
                )
                row = cur.fetchone()
                if row:
                    # Insert succeeded → first-ever claim granted
                    return True, int(row[0]), int(row[1]), None

                # 3) Row exists but cooldown not met → compute remaining
                cur.execute(
                    "SELECT balance, num_tickets, last_daily FROM test_table1 WHERE guild_id=%s AND user_id=%s",
                    (guild_id, user_id)
                )
                bal, ticket, last_daily = cur.fetchone()
                delta = (now_utc - last_daily).total_seconds()
                remaining = max(0, cooldown_secs - int(delta))
                return False, int(bal), int(ticket), remaining
    finally:
        con.close()

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

    view = ImageButtons()
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

@bot.tree.command(name="scrap", description="Chance to win rare ticket", guild=guild)
async def scrap(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Scrap",
        description="Here is your current map:\n**[Map]**\n- Location: Town Center\n- Area: Forest\n\n",
        color=0x5865F2,
    )
    file = discord.File("assets/rare_ticket.png", filename="rare_ticket.png")

    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)

    view = ImageButtons()
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
    # bot.run(DISCORD_TOKEN)
    asyncio.run(runner())