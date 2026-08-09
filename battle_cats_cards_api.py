"""Battle Cats collectible-card draw system for discord.py.

Drop this file next to ``main.py`` and register the commands with::

    from battle_cats_cards import register_battle_cats_commands
    register_battle_cats_commands(bot, guild)

The module intentionally mirrors the structure of the existing /pal_card and
/open_pal_deck flow:

* weighted rarity + quality rolls
* Pillow-rendered collectible card PNGs
* /battle_cat_card for one random preview card
* /open_battle_cat_deck for an interactive seven-card pack
* last card in a pack is guaranteed Super Rare or better

Cat names are fetched from Battle Cats Wiki rarity categories through the public
MediaWiki API. Artwork URLs are resolved only for selected cats, and only those
images are downloaded. Missing/broken artwork gets a generated placeholder.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Optional

import aiohttp
import discord
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("bot.battle_cats")


# ---------------------------------------------------------------------------
# Draw configuration
# ---------------------------------------------------------------------------
# These match the rarity percentages in the existing /gacha command:
# Bana Rare 0.1%, Uber Rare 4.9%, Super Rare 25%, Rare 70%.
BATTLE_CAT_RARITIES: dict[str, dict[str, Any]] = {
    "Rare": {
        "weight": 700,
        "emoji": "🔵",
        "stars": "★★",
        "color": discord.Color.blue(),
    },
    "Super Rare": {
        "weight": 250,
        "emoji": "🟣",
        "stars": "★★★",
        "color": discord.Color.purple(),
    },
    "Uber Rare": {
        "weight": 49,
        "emoji": "🟡",
        "stars": "★★★★★",
        "color": discord.Color.gold(),
    },
    "Bana Rare": {
        "weight": 1,
        "emoji": "🌈",
        "stars": "★★★★★★",
        "color": discord.Color.from_rgb(255, 105, 180),
    },
}

# Existing quality rates from main.py:
# C 50.0%, B 35.0%, A 14.0%, S 0.9%, SS 0.1%.
# The original code's comparisons produce exactly these probabilities.
BATTLE_CAT_QUALITIES: dict[str, dict[str, Any]] = {
    "C": {"weight": 500, "multiplier": 1.00, "label": "Standard"},
    "B": {"weight": 350, "multiplier": 1.05, "label": "Polished"},
    "A": {"weight": 140, "multiplier": 1.10, "label": "Elite"},
    "S": {"weight": 9, "multiplier": 1.20, "label": "Prismatic"},
    "SS": {"weight": 1, "multiplier": 1.35, "label": "Perfect"},
}

# ---------------------------------------------------------------------------
# Public Battle Cats Wiki data source
# ---------------------------------------------------------------------------
# The roster is loaded from the Battle Cats Wiki (Fandom) MediaWiki API.
# We fetch only page titles for the four supported rarities and cache them.
# Artwork URLs are resolved only after a cat is selected, so opening a pack
# downloads artwork for the selected cards only -- never the entire roster.
BATTLE_CATS_WIKI_API = "https://battle-cats.fandom.com/api.php"

WIKI_CATEGORY_BY_RARITY: dict[str, str] = {
    "Rare": "Category:Rare Cats",
    "Super Rare": "Category:Super Rare Cats",
    "Uber Rare": "Category:Uber Rare Cats",
    # Keep the bot's custom jackpot label while sourcing real Legend Rares.
    "Bana Rare": "Category:Legend Rare Cats",
}

_WIKI_ROSTER_CACHE: dict[str, list[dict[str, str]]] | None = None
_WIKI_IMAGE_URL_CACHE: dict[str, str] = {}
_WIKI_CACHE_LOCK: asyncio.Lock | None = None


def _wiki_cache_lock() -> asyncio.Lock:
    global _WIKI_CACHE_LOCK
    if _WIKI_CACHE_LOCK is None:
        _WIKI_CACHE_LOCK = asyncio.Lock()
    return _WIKI_CACHE_LOCK


def _display_name_from_wiki_title(title: str) -> str:
    """Remove the wiki rarity suffix while preserving the actual unit name."""
    return re.sub(
        r"\s*\((?:Rare|Super Rare|Uber Rare|Legend Rare) Cat\)\s*$",
        "",
        str(title),
        flags=re.IGNORECASE,
    ).strip()


async def _wiki_api_get(params: dict[str, Any]) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {
        "User-Agent": "BattleCatsDiscordCard/2.0 (MediaWiki API client)",
        "Accept": "application/json",
    }
    request_params = {"format": "json", "formatversion": "2", **params}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(BATTLE_CATS_WIKI_API, params=request_params) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)

    if "error" in payload:
        raise RuntimeError(f"Battle Cats Wiki API error: {payload['error']}")
    return payload


async def fetch_wiki_category_roster(rarity: str) -> list[dict[str, str]]:
    """Fetch every Cat article title in one rarity category.

    This requests metadata only. No artwork bytes are downloaded here.
    """
    category = WIKI_CATEGORY_BY_RARITY[rarity]
    cats: list[dict[str, str]] = []
    cmcontinue: str | None = None

    while True:
        params: dict[str, Any] = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": category,
            "cmnamespace": "0",
            "cmlimit": "max",
            "cmtype": "page",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        payload = await _wiki_api_get(params)
        members = payload.get("query", {}).get("categorymembers", [])

        for member in members:
            title = str(member.get("title", "")).strip()
            if not title:
                continue
            cats.append(
                {
                    "name": _display_name_from_wiki_title(title),
                    "wiki_title": title,
                    "rarity": rarity,
                    "banner": "Battle Cats Wiki",
                }
            )

        cmcontinue = payload.get("continue", {}).get("cmcontinue")
        if not cmcontinue:
            break

    # Category pages should not duplicate titles, but make the cache defensive.
    unique: dict[str, dict[str, str]] = {}
    for cat in cats:
        unique.setdefault(cat["wiki_title"], cat)

    result = list(unique.values())
    if not result:
        raise RuntimeError(f"No cats returned from {category}")
    return result


async def fetch_battle_cat_roster(*, force_refresh: bool = False) -> dict[str, list[dict[str, str]]]:
    """Fetch/cache the public wiki roster grouped by the bot's rarity names."""
    global _WIKI_ROSTER_CACHE

    if _WIKI_ROSTER_CACHE is not None and not force_refresh:
        return _WIKI_ROSTER_CACHE

    async with _wiki_cache_lock():
        if _WIKI_ROSTER_CACHE is not None and not force_refresh:
            return _WIKI_ROSTER_CACHE

        rarity_names = list(WIKI_CATEGORY_BY_RARITY)
        results = await asyncio.gather(
            *(fetch_wiki_category_roster(rarity) for rarity in rarity_names)
        )
        _WIKI_ROSTER_CACHE = dict(zip(rarity_names, results))
        log.info(
            "Loaded Battle Cats Wiki roster: %s",
            {rarity: len(cats) for rarity, cats in _WIKI_ROSTER_CACHE.items()},
        )
        return _WIKI_ROSTER_CACHE


