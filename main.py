from discord.ext import tasks
from datetime import datetime, timezone, timedelta
import random
import logging, sys
import aiohttp
import asyncio
from io import BytesIO
from textwrap import wrap
from typing import Any, Optional
from PIL import Image, ImageDraw, ImageFont, ImageOps
from bs4 import BeautifulSoup
import discord
from discord import app_commands
from discord.ext import commands
import psycopg2
import os
from cats import Rarity, Cat
from dotenv import load_dotenv
from functions import claim_daily, get_db_connection, scrape, ssal, getTickets
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
USER_ID = int(os.getenv("USER_ID"))

# Set up Discord bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)
guild = discord.Object(id=GUILD_ID)
num_tickets = 10  # Example user data

def rand1to(num):
    rand_bytes = os.urandom(4)
    rand_int = int.from_bytes(rand_bytes, "big")  # convert to integer
    return (rand_int % num)

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

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS test_owned_pals (
                        id BIGSERIAL PRIMARY KEY,
                        guild_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        pal_number TEXT NOT NULL,
                        pal_name TEXT NOT NULL,
                        image_url TEXT NOT NULL,
                        tier_name TEXT NOT NULL,
                        hp_multiplier NUMERIC(6, 2) NOT NULL DEFAULT 1.00,
                        attack_multiplier NUMERIC(6, 2) NOT NULL DEFAULT 1.00,
                        defense_multiplier NUMERIC(6, 2) NOT NULL DEFAULT 1.00,
                        pal_size TEXT NOT NULL DEFAULT 'Normal',
                        test_trait1 TEXT,
                        test_trait2 TEXT,
                        test_trait3 TEXT,
                        test_trait4 TEXT,
                        is_favorite BOOLEAN NOT NULL DEFAULT FALSE,
                        obtained_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                """)
                cur.execute("""
                    ALTER TABLE test_owned_pals
                    ADD COLUMN IF NOT EXISTS is_favorite BOOLEAN NOT NULL DEFAULT FALSE;
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_test_owned_pals_user
                    ON test_owned_pals (
                        guild_id,
                        user_id,
                        is_favorite DESC,
                        obtained_at DESC,
                        id DESC
                    );
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS test_pal_daily_rolls (
                        guild_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        roll_date DATE NOT NULL,
                        roll_count INTEGER NOT NULL DEFAULT 0
                            CHECK (roll_count >= 0 AND roll_count <= 5),
                        PRIMARY KEY (guild_id, user_id, roll_date)
                    );
                """)
                log.info("Ensured schema")
    except Exception as e:
        log.info(f"Error ensuring schema: {e}")
    finally:
        conn.close()

names = ["Balrog", "Luna", "Dasli", "Luno"]

@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user}")
    await bot.tree.sync(guild=guild)
    _ensure_schema()
    rename_loop.start()

class GachaButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.likes = 0

    @discord.ui.button(label="Draw", style=discord.ButtonStyle.success)
    async def draw(self, interaction: discord.Interaction, button: discord.ui.Button):
        log.info("Drawing a cat...")
        cat = rand1to(1000) + 1
        rarity: Rarity = Rarity.RARE
        if cat == 1:
            rarity = Rarity.BANA_RARE
        elif cat <= 50:
            rarity = Rarity.UBER_RARE
        elif cat <= 300:
            rarity = Rarity.SUPER_RARE
        log.info("got rarity...")

        qual = rand1to(1000) + 1
        quality = "C"
        if qual == 1:
            quality = "SS"
        elif qual < 11:
            quality = "S"
        elif qual <= 150:
            quality = "A"
        elif qual <= 500:
            quality = "B"
        log.info("got quality...")
        banner = rand1to(14)
        bannerStr = ""

                
        log.info(f"Drawn cat: Rarity={rarity}, Quality={quality}, Banner={bannerStr}")
        cat = Cat(name="", banner=bannerStr, rarity=rarity, quality=quality, image_url="")
        log.info(cat.name)

        await interaction.response.send_message(
            f"You drew {cat.name}!")

    @discord.ui.button(label="View Rates", style=discord.ButtonStyle.primary)
    async def rate(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "This is an example button under an image embed.", ephemeral=True
        )

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

# TODO: validate user
class GambleMoreView(discord.ui.View):
    def __init__(self, mult):
        super().__init__(timeout=None)
        self.tickets = mult

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Reward",
            description=f"You successfully claimed {self.tickets} rare tickets!\n\n",
            color=0x5865F2,
        )
        await interaction.response.edit_message(
            embed=embed, view=GeneralView()
        )

    @discord.ui.button(label="Nah I'd win", style=discord.ButtonStyle.danger)
    async def win(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Gamble",
            description=f"Pick a most broken champion in league of legends\n\n Your Current Stake: {self.tickets} rare ticket\n\n",
            color=0x5865F2,
        )
        # Tell the embed to use the attached file
        embed.set_image(url="https://cdn.discordapp.com/attachments/928447198746804265/1415487869186998374/rare_ticket.png?ex=68c3634e&is=68c211ce&hm=c53191b9bc20f1ba393755d9b84735a6c99a069ad6b60d84e0e7124fed18eb02&")

        view = GambleView(rand1to(5) + 1, mult=self.tickets * 2)
        await interaction.response.send_message(embed=embed, view=view)
    
class GambleView(discord.ui.View):
    def __init__(self, answer, mult):
        self.answer = answer
        self.mult = mult
        super().__init__(timeout=None)
    
    def get_champ_name(self, num):
        if num == 1:
            return "Gangplank"
        elif num == 2:
            return "Ahri"
        elif num == 3:
            return "Yone"
        elif num == 4:
            return "Brand"
        elif num == 5:
            return "Sylas"
        
    def get_image_path(self, num):
        if num == 1:
            return "https://cdn.discordapp.com/attachments/928447198746804265/1415487870872846406/gp.png?ex=68c3634e&is=68c211ce&hm=cadbc14372766f3741290f43431c197fc38fe9e974bf2c05d342859e6fbf2fe2&"
        elif num == 2:
            return "https://cdn.discordapp.com/attachments/928447198746804265/1415487869514022922/fox.png?ex=68c3634e&is=68c211ce&hm=f3ff970d515024b29543d66b507a4b0a068eba8a740a4569527349ddfe356c3b&"
        elif num == 3:
            return "https://cdn.discordapp.com/attachments/928447198746804265/1415487870537433098/yon.png?ex=68c3634e&is=68c211ce&hm=6cf34a776c34a770d57fb4b6b67bd6faec09a3e10a0d81808dd9333548602003&"
        elif num == 4:
            return "https://cdn.discordapp.com/attachments/928447198746804265/1415487869828464801/b.png?ex=68c3634e&is=68c211ce&hm=9e900d02a3887ff36bc00225d4666761a95543bb8c761b841915ceda8c1c730f&"
        elif num == 5:
            return "https://cdn.discordapp.com/attachments/928447198746804265/1415487870202019871/syl.png?ex=68c3634e&is=68c211ce&hm=ecee15df2286c99bea0ede234c52f81e3c7958322dc4ebc2a7dd4656d1412046&"

    @discord.ui.button(label="💣Gangplank", style=discord.ButtonStyle.primary)
    async def Gangplank(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.answer == 1:
            embed = discord.Embed(
                title="Result!",
                description=f"You are correct! You are rewarded with 1 rare ticket for finding a broken champ! \n\n Would you be 99% of gamblers and claim your reward or be the 1% and gamble for {self.mult*2} ticket?",
                color=discord.Color.green()
            )
            embed.set_image(url=self.get_image_path(self.answer))
            await interaction.response.edit_message(embed=embed, view=GambleMoreView(mult=self.mult*2))
        else:
            embed = discord.Embed(
                title="Result!",
                description=f"Wrong! Your guess: GangPlank\n\nBroken champ was {self.get_champ_name(self.answer)}. Better luck next time.",
                color=discord.Color.red()
            )
            embed.set_image(url=self.get_image_path(self.answer))
            await interaction.response.edit_message(embed=embed, view=GeneralView())

    @discord.ui.button(label="🦊Ahri", style=discord.ButtonStyle.primary)
    async def Ahri(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.answer == 2:
            embed = discord.Embed(
                title="Result!",
                description=f"You are correct! You are rewarded with 1 rare ticket for finding a broken champ! \n\n Would you be 99% of gamblers and claim your reward or be the 1% and gamble for double ticket?",
                color=discord.Color.green()
            )
            embed.set_image(url=self.get_image_path(self.answer))
            await interaction.response.edit_message(embed=embed, view=GambleMoreView())
        else:
            embed = discord.Embed(
                title="Result!",
                description=f"Wrong! Your guess: Ahri\n\nBroken champ was {self.get_champ_name(self.answer)}. Better luck next time.",
                color=discord.Color.red()
            )
            embed.set_image(url=self.get_image_path(self.answer))
            await interaction.response.edit_message(embed=embed, view=GeneralView())
    
    @discord.ui.button(label="🗡️Yone", style=discord.ButtonStyle.primary)
    async def Yone(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.answer == 3:
            embed = discord.Embed(
                title="Result!",
                description=f"You are correct! You are rewarded with 1 rare ticket for finding a broken champ! \n\n Would you be 99% of gamblers and claim your reward or be the 1% and gamble for double ticket?",
                color=discord.Color.green()
            )
            embed.set_image(url=self.get_image_path(self.answer))
            await interaction.response.edit_message(embed=embed, view=GambleMoreView())
        else:
            embed = discord.Embed(
                title="Result!",
                description=f"Wrong! Your guess: Yone\n\nBroken champ was {self.get_champ_name(self.answer)}. Better luck next time.",
                color=discord.Color.red()
            )
            embed.set_image(url=self.get_image_path(self.answer))
            await interaction.response.edit_message(embed=embed, view=GeneralView())

    @discord.ui.button(label="🔥Brand", style=discord.ButtonStyle.primary)
    async def Brand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.answer == 4:
            embed = discord.Embed(
                title="Result!",
                description=f"You are correct! You are rewarded with 1 rare ticket for finding a broken champ! \n\n Would you be 99% of gamblers and claim your reward or be the 1% and gamble for double ticket?",
                color=discord.Color.green()
            )
            embed.set_image(url=self.get_image_path(self.answer))
            await interaction.response.edit_message(embed=embed, view=GambleMoreView())
        else:
            embed = discord.Embed(
                title="Result!",
                description=f"Wrong! Your guess: Brand\n\nBroken champ was {self.get_champ_name(self.answer)}. Better luck next time.",
                color=discord.Color.red()
            )
            embed.set_image(url=self.get_image_path(self.answer))
            await interaction.response.edit_message(embed=embed, view=GeneralView())
    @discord.ui.button(label="⛓️‍💥Sylas", style=discord.ButtonStyle.primary)

    async def Sylas(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.answer == 5:
            embed = discord.Embed(
                title="Result!",
                description=f"You are correct! You are rewarded with 1 rare ticket for finding a broken champ! \n\n Would you be 99% of gamblers and claim your reward or be the 1% and gamble for double ticket?",
                color=discord.Color.green()
            )
            embed.set_image(url=self.get_image_path(self.answer))
            await interaction.response.edit_message(embed=embed, view=GambleMoreView())
        else:
            embed = discord.Embed(
                title="Result!",
                description=f"Wrong! Your guess: Sylas\n\nBroken champ was {self.get_champ_name(self.answer)}. Better luck next time.",
                color=discord.Color.red()
            )
            embed.set_image(url=self.get_image_path(self.answer))
            await interaction.response.edit_message(embed=embed, view=GeneralView())


@bot.tree.command(name="gacha", description="You can spend rare ticket to draw cats in a random banner", guild=guild)
async def gacha(interaction: discord.Interaction):
    tickets = getTickets(GUILD_ID, interaction.user.id)
    embed = discord.Embed(
        title="Gacha",
        description=f"""You can spend rare ticket to draw cats in a random banner\n\nYour Rare Tickets: {tickets}\n\n
        **[Rarity]**\n- Bana Rare: 0.1%\n- Uber Rare: 4.9%\n- Super Rare: 25%\n- Rare: 70%\n\n
        **[Quality]**\n- C: 49%\n- B: 35%\n- A: 15%\n- S: 0.9%\n- SS: 0.1%\n
        """,
        color=0x5865F2,
    )
    file = discord.File("assets/gacha.png", filename="gacha.png")
    
    # Tell the embed to use the attached file
    embed.set_image(url=file.uri)


    view = GachaButtons()
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



@tasks.loop(hours=24)
async def rename_loop():
    global index

    guild = bot.get_guild(GUILD_ID)
    member = await guild.fetch_member(USER_ID)
    username = member.nick
    name_split = username.split("-")
    days = int(name_split[1]) - 1
    new_name = name_split[0] + "-" + str(days)

    if member:
        await member.edit(nick=new_name)

def parse_duration(duration: str) -> timedelta:
    time_units = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400
    }

    unit = duration[-1]
    if unit not in time_units:
        raise ValueError("Invalid time unit")

    amount = int(duration[:-1])
    return timedelta(seconds=amount * time_units[unit])


@bot.tree.command(name="bana_timeout", description="Timeout a user", guild=guild)
@app_commands.describe(user="User to timeout", duration="Duration (e.g. 10m, 1h)")
@app_commands.checks.has_permissions(administrator=True)
async def bana_timeout(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str):
    
    # Prevent self-timeout
    if user == interaction.user:
        await interaction.response.send_message("You can't timeout yourself.", ephemeral=True)
        return

    # Role hierarchy check (user vs target)
    if user.top_role >= interaction.user.top_role:
        await interaction.response.send_message(
            "You can't timeout someone with equal or higher role.",
            ephemeral=True
        )
        return

    # Bot role hierarchy check
    if user.top_role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "I can't timeout this user (role too high).",
            ephemeral=True
        )
        return

    try:
        delta = parse_duration(duration)
    except:
        await interaction.response.send_message(
            "Invalid duration format. Use like `10m`, `1h`, `30s`.",
            ephemeral=True
        )
        return

    try:
        await user.timeout(delta, reason=f"Timed out by {interaction.user}")
        await interaction.response.send_message(
            f"{user.mention} has been timed out for `{duration}` for following reason: `{reason}`."
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "I don't have permission to timeout this user.",
            ephemeral=True
        )



@bot.tree.command(name="gamble", description="Gamble your coins for a chance to win rare tickets", guild=guild)
async def gamble(interaction: discord.Interaction, multiplier: int = 1):
    embed = discord.Embed(
        title="Gamble",
        description="You can gamble your coins for a chance to win rare tickets!\n**[Gamble]**\n- Cost: 1000 Coins\n\n Pick a most broken champion in league of legends\n\n",
        color=0x5865F2,
    )
    # Tell the embed to use the attached file
    embed.set_image(url="https://cdn.discordapp.com/attachments/928447198746804265/1415487869186998374/rare_ticket.png?ex=68c3634e&is=68c211ce&hm=c53191b9bc20f1ba393755d9b84735a6c99a069ad6b60d84e0e7124fed18eb02&")

    view = GambleView(rand1to(5) + 1, mult=multiplier)
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="deck", description="Gamble your coins for a chance to win rare tickets", guild=guild)
async def deck(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Deck",
        description="Not implemented\n",
        color=0x5865F2,
    )

    view = ImageButtons()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="stats", description="View your current stats", guild=guild)
async def stats(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Stats",
        description="Not implemented\n",
        color=0x5865F2,
    )
    view = ImageButtons()
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="upgrade", description="Upgrade your inventory", guild=guild)
async def upgrade(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Upgrade",
        description="Not implemented\n",
        color=0x5865F2,
    )
    view = ImageButtons()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="map", description="View your current map", guild=guild)
async def map(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Map",
        description="Not implemented\n",
        color=0x5865F2,
    )
    view = ImageButtons()
    await interaction.response.send_message(embed=embed, view=view)

# TODO: fix by using universal time
@bot.tree.command(name="ssal_muck", description="Chance to ssal muck free resources", guild=guild)
async def ssal_muck(interaction: discord.Interaction):
    scraped, left, cooldown = scrape(GUILD_ID, interaction.user.id, datetime.now(timezone.utc), timedelta(minutes=10))
    if scraped:
        num = rand1to(100)
        reward = "500 coins"
        if num == 1:
            reward = "5 rare ticket"
            ssal(GUILD_ID, interaction.user.id, 5, 0)
        elif num < 6:
            reward = "1 rare ticket"
            ssal(GUILD_ID, interaction.user.id, 1, 0)
        else:
            ssal(GUILD_ID, interaction.user.id, 0, 500)

        embed = discord.Embed(
            title="SSAL MUCK",
            description=f"You searched nearby rice field to find resources.\n\n - You found {reward} from nearby rice stash. \n\n You have {30 - left} more ssal muck left today.",
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
        description="Not implemented\n",
        color=0x5865F2,
    )

    view = ImageButtons()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="test", guild=guild)
async def test(interaction: discord.Interaction):
    await interaction.response.send_message("Hello World!")

PALS_JSON_URL = (
    "https://raw.githubusercontent.com/mlg404/"
    "palworld-paldex-api/main/src/pals.json"
)

IMAGE_BASE_URL = (
    "https://raw.githubusercontent.com/mlg404/"
    "palworld-paldex-api/main"
)


PAL_TIERS: dict[str, dict[str, Any]] = {
    "Normal": {
        "weight": 7000,
        "emoji": "⚪",
        "stars": "★",
        "color": discord.Color.light_grey(),
        "message": "A regular Pal has appeared.",
        "bonus": "No special tier bonus",
        "size": "Normal",
        "hp_multiplier": 1.00,
        "attack_multiplier": 1.00,
        "defense_multiplier": 1.00,
        "trait_count": 0,
    },
    "Uncommon": {
        "weight": 2000,
        "emoji": "🟢",
        "stars": "★★",
        "color": discord.Color.green(),
        "message": "An uncommon Pal has appeared!",
        "bonus": "+5% to all stored stat multipliers",
        "size": "Normal",
        "hp_multiplier": 1.05,
        "attack_multiplier": 1.05,
        "defense_multiplier": 1.05,
        "trait_count": 1,
    },
    "Rare": {
        "weight": 750,
        "emoji": "🔵",
        "stars": "★★★",
        "color": discord.Color.blue(),
        "message": "A rare Pal has appeared!",
        "bonus": "+10% to all stored stat multipliers",
        "size": "Normal",
        "hp_multiplier": 1.10,
        "attack_multiplier": 1.10,
        "defense_multiplier": 1.10,
        "trait_count": 2,
    },
    "Alpha": {
        "weight": 200,
        "emoji": "🔴",
        "stars": "★★★★",
        "color": discord.Color.red(),
        "message": "An enormous Alpha Pal has appeared!",
        "bonus": "Large size, +20% HP, +5% Attack, +10% Defense",
        "size": "Large",
        "hp_multiplier": 1.20,
        "attack_multiplier": 1.05,
        "defense_multiplier": 1.10,
        "trait_count": 2,
    },
    "Lucky": {
        "weight": 45,
        "emoji": "✨",
        "stars": "★★★★★",
        "color": discord.Color.gold(),
        "message": "A sparkling Lucky Pal has appeared!",
        "bonus": "Large size and +15% to all stored stat multipliers",
        "size": "Large",
        "hp_multiplier": 1.15,
        "attack_multiplier": 1.15,
        "defense_multiplier": 1.15,
        "trait_count": 3,
    },
    "Mythical": {
        "weight": 5,
        "emoji": "🌌",
        "stars": "★★★★★★",
        "color": discord.Color.purple(),
        "message": "A Mythical Pal has appeared!",
        "bonus": "Enormous size and +30% to all stored stat multipliers",
        "size": "Enormous",
        "hp_multiplier": 1.30,
        "attack_multiplier": 1.30,
        "defense_multiplier": 1.30,
        "trait_count": 4,
    },
}


TEST_TRAITS = [
    "Test Attack",
    "Test Defense",
    "Test Health",
    "Test Speed",
    "Test Worker",
    "Test Hunger",
    "Test Critical",
    "Test Shiny",
]


def roll_pal_tier() -> tuple[str, dict[str, Any]]:
    """Roll one custom rarity tier."""
    names = list(PAL_TIERS.keys())
    weights = [PAL_TIERS[name]["weight"] for name in names]

    tier_name = random.choices(
        population=names,
        weights=weights,
        k=1,
    )[0]

    return tier_name, PAL_TIERS[tier_name]


def generate_test_traits(tier_name: str) -> list[str]:
    """Generate placeholder traits according to the rolled tier."""
    trait_count = int(PAL_TIERS[tier_name]["trait_count"])
    trait_count = min(trait_count, len(TEST_TRAITS))

    if trait_count <= 0:
        return []

    return random.sample(TEST_TRAITS, trait_count)


def normalize_image_url(image_value: str) -> str:
    """Turn a repository-relative image path into a raw GitHub URL."""
    image_value = str(image_value).strip()

    if image_value.startswith(("https://", "http://")):
        return image_value

    if not image_value.startswith("/"):
        image_value = f"/{image_value}"

    return f"{IMAGE_BASE_URL}{image_value}"


async def fetch_pals() -> list[dict[str, Any]]:
    """
    Load the repository Pal list.

    GitHub raw responds with text/plain, so content_type=None is required.
    """
    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(PALS_JSON_URL) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)

    if not isinstance(data, list) or not data:
        raise ValueError("Pal API returned an empty or invalid list.")

    valid_pals: list[dict[str, Any]] = []

    for pal in data:
        if not isinstance(pal, dict):
            continue

        if not all(key in pal for key in ("key", "name", "image")):
            continue

        valid_pals.append(pal)

    if not valid_pals:
        raise ValueError("Pal API returned no usable Pal records.")

    return valid_pals


PAL_DAILY_ROLL_LIMIT = 5


def reserve_daily_pal_roll(*, guild_id: int, user_id: int) -> Optional[int]:
    """Atomically reserve one UTC-day roll and return the new count."""
    conn = get_db_connection()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO test_pal_daily_rolls (
                        guild_id,
                        user_id,
                        roll_date,
                        roll_count
                    )
                    VALUES (
                        %s,
                        %s,
                        (NOW() AT TIME ZONE 'UTC')::date,
                        1
                    )
                    ON CONFLICT (guild_id, user_id, roll_date)
                    DO UPDATE SET
                        roll_count = test_pal_daily_rolls.roll_count + 1
                    WHERE test_pal_daily_rolls.roll_count < %s
                    RETURNING roll_count;
                    """,
                    (guild_id, user_id, PAL_DAILY_ROLL_LIMIT),
                )
                row = cur.fetchone()
                return int(row[0]) if row is not None else None
    finally:
        conn.close()


