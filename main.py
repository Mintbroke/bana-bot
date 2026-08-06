from discord.ext import tasks
from datetime import datetime, timezone, timedelta
import random
import logging, sys
import aiohttp
from typing import Any, Optional
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
                        obtained_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_test_owned_pals_user
                    ON test_owned_pals (
                        guild_id,
                        user_id,
                        obtained_at DESC,
                        id DESC
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
                    obtained_at
                FROM test_owned_pals
                WHERE guild_id = %s
                  AND user_id = %s
                ORDER BY obtained_at DESC, id DESC;
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
                    "obtained_at": row[13],
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


def create_inventory_summary_embed(
    *,
    pals: list[dict[str, Any]],
    current_index: int,
    owner: discord.Member | discord.User,
) -> discord.Embed:
    pal = pals[current_index]
    tier_name = pal["tier_name"]
    tier = get_tier_config(tier_name)
    display_name = get_pal_display_name(
        pal["pal_name"],
        tier_name,
    )

    embed = discord.Embed(
        title=f"{tier['emoji']} {display_name}",
        description=(
            f"**Paldeck:** `#{pal['pal_number']}`\n"
            f"**Rarity:** {tier_name}\n"
            f"**Owned Pal ID:** `{pal['id']}`\n\n"
            "Press **View Info** to inspect this Pal."
        ),
        color=tier["color"],
    )

    embed.set_thumbnail(url=pal["image_url"])

    embed.set_author(
        name=f"{owner.display_name}'s Pal Collection",
        icon_url=owner.display_avatar.url,
    )

    embed.set_footer(
        text=f"Pal {current_index + 1} of {len(pals)}"
    )

    return embed


def create_inventory_detail_embed(
    *,
    pal: dict[str, Any],
    current_index: int,
    total_pals: int,
    owner: discord.Member | discord.User,
) -> discord.Embed:
    tier_name = pal["tier_name"]
    tier = get_tier_config(tier_name)
    display_name = get_pal_display_name(
        pal["pal_name"],
        tier_name,
    )

    embed = discord.Embed(
        title=(
            f"{tier['emoji']} {display_name} "
            f"{tier['emoji']}"
        ),
        description=(
            f"**Paldeck Number:** `#{pal['pal_number']}`\n"
            f"**Owned Pal ID:** `{pal['id']}`"
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

    embed.add_field(
        name="Size",
        value=pal["pal_size"],
        inline=True,
    )

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

    obtained_at = pal.get("obtained_at")

    if obtained_at is not None:
        embed.add_field(
            name="Obtained",
            value=discord.utils.format_dt(
                obtained_at,
                style="F",
            ),
            inline=False,
        )

    embed.set_footer(
        text=f"Pal {current_index + 1} of {total_pals}"
    )

    return embed


class PalInventoryView(discord.ui.View):
    def __init__(
        self,
        *,
        owner_id: int,
        owner: discord.Member | discord.User,
        pals: list[dict[str, Any]],
    ):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.owner = owner
        self.pals = pals
        self.current_index = 0
        self.showing_details = False

        self.update_buttons()

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This is not your Pal collection.",
                ephemeral=True,
            )
            return False

        return True

    def update_buttons(self) -> None:
        self.previous_button.disabled = self.current_index == 0
        self.next_button.disabled = (
            self.current_index >= len(self.pals) - 1
        )

        if self.showing_details:
            self.info_button.label = "Back"
            self.info_button.emoji = "↩️"
        else:
            self.info_button.label = "View Info"
            self.info_button.emoji = "🔍"

    def create_current_embed(self) -> discord.Embed:
        if self.showing_details:
            return create_inventory_detail_embed(
                pal=self.pals[self.current_index],
                current_index=self.current_index,
                total_pals=len(self.pals),
                owner=self.owner,
            )

        return create_inventory_summary_embed(
            pals=self.pals,
            current_index=self.current_index,
            owner=self.owner,
        )

    @discord.ui.button(
        label="Previous",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
    )
    async def previous_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.current_index > 0:
            self.current_index -= 1

        self.showing_details = False
        self.update_buttons()

        await interaction.response.edit_message(
            embed=self.create_current_embed(),
            view=self,
        )

    @discord.ui.button(
        label="View Info",
        emoji="🔍",
        style=discord.ButtonStyle.primary,
    )
    async def info_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.showing_details = not self.showing_details
        self.update_buttons()

        await interaction.response.edit_message(
            embed=self.create_current_embed(),
            view=self,
        )

    @discord.ui.button(
        label="Next",
        emoji="➡️",
        style=discord.ButtonStyle.secondary,
    )
    async def next_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.current_index < len(self.pals) - 1:
            self.current_index += 1

        self.showing_details = False
        self.update_buttons()

        await interaction.response.edit_message(
            embed=self.create_current_embed(),
            view=self,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True




@bot.tree.command(
    name="random_pal",
    description="Draw and store a random Pal",
    guild=guild,
)
async def random_pal(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    try:
        pals = await fetch_pals()
        pal = random.choice(pals)

        pal_number = str(pal["key"])
        pal_name = str(pal["name"])
        image_url = normalize_image_url(str(pal["image"]))

        tier_name, tier = roll_pal_tier()
        traits = generate_test_traits(tier_name)

        if interaction.guild_id is None:
            await interaction.followup.send(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        owned_pal_id = save_test_owned_pal(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
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

        embed = create_draw_embed(
            pal_number=pal_number,
            pal_name=pal_name,
            image_url=image_url,
            tier_name=tier_name,
            tier=tier,
            traits=traits,
            owned_pal_id=owned_pal_id,
        )

        announcement: Optional[str] = None

        if tier_name == "Alpha":
            announcement = "🔴 **ALPHA PAL ENCOUNTER!**"
        elif tier_name == "Lucky":
            announcement = "✨ **A LUCKY PAL HAS APPEARED!** ✨"
        elif tier_name == "Mythical":
            announcement = "🌌 **MYTHICAL PULL! INCREDIBLE LUCK!** 🌌"

        await interaction.followup.send(
            content=announcement,
            embed=embed,
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
            "An error occurred while drawing a Pal.",
            ephemeral=True,
        )

    except Exception as error:
        log.exception("Unexpected random Pal error: %s", error)
        await interaction.followup.send(
            "An unexpected error occurred while saving the Pal.",
            ephemeral=True,
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