async def fetch_wiki_cat_image_url(wiki_title: str) -> str:
    """Resolve one selected Cat article to its representative image URL.

    MediaWiki's pageimages property returns URL metadata; it does not download
    the image itself. The returned URL is cached for later pulls.
    """
    if wiki_title in _WIKI_IMAGE_URL_CACHE:
        return _WIKI_IMAGE_URL_CACHE[wiki_title]

    payload = await _wiki_api_get(
        {
            "action": "query",
            "prop": "pageimages",
            "titles": wiki_title,
            "piprop": "original|thumbnail|name",
            "pithumbsize": "900",
        }
    )
    pages = payload.get("query", {}).get("pages", [])
    image_url = ""

    if pages:
        page = pages[0]
        original = page.get("original") or {}
        thumbnail = page.get("thumbnail") or {}
        image_url = str(original.get("source") or thumbnail.get("source") or "")

    _WIKI_IMAGE_URL_CACHE[wiki_title] = image_url
    return image_url


BATTLE_CAT_PACK_SIZE = 7


@dataclass(frozen=True)
class BattleCatPull:
    name: str
    rarity: str
    quality: str
    banner: str
    image_url: str
    wiki_title: str = ""


# ---------------------------------------------------------------------------
# Rolling logic
# ---------------------------------------------------------------------------
def _weighted_choice(config: dict[str, dict[str, Any]]) -> str:
    names = list(config)
    weights = [int(config[name]["weight"]) for name in names]
    return random.choices(names, weights=weights, k=1)[0]