def release_daily_pal_roll(*, guild_id: int, user_id: int) -> None:
    """Return a reserved roll when the draw fails before the Pal is saved."""
    conn = get_db_connection()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE test_pal_daily_rolls
                    SET roll_count = GREATEST(roll_count - 1, 0)
                    WHERE guild_id = %s
                      AND user_id = %s
                      AND roll_date = (NOW() AT TIME ZONE 'UTC')::date;
                    """,
                    (guild_id, user_id),
                )
                cur.execute(
                    """
                    DELETE FROM test_pal_daily_rolls
                    WHERE guild_id = %s
                      AND user_id = %s
                      AND roll_date = (NOW() AT TIME ZONE 'UTC')::date
                      AND roll_count = 0;
                    """,
                    (guild_id, user_id),
                )
    finally:
        conn.close()


def get_next_utc_reset_timestamp() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    reset = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
    return int(reset.timestamp())


def save_test_owned_pal(
    *,
    guild_id: int,
    user_id: int,
    pal_number: str,
    pal_name: str,
    image_url: str,
    tier_name: str,
    hp_multiplier: float,
    attack_multiplier: float,
    defense_multiplier: float,
    pal_size: str,
    traits: Optional[list[str]] = None,
) -> int:
    """Store one individual Pal and return its generated database ID."""
    padded_traits = ((traits or []) + [None, None, None, None])[:4]

    conn = get_db_connection()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO test_owned_pals (
                        guild_id,
                        user_id,
                        pal_number,
                        pal_name,
                        image_url,
                        tier_name,
                        hp_multiplier,
                        attack_multiplier,
                        defense_multiplier,
                        pal_size,
                        test_trait1,
                        test_trait2,
                        test_trait3,
                        test_trait4
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    RETURNING id;
                    """,
                    (
                        guild_id,
                        user_id,
                        pal_number,
                        pal_name,
                        image_url,
                        tier_name,
                        hp_multiplier,
                        attack_multiplier,
                        defense_multiplier,
                        pal_size,
                        padded_traits[0],
                        padded_traits[1],
                        padded_traits[2],
                        padded_traits[3],
                    ),
                )

                row = cur.fetchone()

                if row is None:
                    raise RuntimeError(
                        "Database did not return the inserted Pal ID."
                    )

                return int(row[0])

    finally:
        conn.close()


