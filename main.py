from datetime import datetime, timezone, timedelta
import random
import logging, sys
import discord
from discord import app_commands
from discord.ext import commands
import psycopg2
import os
from cats import Rarity, cat
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

class GachaButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.likes = 0

    @discord.ui.button(label="Draw", style=discord.ButtonStyle.success)
    async def draw(self, interaction: discord.Interaction, button: discord.ui.Button):
        cat = rand1to(1000) + 1
        rarity: Rarity = Rarity.RARE
        if cat == 1:
            rarity = Rarity.BANA_RARE
        elif cat <= 50:
            rarity = Rarity.UBER_RARE
        elif cat <= 300:
            rarity = Rarity.SUPER_RARE

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
        banner = rand1to(14)
        bannerStr = ""
        match banner:
            case 0:
                return
            case 1:
                return
            case 2:
                return
            case 3:
                return
            case 4:
                return
            case 5:
                return
            case 6:
                return
            case 7:
                return
            case 8:
                return
            case 9:
                return
            case 10:
                return
            case 11:
                return
            case 12:
                return
            case 13:
                return
        cat = cat(name="", banner=bannerStr, rarity=rarity, quality=quality, image_url="")

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

@bot.tree.command(name="inventory", description="View your current inventory", guild=guild)
async def inventory(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Inventory",
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