def roll_battle_cat_rarity(*, minimum: Optional[str] = None) -> str:
    """Roll rarity, optionally limiting the pool to ``minimum`` or better."""
    if minimum is None:
        return _weighted_choice(BATTLE_CAT_RARITIES)

    order = ["Rare", "Super Rare", "Uber Rare", "Bana Rare"]
    if minimum not in order:
        raise ValueError(f"Unknown minimum rarity: {minimum}")

    allowed = order[order.index(minimum):]
    return random.choices(
        allowed,
        weights=[BATTLE_CAT_RARITIES[name]["weight"] for name in allowed],
        k=1,
    )[0]


def roll_battle_cat_quality() -> str:
    return _weighted_choice(BATTLE_CAT_QUALITIES)


async def draw_battle_cat(*, minimum_rarity: Optional[str] = None) -> BattleCatPull:
    """Roll rarity/quality and select a unit from the cached public wiki roster."""
    rarity = roll_battle_cat_rarity(minimum=minimum_rarity)
    roster = await fetch_battle_cat_roster()
    pool = roster.get(rarity, [])
    if not pool:
        raise RuntimeError(f"No Battle Cats Wiki entries loaded for {rarity}")

    cat = random.choice(pool)
    image_url = await fetch_wiki_cat_image_url(cat["wiki_title"])
    return BattleCatPull(
        name=cat["name"],
        rarity=rarity,
        quality=roll_battle_cat_quality(),
        banner=cat.get("banner", "Battle Cats Wiki"),
        image_url=image_url,
        wiki_title=cat["wiki_title"],
    )


# ---------------------------------------------------------------------------
# Artwork + card renderer
# ---------------------------------------------------------------------------
def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = str(text).split()
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


def make_missing_cat_art(cat_name: str) -> bytes:
    """Generate simple cat artwork when no external image is configured."""
    image = Image.new("RGBA", (900, 700), (25, 28, 34, 255))
    draw = ImageDraw.Draw(image)

    # Cat head/body silhouette.
    draw.ellipse((260, 130, 640, 510), fill=(235, 235, 235, 255))
    draw.polygon([(300, 185), (340, 70), (410, 170)], fill=(235, 235, 235, 255))
    draw.polygon([(490, 170), (560, 70), (600, 185)], fill=(235, 235, 235, 255))
    draw.ellipse((350, 285, 385, 320), fill=(30, 30, 30, 255))
    draw.ellipse((515, 285, 550, 320), fill=(30, 30, 30, 255))
    draw.arc((420, 315, 480, 375), start=10, end=170, fill=(30, 30, 30, 255), width=5)

    font = _load_font(52, bold=True)
    lines = _fit_text(draw, cat_name, font, 760)[:2]
    y = 555
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        draw.text(((900 - (box[2] - box[0])) / 2, y), line, font=font, fill="white")
        y += 60

    output = BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