def get_test_owned_pals(
    *,
    guild_id: int,
    user_id: int,
) -> list[dict[str, Any]]:
    """Return all Pals owned by one user in newest-first order."""
    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    pal_number,
                    pal_name,
                    image_url,
                    tier_name,
                    hp_multiplier,
                    attack_multiplier,
                    defense_multiplier,
                    pal_size,
                    test_trait1,
                    test_trait2,
                    test_trait3,
                    test_trait4,
                    is_favorite,
                    obtained_at
                FROM test_owned_pals
                WHERE guild_id = %s
                  AND user_id = %s
                ORDER BY is_favorite DESC, obtained_at DESC, id DESC;
                """,
                (guild_id, user_id),
            )

            rows = cur.fetchall()

        pals: list[dict[str, Any]] = []

        for row in rows:
            pals.append(
                {
                    "id": int(row[0]),
                    "pal_number": str(row[1]),
                    "pal_name": str(row[2]),
                    "image_url": str(row[3]),
                    "tier_name": str(row[4]),
                    "hp_multiplier": float(row[5]),
                    "attack_multiplier": float(row[6]),
                    "defense_multiplier": float(row[7]),
                    "pal_size": str(row[8]),
                    "test_trait1": row[9],
                    "test_trait2": row[10],
                    "test_trait3": row[11],
                    "test_trait4": row[12],
                    "is_favorite": bool(row[13]),
                    "obtained_at": row[14],
                }
            )

        return pals

    finally:
        conn.close()


def get_tier_config(tier_name: str) -> dict[str, Any]:
    """Return tier styling, with a safe fallback for old database rows."""
    return PAL_TIERS.get(
        tier_name,
        {
            "emoji": "⚪",
            "stars": "★",
            "color": discord.Color.blurple(),
            "message": "A Pal has appeared.",
            "bonus": "Unknown",
        },
    )


def get_pal_display_name(
    pal_name: str,
    tier_name: str,
) -> str:
    if tier_name in {"Alpha", "Lucky", "Mythical"}:
        return f"{tier_name} {pal_name}"

    return pal_name


def get_trait_lines(pal: dict[str, Any]) -> list[str]:
    traits = [
        pal.get("test_trait1"),
        pal.get("test_trait2"),
        pal.get("test_trait3"),
        pal.get("test_trait4"),
    ]

    return [
        f"`test_trait{index}` — {trait}"
        for index, trait in enumerate(traits, start=1)
        if trait
    ]


def create_draw_embed(
    *,
    pal_number: str,
    pal_name: str,
    image_url: str,
    tier_name: str,
    tier: dict[str, Any],
    traits: list[str],
    owned_pal_id: int,
) -> discord.Embed:
    display_name = get_pal_display_name(pal_name, tier_name)

    embed = discord.Embed(
        title=(
            f"{tier['emoji']} {display_name} "
            f"{tier['emoji']}"
        ),
        description=(
            f"## {tier['stars']}\n"
            f"*{tier['message']}*\n\n"
            f"**Paldeck Number:** `#{pal_number}`"
        ),
        color=tier["color"],
    )

    embed.set_image(url=image_url)

    embed.add_field(
        name="Rarity",
        value=f"{tier['emoji']} **{tier_name}**",
        inline=True,
    )

    embed.add_field(
        name="Size",
        value=str(tier["size"]),
        inline=True,
    )

    embed.add_field(
        name="Tier Bonus",
        value=str(tier["bonus"]),
        inline=False,
    )

    embed.add_field(
        name="Stat Multipliers",
        value=(
            f"❤️ HP: **x{tier['hp_multiplier']:.2f}**\n"
            f"⚔️ Attack: **x{tier['attack_multiplier']:.2f}**\n"
            f"🛡️ Defense: **x{tier['defense_multiplier']:.2f}**"
        ),
        inline=False,
    )

    if traits:
        trait_text = "\n".join(
            f"`test_trait{index}` — {trait}"
            for index, trait in enumerate(traits, start=1)
        )
    else:
        trait_text = "No test traits"

    embed.add_field(
        name="Traits",
        value=trait_text,
        inline=False,
    )

    embed.set_footer(
        text=(
            f"Owned Pal ID: {owned_pal_id} • "
            "Added to your collection"
        )
    )

    return embed


def set_test_owned_pal_favorite(
    *,
    guild_id: int,
    user_id: int,
    owned_pal_id: int,
    is_favorite: bool,
) -> bool:
    """Favorite/unfavorite one Pal only when it belongs to the user."""
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE test_owned_pals
                    SET is_favorite = %s
                    WHERE id = %s
                      AND guild_id = %s
                      AND user_id = %s
                    RETURNING id;
                    """,
                    (is_favorite, owned_pal_id, guild_id, user_id),
                )
                return cur.fetchone() is not None
    finally:
        conn.close()