async def download_cat_image_bytes(cat_name: str, image_url: str) -> bytes:
    if not image_url:
        return make_missing_cat_art(cat_name)

    timeout = aiohttp.ClientTimeout(total=15)
    headers = {
        "User-Agent": "Mozilla/5.0 BattleCatsDiscordCard/1.0",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(image_url) as response:
                if response.status == 200:
                    data = await response.read()
                    if data:
                        return data
    except (aiohttp.ClientError, asyncio.TimeoutError):
        log.warning("Battle Cat artwork failed for %s", cat_name, exc_info=True)

    return make_missing_cat_art(cat_name)


def build_battle_cat_card_png(
    art_png: bytes,
    *,
    pull: BattleCatPull,
) -> BytesIO:
    """Render one Battle Cats pull as a 900x1200 collectible card."""
    width, height = 900, 1200

    palette = {
        "Rare": ((49, 102, 190), (17, 38, 75)),
        "Super Rare": ((126, 71, 181), (46, 24, 72)),
        "Uber Rare": ((218, 166, 42), (85, 60, 10)),
        "Bana Rare": ((226, 80, 158), (78, 24, 66)),
    }
    accent, dark = palette.get(pull.rarity, palette["Rare"])
    rarity_cfg = BATTLE_CAT_RARITIES[pull.rarity]
    quality_cfg = BATTLE_CAT_QUALITIES[pull.quality]

    card = Image.new("RGBA", (width, height), dark + (255,))
    draw = ImageDraw.Draw(card)

    draw.rounded_rectangle((18, 18, 882, 1182), radius=42, fill=accent + (255,))
    draw.rounded_rectangle((34, 34, 866, 1166), radius=34, fill=dark + (255,))
    draw.rounded_rectangle((55, 55, 845, 180), radius=25, fill=accent + (235,))
    draw.rounded_rectangle((55, 200, 845, 735), radius=30, fill=(15, 18, 24, 235))
    draw.rounded_rectangle((55, 755, 845, 1135), radius=30, fill=(12, 15, 20, 235))

    title_font = _load_font(46, bold=True)
    subtitle_font = _load_font(27, bold=True)
    section_font = _load_font(29, bold=True)
    body_font = _load_font(24)
    stat_font = _load_font(25, bold=True)

    title_lines = _fit_text(draw, pull.name, title_font, 700)[:2]
    ty = 68
    for line in title_lines:
        draw.text((80, ty), line, font=title_font, fill="white")
        ty += 48

    subtitle = f"{pull.rarity.upper()}  •  {rarity_cfg['stars']}"
    draw.text((82, 145), subtitle, font=subtitle_font, fill=(245, 245, 245))

    artwork = Image.open(BytesIO(art_png)).convert("RGBA")
    max_w, max_h = 700, 480
    scale = min(max_w / max(1, artwork.width), max_h / max(1, artwork.height))
    artwork = artwork.resize(
        (max(1, int(artwork.width * scale)), max(1, int(artwork.height * scale))),
        Image.Resampling.LANCZOS,
    )
    backdrop = Image.new("RGBA", (740, 490), (255, 255, 255, 18))
    backdrop.alpha_composite(
        artwork,
        ((740 - artwork.width) // 2, (490 - artwork.height) // 2),
    )
    card.alpha_composite(backdrop, (80, 220))

    draw = ImageDraw.Draw(card)
    draw.text((82, 785), "DRAW RESULT", font=section_font, fill=accent)

    quality_stars = {"C": "★", "B": "★★", "A": "★★★", "S": "★★★★", "SS": "★★★★★"}
    rows = [
        ("Rarity", f"{rarity_cfg['emoji']} {pull.rarity}"),
        ("Quality", f"{pull.quality}  {quality_stars[pull.quality]}  ({quality_cfg['label']})"),
        ("Banner", pull.banner),
        ("Card Multiplier", f"x{quality_cfg['multiplier']:.2f}"),
    ]

    y = 845
    for label, value in rows:
        draw.text((85, y), f"{label}:", font=stat_font, fill="white")
        value_lines = _fit_text(draw, value, body_font, 480)[:2]
        vy = y + 2
        for line in value_lines:
            draw.text((325, vy), line, font=body_font, fill=(215, 220, 228))
            vy += 28
        y = max(y + 52, vy + 10)

    output = BytesIO()
    card.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


async def create_rendered_battle_cat_card(
    *,
    minimum_rarity: Optional[str] = None,
) -> dict[str, Any]:
    pull = await draw_battle_cat(minimum_rarity=minimum_rarity)
    art = await download_cat_image_bytes(pull.name, pull.image_url)
    rendered = await asyncio.to_thread(build_battle_cat_card_png, art, pull=pull)
    return {"pull": pull, "png": rendered.getvalue()}


# ---------------------------------------------------------------------------
# Interactive pack UI
# ---------------------------------------------------------------------------
class BattleCatDeckView(discord.ui.View):
    def __init__(self, *, owner_id: int, cards: list[dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.cards = cards
        self.current_index = 0
        self.opened = False

        for index, card in enumerate(cards, start=1):
            card["filename"] = f"battle_cat_card_{owner_id}_{index}.png"

        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the user who opened this Battle Cats pack can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    def _sync_buttons(self) -> None:
        self.open_button.disabled = self.opened
        self.open_button.label = "Pack Opened" if self.opened else "Open Pack"
        self.back_button.disabled = not self.opened or self.current_index == 0
        self.next_button.disabled = (
            not self.opened or self.current_index >= len(self.cards) - 1
        )

    def cover_embed(self) -> discord.Embed:
        return discord.Embed(
            title="Battle Cats Rare Capsule Card Pack",
            description=(
                f"Contains **{len(self.cards)} Battle Cat cards**.\n"
                f"Card {len(self.cards)} is guaranteed to be **Super Rare or better**.\n\n"
                "Press **Open Pack** to reveal card 1."
            ),
            color=discord.Color.orange(),
        )

    def current_file(self) -> discord.File:
        card = self.cards[self.current_index]
        return discord.File(BytesIO(card["png"]), filename=card["filename"])

    def current_embed(self) -> discord.Embed:
        card = self.cards[self.current_index]
        pull: BattleCatPull = card["pull"]
        rarity_cfg = BATTLE_CAT_RARITIES[pull.rarity]
        guaranteed = self.current_index == len(self.cards) - 1

        description = (
            f"**Rarity:** {rarity_cfg['emoji']} {pull.rarity} {rarity_cfg['stars']}\n"
            f"**Quality:** `{pull.quality}`\n"
            f"**Banner:** {pull.banner}"
        )
        if guaranteed:
            description += "\n**Guaranteed slot:** Super Rare or better"
        description += "\n\nUse **Back** and **Next** to browse this pack."

        embed = discord.Embed(
            title=f"Card {self.current_index + 1} of {len(self.cards)} — {pull.name}",
            description=description,
            color=rarity_cfg["color"],
        )
        embed.set_image(url=f"attachment://{card['filename']}")
        return embed

    @discord.ui.button(label="Open Pack", style=discord.ButtonStyle.success, row=0)
    async def open_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.opened = True
        self.current_index = 0
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.current_embed(),
            attachments=[self.current_file()],
            view=self,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=0)
    async def back_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.current_index = max(0, self.current_index - 1)
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.current_embed(),
            attachments=[self.current_file()],
            view=self,
        )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=0)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.current_index = min(len(self.cards) - 1, self.current_index + 1)
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.current_embed(),
            attachments=[self.current_file()],
            view=self,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True


async def create_battle_cat_deck_cards() -> list[dict[str, Any]]:
    """Create a fixed seven-card pack; final slot is Super Rare+ guaranteed."""
    tasks = [
        create_rendered_battle_cat_card(
            minimum_rarity="Super Rare" if index == BATTLE_CAT_PACK_SIZE - 1 else None
        )
        for index in range(BATTLE_CAT_PACK_SIZE)
    ]
    return list(await asyncio.gather(*tasks))


# ---------------------------------------------------------------------------
# Slash-command registration
# ---------------------------------------------------------------------------
def register_battle_cats_commands(
    bot: discord.ext.commands.Bot,
    guild: discord.Object,
) -> None:
    """Register Battle Cats card commands on an existing discord.py bot."""

    @bot.tree.command(
        name="battle_cat_card",
        description="Generate a random Battle Cats collectible card preview",
        guild=guild,
    )
    async def battle_cat_card(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            card = await create_rendered_battle_cat_card()
            pull: BattleCatPull = card["pull"]
            rarity_cfg = BATTLE_CAT_RARITIES[pull.rarity]
            filename = f"battle_cat_{interaction.user.id}.png"
            file = discord.File(BytesIO(card["png"]), filename=filename)

            embed = discord.Embed(
                title=f"{rarity_cfg['emoji']} {pull.name}",
                description=(
                    f"**Rarity:** {pull.rarity} {rarity_cfg['stars']}\n"
                    f"**Quality:** `{pull.quality}`\n"
                    f"**Banner:** {pull.banner}\n\n"
                    "This is a preview and was **not** stored."
                ),
                color=rarity_cfg["color"],
            )
            embed.set_image(url=f"attachment://{filename}")
            await interaction.followup.send(embed=embed, file=file)
        except Exception as error:
            log.exception("Failed to generate Battle Cat card: %s", error)
            await interaction.followup.send(
                "An error occurred while generating the Battle Cat card.",
                ephemeral=True,
            )

    @bot.tree.command(
        name="open_battle_cat_deck",
        description="Open a seven-card Battle Cats capsule pack",
        guild=guild,
    )
    async def open_battle_cat_deck(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            cards = await create_battle_cat_deck_cards()
            view = BattleCatDeckView(owner_id=interaction.user.id, cards=cards)
            await interaction.followup.send(embed=view.cover_embed(), view=view)
        except Exception as error:
            log.exception("Failed to create Battle Cats deck: %s", error)
            await interaction.followup.send(
                "An error occurred while creating the Battle Cats card pack.",
                ephemeral=True,
            )


__all__ = [
    "BATTLE_CAT_RARITIES",
    "BATTLE_CAT_QUALITIES",
    "BATTLE_CATS_WIKI_API",
    "WIKI_CATEGORY_BY_RARITY",
    "BattleCatPull",
    "BattleCatDeckView",
    "build_battle_cat_card_png",
    "create_battle_cat_deck_cards",
    "create_rendered_battle_cat_card",
    "fetch_battle_cat_roster",
    "fetch_wiki_cat_image_url",
    "draw_battle_cat",
    "register_battle_cats_commands",
    "roll_battle_cat_quality",
    "roll_battle_cat_rarity",
]