def favorite_marker(pal: dict[str, Any]) -> str:
    return "⭐ " if pal.get("is_favorite") else ""


def create_inventory_page_embed(
    *,
    pals: list[dict[str, Any]],
    page: int,
    owner: discord.Member | discord.User,
    per_page: int = 5,
) -> discord.Embed:
    total_pages = max(1, (len(pals) + per_page - 1) // per_page)
    start = page * per_page
    page_pals = pals[start:start + per_page]

    embed = discord.Embed(
        title="Pal Inventory",
        description=(
            "⭐ Favorites are shown first. Press a numbered button to view "
            "that Pal's full information."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_author(
        name=f"{owner.display_name}'s Pal Collection",
        icon_url=owner.display_avatar.url,
    )

    for slot, pal in enumerate(page_pals, start=1):
        tier = get_tier_config(pal["tier_name"])
        display_name = get_pal_display_name(pal["pal_name"], pal["tier_name"])
        embed.add_field(
            name=f"{slot}. {favorite_marker(pal)}{tier['emoji']} {display_name}",
            value=(
                f"Paldeck `#{pal['pal_number']}` • **{pal['tier_name']}**\n"
                f"Owned ID: `{pal['id']}`"
            ),
            inline=False,
        )

    embed.set_footer(
        text=f"Page {page + 1} of {total_pages} • {len(pals)} total Pals"
    )
    return embed


def create_inventory_detail_embed(
    *,
    pal: dict[str, Any],
    owner: discord.Member | discord.User,
    position: int,
    total_pals: int,
) -> discord.Embed:
    tier_name = pal["tier_name"]
    tier = get_tier_config(tier_name)
    display_name = get_pal_display_name(pal["pal_name"], tier_name)
    favorite_text = "⭐ Favorite" if pal.get("is_favorite") else "Not favorited"

    embed = discord.Embed(
        title=f"{favorite_marker(pal)}{tier['emoji']} {display_name} {tier['emoji']}",
        description=(
            f"**Paldeck Number:** `#{pal['pal_number']}`\n"
            f"**Owned Pal ID:** `{pal['id']}`\n"
            f"**Favorite:** {favorite_text}"
        ),
        color=tier["color"],
    )
    embed.set_image(url=pal["image_url"])
    embed.set_author(
        name=f"Owned by {owner.display_name}",
        icon_url=owner.display_avatar.url,
    )
    embed.add_field(
        name="Rarity",
        value=f"{tier['emoji']} **{tier_name}**",
        inline=True,
    )
    embed.add_field(name="Size", value=pal["pal_size"], inline=True)
    embed.add_field(
        name="Stat Multipliers",
        value=(
            f"❤️ HP: **x{pal['hp_multiplier']:.2f}**\n"
            f"⚔️ Attack: **x{pal['attack_multiplier']:.2f}**\n"
            f"🛡️ Defense: **x{pal['defense_multiplier']:.2f}**"
        ),
        inline=False,
    )
    trait_lines = get_trait_lines(pal)
    embed.add_field(
        name="Traits",
        value="\n".join(trait_lines) if trait_lines else "No test traits",
        inline=False,
    )
    if pal.get("obtained_at") is not None:
        embed.add_field(
            name="Obtained",
            value=discord.utils.format_dt(pal["obtained_at"], style="F"),
            inline=False,
        )
    embed.set_footer(text=f"Pal {position + 1} of {total_pals}")
    return embed


class PullFavoriteView(discord.ui.View):
    """Favorite a newly pulled Pal directly from its result message."""
    def __init__(self, *, guild_id: int, owner_id: int, owned_pal_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.owned_pal_id = owned_pal_id
        self.is_favorite = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the user who pulled this Pal can favorite it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="☆ Favorite", style=discord.ButtonStyle.secondary)
    async def favorite_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.is_favorite = not self.is_favorite
        changed = set_test_owned_pal_favorite(
            guild_id=self.guild_id,
            user_id=self.owner_id,
            owned_pal_id=self.owned_pal_id,
            is_favorite=self.is_favorite,
        )
        if not changed:
            await interaction.response.send_message(
                "That Pal could not be found in your collection.", ephemeral=True
            )
            return
        button.label = "⭐ Favorited" if self.is_favorite else "☆ Favorite"
        button.emoji = None
        button.style = (
            discord.ButtonStyle.success
            if self.is_favorite
            else discord.ButtonStyle.secondary
        )
        await interaction.response.edit_message(view=self)


class PalInventoryView(discord.ui.View):
    PER_PAGE = 5

    def __init__(
        self,
        *,
        owner_id: int,
        owner: discord.Member | discord.User,
        pals: list[dict[str, Any]],
        guild_id: int,
    ):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.owner = owner
        self.guild_id = guild_id
        self.pals = pals
        self.page = 0
        self.selected_index: Optional[int] = None
        self.update_buttons()

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.pals) + self.PER_PAGE - 1) // self.PER_PAGE)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This is not your Pal collection.", ephemeral=True
            )
            return False
        return True

    def page_pals(self) -> list[dict[str, Any]]:
        start = self.page * self.PER_PAGE
        return self.pals[start:start + self.PER_PAGE]

    def update_buttons(self) -> None:
        page_count = len(self.page_pals())
        slot_buttons = [self.slot1, self.slot2, self.slot3, self.slot4, self.slot5]
        for index, button in enumerate(slot_buttons):
            button.disabled = self.selected_index is not None or index >= page_count
            button.label = str(index + 1)

        self.previous_page.disabled = self.selected_index is not None or self.page == 0
        self.next_page.disabled = (
            self.selected_index is not None or self.page >= self.total_pages - 1
        )
        self.back_button.disabled = self.selected_index is None
        self.favorite_button.disabled = self.selected_index is None

        if self.selected_index is not None:
            pal = self.pals[self.selected_index]
            self.favorite_button.label = (
                "⭐ Unfavorite" if pal.get("is_favorite") else "☆ Favorite"
            )
            self.favorite_button.emoji = None

    def create_current_embed(self) -> discord.Embed:
        if self.selected_index is not None:
            return create_inventory_detail_embed(
                pal=self.pals[self.selected_index],
                owner=self.owner,
                position=self.selected_index,
                total_pals=len(self.pals),
            )
        return create_inventory_page_embed(
            pals=self.pals,
            page=self.page,
            owner=self.owner,
            per_page=self.PER_PAGE,
        )

    async def select_slot(self, interaction: discord.Interaction, slot: int) -> None:
        index = self.page * self.PER_PAGE + slot
        if index >= len(self.pals):
            await interaction.response.send_message("No Pal is in that slot.", ephemeral=True)
            return
        self.selected_index = index
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_current_embed(), view=self)

    @discord.ui.button(label="1", style=discord.ButtonStyle.primary, row=0)
    async def slot1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.select_slot(interaction, 0)

    @discord.ui.button(label="2", style=discord.ButtonStyle.primary, row=0)
    async def slot2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.select_slot(interaction, 1)

    @discord.ui.button(label="3", style=discord.ButtonStyle.primary, row=0)
    async def slot3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.select_slot(interaction, 2)

    @discord.ui.button(label="4", style=discord.ButtonStyle.primary, row=0)
    async def slot4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.select_slot(interaction, 3)

    @discord.ui.button(label="5", style=discord.ButtonStyle.primary, row=0)
    async def slot5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.select_slot(interaction, 4)

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_current_embed(), view=self)

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_index = None
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_current_embed(), view=self)

    @discord.ui.button(label="☆ Favorite", style=discord.ButtonStyle.success, row=1)
    async def favorite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.selected_index is None:
            return
        pal = self.pals[self.selected_index]
        new_value = not bool(pal.get("is_favorite"))
        changed = set_test_owned_pal_favorite(
            guild_id=self.guild_id,
            user_id=self.owner_id,
            owned_pal_id=pal["id"],
            is_favorite=new_value,
        )
        if not changed:
            await interaction.response.send_message("Pal not found.", ephemeral=True)
            return
        pal["is_favorite"] = new_value
        # Re-sort favorites first, then newest first, and keep selected Pal open.
        selected_id = pal["id"]
        self.pals.sort(
            key=lambda item: (
                not bool(item.get("is_favorite")),
                -(item["obtained_at"].timestamp() if item.get("obtained_at") else 0),
                -item["id"],
            )
        )
        self.selected_index = next(
            i for i, item in enumerate(self.pals) if item["id"] == selected_id
        )
        self.page = self.selected_index // self.PER_PAGE
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_current_embed(), view=self)

    @discord.ui.button(label="Next", emoji="➡️", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_current_embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True




@bot.tree.command(
    name="random_pal",
    description="Draw and store a random Pal (5 rolls per UTC day)",
    guild=guild,
)
async def random_pal(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    if interaction.guild_id is None:
        await interaction.followup.send(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    guild_id = interaction.guild_id
    user_id = interaction.user.id
    reserved_roll = False
    pal_saved = False

    try:
        roll_count = reserve_daily_pal_roll(
            guild_id=guild_id,
            user_id=user_id,
        )

        if roll_count is None:
            reset_timestamp = get_next_utc_reset_timestamp()
            await interaction.followup.send(
                (
                    f"You have used all **{PAL_DAILY_ROLL_LIMIT}** Pal rolls "
                    f"for today. Your rolls reset <t:{reset_timestamp}:R> "
                    f"at <t:{reset_timestamp}:F>."
                ),
                ephemeral=True,
            )
            return

        reserved_roll = True
        rolls_remaining = PAL_DAILY_ROLL_LIMIT - roll_count

        pals = await fetch_pals()
        pal = random.choice(pals)

        pal_number = str(pal["key"])
        pal_name = str(pal["name"])
        image_url = normalize_image_url(str(pal["image"]))

        tier_name, tier = roll_pal_tier()
        traits = generate_test_traits(tier_name)

        owned_pal_id = save_test_owned_pal(
            guild_id=guild_id,
            user_id=user_id,
            pal_number=pal_number,
            pal_name=pal_name,
            image_url=image_url,
            tier_name=tier_name,
            hp_multiplier=float(tier["hp_multiplier"]),
            attack_multiplier=float(tier["attack_multiplier"]),
            defense_multiplier=float(tier["defense_multiplier"]),
            pal_size=str(tier["size"]),
            traits=traits,
        )
        pal_saved = True

        embed = create_draw_embed(
            pal_number=pal_number,
            pal_name=pal_name,
            image_url=image_url,
            tier_name=tier_name,
            tier=tier,
            traits=traits,
            owned_pal_id=owned_pal_id,
        )
        embed.set_footer(
            text=(
                f"Owned Pal ID: {owned_pal_id} • Added to your collection • "
                f"{rolls_remaining}/{PAL_DAILY_ROLL_LIMIT} rolls remaining today"
            )
        )

        announcement: Optional[str] = None

        if tier_name == "Alpha":
            announcement = "🔴 **ALPHA PAL ENCOUNTER!**"
        elif tier_name == "Lucky":
            announcement = "✨ **A LUCKY PAL HAS APPEARED!** ✨"
        elif tier_name == "Mythical":
            announcement = "🌌 **MYTHICAL PULL! INCREDIBLE LUCK!** 🌌"

        pull_view = PullFavoriteView(
            guild_id=guild_id,
            owner_id=user_id,
            owned_pal_id=owned_pal_id,
        )
        await interaction.followup.send(
            content=announcement,
            embed=embed,
            view=pull_view,
        )

    except aiohttp.ClientResponseError as error:
        log.exception("Paldeck HTTP error: %s", error)
        await interaction.followup.send(
            f"Could not load the Paldeck. HTTP status: {error.status}",
            ephemeral=True,
        )

    except (aiohttp.ClientError, KeyError, ValueError, TypeError) as error:
        log.exception("Failed to draw random Pal: %s", error)
        await interaction.followup.send(
            "An error occurred while drawing a Pal. Your roll was returned.",
            ephemeral=True,
        )

    except Exception as error:
        log.exception("Unexpected random Pal error: %s", error)
        await interaction.followup.send(
            "An unexpected error occurred while saving the Pal.",
            ephemeral=True,
        )

    finally:
        if reserved_roll and not pal_saved:
            try:
                release_daily_pal_roll(
                    guild_id=guild_id,
                    user_id=user_id,
                )
            except Exception:
                log.exception(
                    "Failed to return reserved Pal roll for user %s",
                    user_id,
                )


@bot.tree.command(
    name="inventory",
    description="View the Pals stored in your collection",
    guild=guild,
)
async def inventory(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    try:
        if interaction.guild_id is None:
            await interaction.followup.send(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        pals = get_test_owned_pals(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
        )

        if not pals:
            embed = discord.Embed(
                title="Pal Inventory",
                description=(
                    "You do not own any Pals yet.\n\n"
                    "Use `/random_pal` to obtain one."
                ),
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        view = PalInventoryView(
            owner_id=interaction.user.id,
            owner=interaction.user,
            pals=pals,
            guild_id=interaction.guild_id,
        )

        await interaction.followup.send(
            embed=view.create_current_embed(),
            view=view,
        )

    except Exception as error:
        log.exception("Failed to load Pal inventory: %s", error)
        await interaction.followup.send(
            "An error occurred while loading your Pal inventory.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Standalone composite Pal card preview (not stored in the database)
# ---------------------------------------------------------------------------
PASSIVE_SKILLS_API_URL = (
    "https://palworld.wiki.gg/api.php"
    "?action=parse&page=Passive_Skills/List&prop=text&format=json&origin=*"
)

_passive_skill_cache: list[dict[str, Any]] | None = None


async def fetch_palworld_1_0_passive_skills() -> list[dict[str, Any]]:
    """Fetch and cache the passive-skill table used by Palworld 1.0."""
    global _passive_skill_cache

    if _passive_skill_cache:
        return _passive_skill_cache

    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": "DiscordPalBot/1.0 (passive skill card preview)"}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(PASSIVE_SKILLS_API_URL) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)

    html = payload["parse"]["text"]["*"]
    soup = BeautifulSoup(html, "html.parser")
    skills: list[dict[str, Any]] = []

    # The list page's first sortable table contains Name, Pal, Rank, Description...
    for table in soup.select("table.wikitable"):
        headers_text = [
            cell.get_text(" ", strip=True)
            for cell in table.select("tr th")[:12]
        ]
        if "Passive Skill Name" not in headers_text and "Name" not in headers_text:
            continue

        for row in table.select("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 4:
                continue

            name = cells[0].get_text(" ", strip=True)
            rank_text = cells[2].get_text(" ", strip=True)
            description = cells[3].get_text(" ", strip=True)

            if not name or name.lower() in {"passive skill name", "name"}:
                continue

            try:
                rank = int(rank_text.replace("+", "").strip())
            except ValueError:
                rank = 0

            skills.append(
                {
                    "name": name,
                    "rank": rank,
                    "description": description or "No description available.",
                }
            )

        if skills:
            break

    # Keep unique names while preserving table order.
    unique: dict[str, dict[str, Any]] = {}
    for skill in skills:
        unique.setdefault(skill["name"], skill)

    _passive_skill_cache = list(unique.values())

    if not _passive_skill_cache:
        raise ValueError("The Palworld Wiki returned no passive skills.")

    return _passive_skill_cache


def roll_real_passive_traits(
    skills: list[dict[str, Any]],
    tier_name: str,
) -> list[dict[str, Any]]:
    """Roll 0-4 unique real passives, with stronger cards tending to get more."""
    count_weights = {
        "Normal": [45, 35, 15, 4, 1],
        "Uncommon": [20, 40, 28, 10, 2],
        "Rare": [8, 25, 38, 23, 6],
        "Alpha": [4, 18, 38, 30, 10],
        "Lucky": [0, 8, 30, 42, 20],
        "Mythical": [0, 0, 15, 40, 45],
    }
    trait_count = random.choices(
        population=[0, 1, 2, 3, 4],
        weights=count_weights.get(tier_name, count_weights["Normal"]),
        k=1,
    )[0]

    if trait_count == 0:
        return []

    # Rank affects selection weight but negative passives remain possible.
    def skill_weight(skill: dict[str, Any]) -> int:
        rank = int(skill.get("rank", 0))
        if tier_name in {"Lucky", "Mythical"}:
            return max(1, rank + 4)
        if tier_name in {"Rare", "Alpha"}:
            return max(1, rank + 3)
        return max(1, 4 - abs(rank))

    pool = list(skills)
    selected: list[dict[str, Any]] = []

    for _ in range(min(trait_count, len(pool))):
        chosen = random.choices(
            pool,
            weights=[skill_weight(skill) for skill in pool],
            k=1,
        )[0]
        selected.append(chosen)
        pool.remove(chosen)

    return selected


async def download_image_bytes(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": "DiscordPalBot/1.0"}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.read()


def _load_card_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold else
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]

    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue

    return ImageFont.load_default()


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), candidate, font=font)
        if box[2] - box[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def build_pal_card_png(
    pal_png: bytes,
    *,
    pal_number: str,
    pal_name: str,
    tier_name: str,
    tier: dict[str, Any],
    traits: list[dict[str, Any]],
) -> BytesIO:
    """Render a self-contained collectible card into an in-memory PNG."""
    width, height = 900, 1200

    tier_colors = {
        "Normal": ((67, 76, 86), (28, 33, 40)),
        "Uncommon": ((36, 133, 76), (18, 54, 35)),
        "Rare": ((36, 98, 180), (17, 41, 83)),
        "Alpha": ((184, 52, 52), (73, 21, 21)),
        "Lucky": ((224, 174, 37), (89, 60, 8)),
        "Mythical": ((125, 67, 181), (45, 21, 75)),
    }
    accent, dark = tier_colors.get(tier_name, tier_colors["Normal"])

    card = Image.new("RGBA", (width, height), dark + (255,))
    draw = ImageDraw.Draw(card)

    # Layered border and header/footer panels.
    draw.rounded_rectangle((18, 18, 882, 1182), radius=42, fill=accent + (255,))
    draw.rounded_rectangle((34, 34, 866, 1166), radius=34, fill=dark + (255,))
    draw.rounded_rectangle((55, 55, 845, 175), radius=25, fill=accent + (235,))
    draw.rounded_rectangle((55, 195, 845, 715), radius=30, fill=(15, 18, 24, 235))
    draw.rounded_rectangle((55, 735, 845, 1135), radius=30, fill=(12, 15, 20, 235))

    title_font = _load_card_font(48, bold=True)
    subtitle_font = _load_card_font(28, bold=True)
    body_font = _load_card_font(24)
    small_font = _load_card_font(20)
    trait_font = _load_card_font(22, bold=True)

    display_name = get_pal_display_name(pal_name, tier_name)
    draw.text((80, 72), display_name, font=title_font, fill="white")
    number_text = f"#{pal_number}  •  {tier_name.upper()}  •  {tier['stars']}"
    draw.text((82, 132), number_text, font=subtitle_font, fill=(245, 245, 245))

    pal_image = Image.open(BytesIO(pal_png)).convert("RGBA")
    pal_image.thumbnail((700, 480), Image.Resampling.LANCZOS)

    # Give transparent artwork a soft backdrop and center it.
    backdrop = Image.new("RGBA", (740, 480), (255, 255, 255, 18))
    px = (740 - pal_image.width) // 2
    py = (480 - pal_image.height) // 2
    backdrop.alpha_composite(pal_image, (px, py))
    card.alpha_composite(backdrop, (80, 215))

    draw = ImageDraw.Draw(card)
    draw.text((80, 760), "PASSIVE TRAITS", font=subtitle_font, fill=accent)

    y = 810
    if not traits:
        draw.text((82, y), "No passive traits", font=body_font, fill=(215, 215, 215))
    else:
        rank_symbols = {-3: "▼▼▼", -2: "▼▼", -1: "▼", 0: "•", 1: "▲", 2: "▲▲", 3: "▲▲▲", 4: "◆"}
        for trait in traits:
            rank = int(trait.get("rank", 0))
            symbol = rank_symbols.get(rank, f"R{rank}")
            heading = f"{symbol}  {trait['name']}"
            draw.text((82, y), heading, font=trait_font, fill="white")
            y += 31

            lines = _fit_text(
                draw,
                str(trait.get("description", "")),
                small_font,
                700,
            )[:2]
            for line in lines:
                draw.text((110, y), line, font=small_font, fill=(205, 210, 218))
                y += 25
            y += 13

    draw.line((80, 1082, 820, 1082), fill=accent, width=3)
    draw.text(
        (82, 1097),
        "Preview only • This Pal is not added to your database",
        font=small_font,
        fill=(205, 210, 218),
    )

    output = BytesIO()
    card.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


@bot.tree.command(
    name="pal_card",
    description="Generate a random Pal trading-card preview without saving it",
    guild=guild,
)
async def pal_card(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    try:
        pals, passive_skills = await asyncio.gather(
            fetch_pals(),
            fetch_palworld_1_0_passive_skills(),
        )

        pal = random.choice(pals)
        pal_number = str(pal["key"])
        pal_name = str(pal["name"])
        image_url = normalize_image_url(str(pal["image"]))

        tier_name, tier = roll_pal_tier()
        traits = roll_real_passive_traits(passive_skills, tier_name)
        pal_png = await download_image_bytes(image_url)

        card_buffer = await asyncio.to_thread(
            build_pal_card_png,
            pal_png,
            pal_number=pal_number,
            pal_name=pal_name,
            tier_name=tier_name,
            tier=tier,
            traits=traits,
        )

        filename = f"pal_card_{pal_number}_{interaction.user.id}.png"
        file = discord.File(card_buffer, filename=filename)

        embed = discord.Embed(
            title=f"{tier['emoji']} {get_pal_display_name(pal_name, tier_name)}",
            description=(
                f"**Paldeck:** `#{pal_number}`\n"
                f"**Tier:** {tier_name}\n"
                f"**Passives:** {len(traits)}/4\n\n"
                "This is a preview and was **not** stored."
            ),
            color=tier["color"],
        )
        embed.set_image(url=f"attachment://{filename}")

        await interaction.followup.send(embed=embed, file=file)

    except aiohttp.ClientResponseError as error:
        log.exception("Pal card HTTP error: %s", error)
        await interaction.followup.send(
            f"Could not load card data. HTTP status: {error.status}",
            ephemeral=True,
        )
    except Exception as error:
        log.exception("Failed to generate Pal card: %s", error)
        await interaction.followup.send(
            "An error occurred while generating the Pal card.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Interactive five-card Pal deck preview (not stored in the database)
# ---------------------------------------------------------------------------
def roll_guaranteed_two_star_tier() -> tuple[str, dict[str, Any]]:
    """Roll a tier of Uncommon (2 stars) or better."""
    eligible_names = [
        name for name, config in PAL_TIERS.items()
        if len(str(config.get("stars", ""))) >= 2
    ]
    eligible_weights = [PAL_TIERS[name]["weight"] for name in eligible_names]
    tier_name = random.choices(
        eligible_names,
        weights=eligible_weights,
        k=1,
    )[0]
    return tier_name, PAL_TIERS[tier_name]


def build_pal_pack_cover_png(
    preview_images: list[bytes],
    *,
    owner_name: str,
) -> BytesIO:
    """Create a sealed Pal deck cover using a few pulled Pal images."""
    width, height = 900, 1200
    card = Image.new("RGBA", (width, height), (14, 28, 44, 255))
    draw = ImageDraw.Draw(card)

    # Foil-like layered border.
    draw.rounded_rectangle((18, 18, 882, 1182), radius=52, fill=(46, 159, 184, 255))
    draw.rounded_rectangle((31, 31, 869, 1169), radius=44, fill=(11, 25, 43, 255))
    draw.rounded_rectangle((48, 48, 852, 1152), radius=36, fill=(19, 48, 68, 255))

    # Decorative diagonal bands.
    for offset in range(-500, 1200, 105):
        draw.line((offset, 1120, offset + 700, 80), fill=(55, 158, 183, 65), width=26)

    # Dimmed Pal artwork collage.
    positions = [(55, 300), (315, 260), (560, 320)]
    sizes = [(330, 420), (340, 470), (300, 400)]
    for raw, position, size in zip(preview_images[:3], positions, sizes):
        try:
            image = Image.open(BytesIO(raw)).convert("RGBA")
            image.thumbnail(size, Image.Resampling.LANCZOS)
            alpha = image.getchannel("A").point(lambda value: int(value * 0.46))
            image.putalpha(alpha)
            card.alpha_composite(image, position)
        except Exception:
            continue

    draw = ImageDraw.Draw(card)
    title_font = _load_card_font(78, bold=True)
    subtitle_font = _load_card_font(35, bold=True)
    body_font = _load_card_font(27)
    small_font = _load_card_font(22)

    title = "PAL DECK"
    title_box = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((width - (title_box[2] - title_box[0])) / 2, 105), title, font=title_font, fill="white")

    subtitle = "FRIEND PACK"
    subtitle_box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    draw.text(((width - (subtitle_box[2] - subtitle_box[0])) / 2, 205), subtitle, font=subtitle_font, fill=(157, 231, 239))

    draw.rounded_rectangle((120, 770, 780, 1015), radius=30, fill=(8, 21, 34, 220), outline=(113, 220, 231), width=4)
    lines = [
        "5 RANDOM PAL CARDS",
        "Final card guaranteed 2 stars or higher",
        f"Prepared for {owner_name}",
    ]
    y = 815
    for index, line in enumerate(lines):
        font = subtitle_font if index == 0 else body_font
        box = draw.textbbox((0, 0), line, font=font)
        draw.text(((width - (box[2] - box[0])) / 2, y), line, font=font, fill="white")
        y += 68 if index == 0 else 55

    draw.text((75, 1090), "Press Open Pack to reveal your cards", font=small_font, fill=(190, 225, 232))

    output = BytesIO()
    card.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


class PalDeckView(discord.ui.View):
    """Open and browse a fixed five-card preview pack."""

    def __init__(
        self,
        *,
        owner_id: int,
        cards: list[dict[str, Any]],
        cover_png: bytes,
    ) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.cards = cards
        self.cover_png = cover_png
        self.current_index = 0
        self.opened = False
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the user who opened this Pal deck can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    def _sync_buttons(self) -> None:
        self.open_button.disabled = self.opened
        self.open_button.label = "Pack Opened" if self.opened else "Open Pack"
        self.back_button.disabled = not self.opened or self.current_index == 0
        self.next_button.disabled = not self.opened or self.current_index >= len(self.cards) - 1

    def _cover_message(self) -> tuple[discord.Embed, discord.File]:
        filename = f"pal_deck_cover_{self.owner_id}.png"
        file = discord.File(BytesIO(self.cover_png), filename=filename)
        embed = discord.Embed(
            title="Sealed Pal Deck",
            description=(
                "Contains **5 cards**.\n"
                "The fifth card is guaranteed to be **2 stars or higher**.\n\n"
                "This preview does not save cards or consume daily rolls."
            ),
            color=discord.Color.teal(),
        )
        embed.set_image(url=f"attachment://{filename}")
        return embed, file

    def _card_message(self) -> tuple[discord.Embed, discord.File]:
        card = self.cards[self.current_index]
        filename = f"deck_card_{self.owner_id}_{self.current_index + 1}.png"
        file = discord.File(BytesIO(card["png"]), filename=filename)
        guaranteed = self.current_index == len(self.cards) - 1

        embed = discord.Embed(
            title=(
                f"Card {self.current_index + 1} of {len(self.cards)} — "
                f"{card['tier']['emoji']} {get_pal_display_name(card['pal_name'], card['tier_name'])}"
            ),
            description=(
                f"**Paldeck:** `#{card['pal_number']}`\n"
                f"**Tier:** {card['tier_name']} {card['tier']['stars']}\n"
                f"**Passives:** {len(card['traits'])}/4"
                + ("\n**Guaranteed slot:** 2 stars or higher" if guaranteed else "")
                + "\n\nUse **Back** and **Next** to browse this pack."
            ),
            color=card["tier"]["color"],
        )
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text="Preview only • Nothing from this pack is stored")
        return embed, file

    @discord.ui.button(label="Open Pack", style=discord.ButtonStyle.success, row=0)
    async def open_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.opened = True
        self.current_index = 0
        self._sync_buttons()
        embed, file = self._card_message()
        await interaction.response.edit_message(
            embed=embed,
            attachments=[file],
            view=self,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=0)
    async def back_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.current_index > 0:
            self.current_index -= 1
        self._sync_buttons()
        embed, file = self._card_message()
        await interaction.response.edit_message(
            embed=embed,
            attachments=[file],
            view=self,
        )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=0)
    async def next_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.current_index < len(self.cards) - 1:
            self.current_index += 1
        self._sync_buttons()
        embed, file = self._card_message()
        await interaction.response.edit_message(
            embed=embed,
            attachments=[file],
            view=self,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True


async def create_pal_deck_cards(owner_name: str) -> tuple[list[dict[str, Any]], bytes]:
    """Generate five fixed preview cards and a pack cover."""
    pals, passive_skills = await asyncio.gather(
        fetch_pals(),
        fetch_palworld_1_0_passive_skills(),
    )

    chosen_pals = random.choices(pals, k=5)
    card_specs: list[dict[str, Any]] = []

    for index, pal in enumerate(chosen_pals):
        if index == 4:
            tier_name, tier = roll_guaranteed_two_star_tier()
        else:
            tier_name, tier = roll_pal_tier()

        card_specs.append(
            {
                "pal_number": str(pal["key"]),
                "pal_name": str(pal["name"]),
                "image_url": normalize_image_url(str(pal["image"])),
                "tier_name": tier_name,
                "tier": tier,
                "traits": roll_real_passive_traits(passive_skills, tier_name),
            }
        )

    pal_pngs = await asyncio.gather(
        *(download_image_bytes(card["image_url"]) for card in card_specs)
    )

    rendered_cards = await asyncio.gather(
        *(
            asyncio.to_thread(
                build_pal_card_png,
                pal_png,
                pal_number=spec["pal_number"],
                pal_name=spec["pal_name"],
                tier_name=spec["tier_name"],
                tier=spec["tier"],
                traits=spec["traits"],
            )
            for spec, pal_png in zip(card_specs, pal_pngs)
        )
    )

    for spec, rendered in zip(card_specs, rendered_cards):
        spec["png"] = rendered.getvalue()

    cover_buffer = await asyncio.to_thread(
        build_pal_pack_cover_png,
        list(pal_pngs[:3]),
        owner_name=owner_name,
    )
    return card_specs, cover_buffer.getvalue()


@bot.tree.command(
    name="open_pal_deck",
    description="Open a five-card Pal deck preview",
    guild=guild,
)
async def open_pal_deck(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    try:
        cards, cover_png = await create_pal_deck_cards(interaction.user.display_name)
        view = PalDeckView(
            owner_id=interaction.user.id,
            cards=cards,
            cover_png=cover_png,
        )

        embed, file = view._cover_message()
        await interaction.followup.send(
            embed=embed,
            file=file,
            view=view,
        )

    except aiohttp.ClientResponseError as error:
        log.exception("Pal deck HTTP error: %s", error)
        await interaction.followup.send(
            f"Could not load Pal deck data. HTTP status: {error.status}",
            ephemeral=True,
        )
    except Exception as error:
        log.exception("Failed to open Pal deck: %s", error)
        await interaction.followup.send(
            "An error occurred while creating the Pal deck.",
            ephemeral=True,
        )

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