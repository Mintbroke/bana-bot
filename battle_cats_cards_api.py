"""Battle Cats collectible-card draw system for discord.py.

Drop this file next to ``main.py`` and register the commands with::

    from battle_cats_cards import register_battle_cats_commands
    register_battle_cats_commands(bot, guild)

The module intentionally mirrors the structure of the existing /pal_card and
/open_pal_deck flow:

* weighted rarity + quality rolls
* Pillow-rendered collectible card PNGs
* /battle_cat_card for one stored random card
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
from datetime import datetime, timezone, timedelta

import aiohttp
import discord
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont
from functions import get_db_connection

log = logging.getLogger("bot.battle_cats")


# ---------------------------------------------------------------------------
# Draw configuration
# ---------------------------------------------------------------------------
# These match the rarity percentages in the existing /gacha command:
# Bana Rare 0.1%, Uber Rare 4.9%, Super Rare 25%, Rare 70%.
BATTLE_CAT_RARITIES: dict[str, dict[str, Any]] = {
    "Rare": {
        "weight": 700,
        "emoji": "⚪",
        "stars": "★★",
        "color": discord.Color.light_grey(),
    },
    "Super Rare": {
        "weight": 250,
        "emoji": "🟢",
        "stars": "★★★",
        "color": discord.Color.green(),
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
        "color": discord.Color.red(),  # Discord embeds cannot use a rainbow gradient
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
_WIKI_CAT_TRAITS_CACHE: dict[str, tuple[str, ...]] = {}
_WIKI_TRAIT_ICON_URL_CACHE: dict[str, str] = {}
_WIKI_COLLAB_TITLES_CACHE: set[str] | None = None
_WIKI_CACHE_LOCK: asyncio.Lock | None = None

BATTLE_CAT_PACK_IMAGES: list[str] = [
    "https://cdn.discordapp.com/attachments/928447198746804265/1535807553689092126/image.png?ex=6a791bbe&is=6a77ca3e&hm=03ea1d27ff5833efbb15b3b89d255e2c0d54bb29bf3bd6af3d5023cdaf94dc91&",
    "https://cdn.discordapp.com/attachments/928447198746804265/1535807554083364934/image.png?ex=6a791bbe&is=6a77ca3e&hm=1ce30159d89a99db191b90463bcdb9cc4445f769cc47c6597d939702bb1cc13c&",
    "https://cdn.discordapp.com/attachments/928447198746804265/1535807554465300630/image.png?ex=6a791bbe&is=6a77ca3e&hm=c3bd10350c8de82e56c3b94a3c0681887e30ba1ca9772ab235e574fe75879c68&",
    "https://cdn.discordapp.com/attachments/928447198746804265/1535807555106897920/image.png?ex=6a791bbf&is=6a77ca3f&hm=bd1c6e81e8432955ed558e104c014e1e21a3abe4ce1d759f5b396bf40421fcfc&",
    "https://cdn.discordapp.com/attachments/928447198746804265/1535807555652026388/image.png?ex=6a791bbf&is=6a77ca3f&hm=cee9247cb939b7f3bb841faf4b013cb61028f22e4b5015a4751fd41bdb5d13b8&",
    "https://cdn.discordapp.com/attachments/928447198746804265/1535807556109213776/image.png?ex=6a791bbf&is=6a77ca3f&hm=d600b203bdfd063015837ac7f231023ec34bf08e1455dc4c2e30d329aa237b0b&",
]

# Battle Cats Wiki trait icon filenames. These are the same official icon names
# used by the wiki's trait modules/categories.
TRAIT_ICON_FILES: dict[str, str] = {
    "Traitless": "Traitlesstraiticon.png",
    "Red": "Redtraiticon.png",
    "Floating": "Floatingtraiticon.png",
    "Black": "Darktraiticon.png",
    "Metal": "Metaltraiticon.png",
    "Angel": "Angeltraiticon.png",
    "Alien": "Alientraiticon.png",
    "Zombie": "Zombietraiticon.png",
    "Relic": "Relictraiticon.png",
    "Aku": "Akutraiticon.png",
}

# Only enemy-target traits are shown on Cat cards. Miscellaneous categories
# such as Anti-Cat Cats (old PC PvP content) are intentionally excluded.
SUPPORTED_TARGET_TRAITS = tuple(TRAIT_ICON_FILES)

# After rarity is rolled, choose the source pool inside that rarity.
# 95% standard Battle Cats units, 5% Collaboration Event units.
COLLAB_PULL_CHANCE = 0.05
COLLAB_CATEGORY = "Category:Collaboration Event Cats"


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
        "User-Agent": "BattleCatsDiscordCard/3.0 (MediaWiki API client)",
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




async def fetch_collaboration_cat_titles(*, force_refresh: bool = False) -> set[str]:
    """Fetch/cache every wiki page listed as a Collaboration Event Cat.

    Only page titles are cached. This does not download any cat artwork.
    The resulting set is intersected with the already-selected rarity pool,
    so a collab pull can never bypass the rarity rolled first.
    """
    global _WIKI_COLLAB_TITLES_CACHE

    if _WIKI_COLLAB_TITLES_CACHE is not None and not force_refresh:
        return _WIKI_COLLAB_TITLES_CACHE

    titles: set[str] = set()
    cmcontinue: str | None = None

    while True:
        params: dict[str, Any] = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": COLLAB_CATEGORY,
            "cmnamespace": "0",
            "cmlimit": "max",
            "cmtype": "page",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        payload = await _wiki_api_get(params)
        for member in payload.get("query", {}).get("categorymembers", []):
            title = str(member.get("title", "")).strip()
            if title:
                titles.add(title)

        cmcontinue = payload.get("continue", {}).get("cmcontinue")
        if not cmcontinue:
            break

    if not titles:
        raise RuntimeError(f"No cats returned from {COLLAB_CATEGORY}")

    _WIKI_COLLAB_TITLES_CACHE = titles
    log.info("Loaded %s Battle Cats collaboration unit titles", len(titles))
    return titles


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


async def fetch_wiki_cat_target_traits(wiki_title: str) -> tuple[str, ...]:
    """Return the enemy traits this specific Cat targets.

    Battle Cats Wiki categorizes units as ``Anti-Red Cats``,
    ``Anti-Floating Cats``, etc. Querying the selected page's categories gives
    us unit-specific target data without hardcoding a Cat -> traits database.
    """
    if wiki_title in _WIKI_CAT_TRAITS_CACHE:
        return _WIKI_CAT_TRAITS_CACHE[wiki_title]

    payload = await _wiki_api_get(
        {
            "action": "query",
            "prop": "categories",
            "titles": wiki_title,
            "cllimit": "max",
            "clshow": "!hidden",
        }
    )
    pages = payload.get("query", {}).get("pages", [])
    category_titles: list[str] = []
    if pages:
        category_titles = [
            str(item.get("title", ""))
            for item in pages[0].get("categories", [])
        ]

    found: list[str] = []
    for category in category_titles:
        match = re.fullmatch(r"Category:Anti-(.+?) Cats", category, flags=re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1).strip()
        # Wiki calls Black enemies "Black" in anti-target categories while
        # some internal modules call the trait "Dark".
        normalized = {
            "traitless": "Traitless",
            "red": "Red",
            "floating": "Floating",
            "black": "Black",
            "metal": "Metal",
            "angel": "Angel",
            "alien": "Alien",
            "zombie": "Zombie",
            "relic": "Relic",
            "aku": "Aku",
        }.get(raw.casefold())
        if normalized and normalized not in found:
            found.append(normalized)

    traits = tuple(found)
    _WIKI_CAT_TRAITS_CACHE[wiki_title] = traits
    return traits


async def fetch_trait_icon_url(trait: str) -> str:
    """Resolve one official Battle Cats Wiki trait icon URL."""
    if trait in _WIKI_TRAIT_ICON_URL_CACHE:
        return _WIKI_TRAIT_ICON_URL_CACHE[trait]

    filename = TRAIT_ICON_FILES.get(trait)
    if not filename:
        return ""

    payload = await _wiki_api_get(
        {
            "action": "query",
            "prop": "imageinfo",
            "titles": f"File:{filename}",
            "iiprop": "url",
            "iiurlwidth": "96",
        }
    )
    pages = payload.get("query", {}).get("pages", [])
    url = ""
    if pages:
        info = pages[0].get("imageinfo") or []
        if info:
            url = str(info[0].get("thumburl") or info[0].get("url") or "")

    _WIKI_TRAIT_ICON_URL_CACHE[trait] = url
    return url


async def fetch_cat_trait_icons(wiki_title: str) -> tuple[tuple[str, str], ...]:
    """Return ``(trait_name, icon_url)`` pairs for one selected Cat."""
    traits = await fetch_wiki_cat_target_traits(wiki_title)
    if not traits:
        return ()
    urls = await asyncio.gather(*(fetch_trait_icon_url(trait) for trait in traits))
    return tuple((trait, url) for trait, url in zip(traits, urls) if url)


async def download_trait_icon_bytes(icon_pairs: tuple[tuple[str, str], ...]) -> dict[str, bytes]:
    """Download only the trait icons needed by the currently rendered Cat."""
    if not icon_pairs:
        return {}

    timeout = aiohttp.ClientTimeout(total=12)
    headers = {
        "User-Agent": "Mozilla/5.0 BattleCatsDiscordCard/3.0",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }

    async def one(trait: str, url: str) -> tuple[str, bytes | None]:
        if not url:
            return trait, None
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.read()
                        return trait, data or None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            log.warning("Trait icon download failed for %s", trait, exc_info=True)
        return trait, None

    results = await asyncio.gather(*(one(trait, url) for trait, url in icon_pairs))
    return {trait: data for trait, data in results if data}


BATTLE_CAT_PACK_SIZE = 7
BATTLE_CAT_CARD_DAILY_LIMIT = 5
BATTLE_CAT_DECK_DAILY_LIMIT = 1

NORMAL_CARD_PULL_IMAGE = "https://cdn.discordapp.com/attachments/928447198746804265/1535813548951732244/images.png?ex=6a792154&is=6a77cfd4&hm=d74d6d9e0ebc5b88d252aa782e5a1138a0cf3caf82ebcdcbf7f6ba7673008dee&"
PLATINUM_PULL_IMAGE = "https://cdn.discordapp.com/attachments/928447198746804265/1535813594862583858/latest.png?ex=6a79215f&is=6a77cfdf&hm=098bc8ab22ccb1840989923ab0cbbae03c5318cfe95362c52e619c47695f9327&"
LEGEND_PULL_IMAGE = "https://cdn.discordapp.com/attachments/928447198746804265/1535813612923260989/700.png?ex=6a792163&is=6a77cfe3&hm=51fda2aa161fe947cf7039370df72cea69caecb04d539649cb8cace497499be0&"


# ---------------------------------------------------------------------------
# PostgreSQL persistence + per-user UTC daily limits
# ---------------------------------------------------------------------------
def ensure_battle_cats_schema() -> None:
    """Create Battle Cats ownership and daily-limit tables if needed."""
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS test_owned_battle_cats (
                        id BIGSERIAL PRIMARY KEY,
                        guild_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        cat_name TEXT NOT NULL,
                        wiki_title TEXT NOT NULL,
                        rarity TEXT NOT NULL,
                        quality TEXT NOT NULL,
                        banner TEXT NOT NULL DEFAULT 'Battle Cats Wiki',
                        image_url TEXT NOT NULL DEFAULT '',
                        is_collab BOOLEAN NOT NULL DEFAULT FALSE,
                        traits_json TEXT NOT NULL DEFAULT '[]',
                        is_favorite BOOLEAN NOT NULL DEFAULT FALSE,
                        level INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1),
                        obtained_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                """)
                cur.execute("""
                    ALTER TABLE test_owned_battle_cats
                    ADD COLUMN IF NOT EXISTS level INTEGER NOT NULL DEFAULT 1;
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_test_owned_battle_cats_user
                    ON test_owned_battle_cats (
                        guild_id, user_id, is_favorite DESC, obtained_at DESC, id DESC
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS test_battle_cat_daily_limits (
                        guild_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        roll_date DATE NOT NULL,
                        card_draw_count INTEGER NOT NULL DEFAULT 0
                            CHECK (card_draw_count >= 0 AND card_draw_count <= 5),
                        deck_open_count INTEGER NOT NULL DEFAULT 0
                            CHECK (deck_open_count >= 0 AND deck_open_count <= 1),
                        PRIMARY KEY (guild_id, user_id, roll_date)
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS test_battle_cat_tickets (
                        guild_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        rare_ticket BIGINT NOT NULL DEFAULT 0 CHECK (rare_ticket >= 0),
                        pack_ticket BIGINT NOT NULL DEFAULT 0 CHECK (pack_ticket >= 0),
                        platinum_ticket BIGINT NOT NULL DEFAULT 0 CHECK (platinum_ticket >= 0),
                        legend_ticket BIGINT NOT NULL DEFAULT 0 CHECK (legend_ticket >= 0),
                        PRIMARY KEY (guild_id, user_id)
                    );
                """)
        log.info("Ensured Battle Cats database schema")
    finally:
        conn.close()


def reserve_battle_cat_daily_use(*, guild_id: int, user_id: int, kind: str) -> Optional[int]:
    """Atomically reserve one card draw or deck opening for the current UTC day."""
    if kind not in {"card", "deck"}:
        raise ValueError("kind must be 'card' or 'deck'")

    column = "card_draw_count" if kind == "card" else "deck_open_count"
    limit = BATTLE_CAT_CARD_DAILY_LIMIT if kind == "card" else BATTLE_CAT_DECK_DAILY_LIMIT
    other_column = "deck_open_count" if kind == "card" else "card_draw_count"

    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                # Column names come only from the validated fixed choices above.
                cur.execute(
                    f"""
                    INSERT INTO test_battle_cat_daily_limits (
                        guild_id, user_id, roll_date, {column}, {other_column}
                    )
                    VALUES (%s, %s, (NOW() AT TIME ZONE 'UTC')::date, 1, 0)
                    ON CONFLICT (guild_id, user_id, roll_date)
                    DO UPDATE SET {column} = test_battle_cat_daily_limits.{column} + 1
                    WHERE test_battle_cat_daily_limits.{column} < %s
                    RETURNING {column};
                    """,
                    (guild_id, user_id, limit),
                )
                row = cur.fetchone()
                return int(row[0]) if row else None
    finally:
        conn.close()


def release_battle_cat_daily_use(*, guild_id: int, user_id: int, kind: str) -> None:
    """Return a reserved daily use when generation/storage fails."""
    if kind not in {"card", "deck"}:
        raise ValueError("kind must be 'card' or 'deck'")
    column = "card_draw_count" if kind == "card" else "deck_open_count"
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE test_battle_cat_daily_limits
                    SET {column} = GREATEST({column} - 1, 0)
                    WHERE guild_id = %s
                      AND user_id = %s
                      AND roll_date = (NOW() AT TIME ZONE 'UTC')::date;
                    """,
                    (guild_id, user_id),
                )
                cur.execute("""
                    DELETE FROM test_battle_cat_daily_limits
                    WHERE guild_id = %s
                      AND user_id = %s
                      AND roll_date = (NOW() AT TIME ZONE 'UTC')::date
                      AND card_draw_count = 0
                      AND deck_open_count = 0;
                """, (guild_id, user_id))
    finally:
        conn.close()


def get_battle_cat_daily_counts(*, guild_id: int, user_id: int) -> dict[str, int]:
    """Return today's UTC single-card and deck usage without reserving anything."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT card_draw_count, deck_open_count
                FROM test_battle_cat_daily_limits
                WHERE guild_id = %s
                  AND user_id = %s
                  AND roll_date = (NOW() AT TIME ZONE 'UTC')::date;
                """,
                (guild_id, user_id),
            )
            row = cur.fetchone()
        return {"card": int(row[0]), "deck": int(row[1])} if row else {"card": 0, "deck": 0}
    finally:
        conn.close()


TICKET_COLUMNS: dict[str, str] = {
    "rare": "rare_ticket",
    "pack": "pack_ticket",
    "platinum": "platinum_ticket",
    "legend": "legend_ticket",
}

TICKET_LABELS: dict[str, str] = {
    "rare": "Rare Ticket",
    "pack": "Pack Ticket",
    "platinum": "Platinum Ticket",
    "legend": "Legend Ticket",
}


def get_battle_cat_tickets(*, guild_id: int, user_id: int) -> dict[str, int]:
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO test_battle_cat_tickets (guild_id, user_id)
                    VALUES (%s, %s)
                    ON CONFLICT (guild_id, user_id) DO NOTHING;
                """, (guild_id, user_id))
                cur.execute("""
                    SELECT rare_ticket, pack_ticket, platinum_ticket, legend_ticket
                    FROM test_battle_cat_tickets
                    WHERE guild_id = %s AND user_id = %s;
                """, (guild_id, user_id))
                row = cur.fetchone()
        if row is None:
            return {key: 0 for key in TICKET_COLUMNS}
        return {
            "rare": int(row[0]),
            "pack": int(row[1]),
            "platinum": int(row[2]),
            "legend": int(row[3]),
        }
    finally:
        conn.close()


def add_battle_cat_tickets(*, guild_id: int, user_id: int, ticket_type: str, amount: int) -> int:
    if ticket_type not in TICKET_COLUMNS:
        raise ValueError("Unknown ticket type")
    if amount <= 0:
        raise ValueError("amount must be greater than 0")
    column = TICKET_COLUMNS[ticket_type]
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO test_battle_cat_tickets (guild_id, user_id, {column})
                    VALUES (%s, %s, %s)
                    ON CONFLICT (guild_id, user_id)
                    DO UPDATE SET {column} = test_battle_cat_tickets.{column} + EXCLUDED.{column}
                    RETURNING {column};
                    """,
                    (guild_id, user_id, amount),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("Database did not return updated ticket balance")
                return int(row[0])
    finally:
        conn.close()


def consume_battle_cat_ticket(*, guild_id: int, user_id: int, ticket_type: str) -> Optional[int]:
    """Atomically consume one ticket and return the remaining balance."""
    if ticket_type not in TICKET_COLUMNS:
        raise ValueError("Unknown ticket type")
    column = TICKET_COLUMNS[ticket_type]
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO test_battle_cat_tickets (guild_id, user_id)
                    VALUES (%s, %s)
                    ON CONFLICT (guild_id, user_id) DO NOTHING;
                """, (guild_id, user_id))
                cur.execute(
                    f"""
                    UPDATE test_battle_cat_tickets
                    SET {column} = {column} - 1
                    WHERE guild_id = %s AND user_id = %s AND {column} > 0
                    RETURNING {column};
                    """,
                    (guild_id, user_id),
                )
                row = cur.fetchone()
                return int(row[0]) if row else None
    finally:
        conn.close()


def refund_battle_cat_ticket(*, guild_id: int, user_id: int, ticket_type: str) -> None:
    add_battle_cat_tickets(
        guild_id=guild_id,
        user_id=user_id,
        ticket_type=ticket_type,
        amount=1,
    )


def reset_battle_cat_daily_limit(*, guild_id: int, user_id: int, kind: str) -> None:
    if kind not in {"card", "deck"}:
        raise ValueError("kind must be 'card' or 'deck'")
    column = "card_draw_count" if kind == "card" else "deck_open_count"
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE test_battle_cat_daily_limits
                    SET {column} = 0
                    WHERE guild_id = %s
                      AND user_id = %s
                      AND roll_date = (NOW() AT TIME ZONE 'UTC')::date;
                    """,
                    (guild_id, user_id),
                )
                cur.execute("""
                    DELETE FROM test_battle_cat_daily_limits
                    WHERE guild_id = %s
                      AND user_id = %s
                      AND roll_date = (NOW() AT TIME ZONE 'UTC')::date
                      AND card_draw_count = 0
                      AND deck_open_count = 0;
                """, (guild_id, user_id))
    finally:
        conn.close()


def next_utc_reset_timestamp() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    reset = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
    return int(reset.timestamp())


def save_owned_battle_cat(*, guild_id: int, user_id: int, pull: "BattleCatPull") -> int:
    """Persist one individual drawn card and return its ownership ID."""
    import json
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO test_owned_battle_cats (
                        guild_id, user_id, cat_name, wiki_title, rarity, quality,
                        banner, image_url, is_collab, traits_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id;
                """, (
                    guild_id, user_id, pull.name, pull.wiki_title, pull.rarity,
                    pull.quality, pull.banner, pull.image_url, pull.is_collab,
                    json.dumps(list(pull.traits)),
                ))
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("Database did not return Battle Cat card ID")
                return int(row[0])
    finally:
        conn.close()


def save_owned_battle_cat_pack(*, guild_id: int, user_id: int, cards: list[dict[str, Any]]) -> list[int]:
    """Persist every card in one pack in a single transaction."""
    import json
    conn = get_db_connection()
    ids: list[int] = []
    try:
        with conn:
            with conn.cursor() as cur:
                for card in cards:
                    pull: BattleCatPull = card["pull"]
                    cur.execute("""
                        INSERT INTO test_owned_battle_cats (
                            guild_id, user_id, cat_name, wiki_title, rarity, quality,
                            banner, image_url, is_collab, traits_json
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id;
                    """, (
                        guild_id, user_id, pull.name, pull.wiki_title, pull.rarity,
                        pull.quality, pull.banner, pull.image_url, pull.is_collab,
                        json.dumps(list(pull.traits)),
                    ))
                    row = cur.fetchone()
                    if row is None:
                        raise RuntimeError("Database did not return Battle Cat card ID")
                    ids.append(int(row[0]))
        return ids
    finally:
        conn.close()


def get_owned_battle_cats(*, guild_id: int, user_id: int) -> list[dict[str, Any]]:
    import json
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, cat_name, wiki_title, rarity, quality, banner, image_url,
                       is_collab, traits_json, is_favorite, level, obtained_at
                FROM test_owned_battle_cats
                WHERE guild_id = %s AND user_id = %s
                ORDER BY is_favorite DESC, obtained_at DESC, id DESC;
            """, (guild_id, user_id))
            rows = cur.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                traits = tuple(json.loads(row[8] or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                traits = ()
            result.append({
                "id": int(row[0]), "cat_name": str(row[1]), "wiki_title": str(row[2]),
                "rarity": str(row[3]), "quality": str(row[4]), "banner": str(row[5]),
                "image_url": str(row[6] or ""), "is_collab": bool(row[7]),
                "traits": traits, "is_favorite": bool(row[9]), "level": int(row[10]), "obtained_at": row[11],
            })
        return result
    finally:
        conn.close()


def set_owned_battle_cat_favorite(*, guild_id: int, user_id: int, card_id: int, is_favorite: bool) -> bool:
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE test_owned_battle_cats
                    SET is_favorite = %s
                    WHERE id = %s AND guild_id = %s AND user_id = %s
                    RETURNING id;
                """, (is_favorite, card_id, guild_id, user_id))
                return cur.fetchone() is not None
    finally:
        conn.close()



@dataclass(frozen=True)
class BattleCatPull:
    name: str
    rarity: str
    quality: str
    banner: str
    image_url: str
    wiki_title: str = ""
    traits: tuple[str, ...] = ()
    trait_icons: tuple[tuple[str, str], ...] = ()
    is_collab: bool = False


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


async def draw_battle_cat(
    *,
    minimum_rarity: Optional[str] = None,
    fixed_rarity: Optional[str] = None,
) -> BattleCatPull:
    """Roll rarity FIRST, then pull only from that rarity's wiki category.

    Example: if the roll is Uber Rare, ``random.choice`` is performed only on
    the cached ``Category:Uber Rare Cats`` pool. A Rare/Super Rare cat can
    never be selected and then relabeled as Uber.
    """
    if fixed_rarity is not None:
        if fixed_rarity not in BATTLE_CAT_RARITIES:
            raise ValueError(f"Unknown fixed rarity: {fixed_rarity}")
        rarity = fixed_rarity
    else:
        rarity = roll_battle_cat_rarity(minimum=minimum_rarity)
    roster = await fetch_battle_cat_roster()
    rarity_pool = roster.get(rarity, [])
    if not rarity_pool:
        raise RuntimeError(f"No Battle Cats Wiki entries loaded for {rarity}")

    collab_titles = await fetch_collaboration_cat_titles()
    collab_pool = [cat for cat in rarity_pool if cat["wiki_title"] in collab_titles]
    standard_pool = [cat for cat in rarity_pool if cat["wiki_title"] not in collab_titles]

    wants_collab = random.random() < COLLAB_PULL_CHANCE
    if wants_collab and collab_pool:
        selected_pool = collab_pool
        is_collab = True
    elif standard_pool:
        selected_pool = standard_pool
        is_collab = False
    elif collab_pool:
        # Defensive fallback if the wiki ever has a rarity containing only
        # collaboration units. Rarity correctness takes priority over 95/5.
        selected_pool = collab_pool
        is_collab = True
    else:
        raise RuntimeError(f"No selectable Battle Cats Wiki entries for {rarity}")

    cat = random.choice(selected_pool)
    image_url, traits, trait_icons = await asyncio.gather(
        fetch_wiki_cat_image_url(cat["wiki_title"]),
        fetch_wiki_cat_target_traits(cat["wiki_title"]),
        fetch_cat_trait_icons(cat["wiki_title"]),
    )
    return BattleCatPull(
        name=cat["name"],
        rarity=rarity,
        quality=roll_battle_cat_quality(),
        banner=cat.get("banner", "Battle Cats Wiki"),
        image_url=image_url,
        wiki_title=cat["wiki_title"],
        traits=traits,
        trait_icons=trait_icons,
        is_collab=is_collab,
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
        "User-Agent": "Mozilla/5.0 BattleCatsDiscordCard/3.0",
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


def _make_rainbow_gradient(width: int, height: int) -> Image.Image:
    """Create a smooth horizontal rainbow RGBA gradient for Legend/Bana cards."""
    stops = [
        (255, 70, 70),
        (255, 170, 55),
        (245, 225, 70),
        (70, 205, 105),
        (65, 170, 245),
        (105, 90, 235),
        (205, 80, 225),
        (255, 70, 120),
    ]
    image = Image.new("RGBA", (width, height))
    px = image.load()
    segments = len(stops) - 1
    for x in range(width):
        pos = (x / max(1, width - 1)) * segments
        index = min(int(pos), segments - 1)
        frac = pos - index
        c1, c2 = stops[index], stops[index + 1]
        color = tuple(int(c1[i] + (c2[i] - c1[i]) * frac) for i in range(3)) + (255,)
        for y in range(height):
            px[x, y] = color
    return image


def _paste_rounded_gradient(
    base: Image.Image,
    box: tuple[int, int, int, int],
    *,
    radius: int,
    alpha: int = 255,
) -> None:
    """Paste a rainbow gradient clipped to a rounded rectangle."""
    left, top, right, bottom = box
    w, h = right - left, bottom - top
    gradient = _make_rainbow_gradient(w, h)
    if alpha < 255:
        gradient.putalpha(alpha)
    mask = Image.new("L", (w, h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    base.paste(gradient, (left, top), mask)


def build_battle_cat_card_png(
    art_png: bytes,
    *,
    pull: BattleCatPull,
    trait_icon_bytes: Optional[dict[str, bytes]] = None,
) -> BytesIO:
    """Render one Battle Cats pull as a 900x1200 collectible card."""
    width, height = 900, 1200

    palette = {
        # Common -> uncommon -> premium rarity progression.
        "Rare": ((145, 150, 158), (42, 46, 52)),       # Gray
        "Super Rare": ((65, 180, 92), (24, 67, 35)),   # Green
        "Uber Rare": ((230, 180, 45), (88, 63, 10)),   # Gold
        # Bana Rare maps to Battle Cats Legend Rare. Its card gets a rainbow
        # accent below; red is retained as the readable dark/background tone.
        "Bana Rare": ((220, 55, 55), (72, 20, 20)),
    }
    accent, dark = palette.get(pull.rarity, palette["Rare"])
    rarity_cfg = BATTLE_CAT_RARITIES[pull.rarity]
    quality_cfg = BATTLE_CAT_QUALITIES[pull.quality]

    card = Image.new("RGBA", (width, height), dark + (255,))
    draw = ImageDraw.Draw(card)

    if pull.rarity == "Bana Rare":
        # True rainbow treatment on the generated Legend Rare card. Discord
        # embed sidebars can only use one color, so those use the red fallback.
        _paste_rounded_gradient(card, (18, 18, 882, 1182), radius=42)
        draw = ImageDraw.Draw(card)
        draw.rounded_rectangle((34, 34, 866, 1166), radius=34, fill=dark + (255,))
        _paste_rounded_gradient(card, (55, 55, 845, 180), radius=25, alpha=245)
        draw = ImageDraw.Draw(card)
    else:
        draw.rounded_rectangle((18, 18, 882, 1182), radius=42, fill=accent + (255,))
        draw.rounded_rectangle((34, 34, 866, 1166), radius=34, fill=dark + (255,))
        draw.rounded_rectangle((55, 55, 845, 180), radius=25, fill=accent + (235,))
    draw.rounded_rectangle((55, 200, 845, 735), radius=30, fill=(15, 18, 24, 235))
    draw.rounded_rectangle((55, 755, 845, 1135), radius=30, fill=(12, 15, 20, 235))

    title_font = _load_font(46, bold=True)
    subtitle_font = _load_font(27, bold=True)
    section_font = _load_font(29, bold=True)
    body_font = _load_font(24)
    small_font = _load_font(18, bold=True)
    stat_font = _load_font(25, bold=True)

    title_lines = _fit_text(draw, pull.name, title_font, 700)[:2]
    ty = 68
    for line in title_lines:
        draw.text((80, ty), line, font=title_font, fill="white")
        ty += 48

    subtitle = f"{pull.rarity.upper()}  •  {rarity_cfg['stars']}"
    if pull.is_collab:
        subtitle += "  •  COLLAB"
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
    draw.text((82, 785), "TARGET TRAITS", font=section_font, fill=accent)

    trait_icon_bytes = trait_icon_bytes or {}
    icon_x = 82
    icon_y = 830
    icon_size = 68
    shown = 0
    for trait in pull.traits:
        raw = trait_icon_bytes.get(trait)
        if not raw:
            continue
        try:
            icon = Image.open(BytesIO(raw)).convert("RGBA")
            icon.thumbnail((icon_size, icon_size), Image.Resampling.LANCZOS)
            card.alpha_composite(icon, (icon_x + (icon_size - icon.width) // 2, icon_y))
            label_box = draw.textbbox((0, 0), trait, font=small_font)
            label_w = label_box[2] - label_box[0]
            draw.text(
                (icon_x + (icon_size - label_w) / 2, icon_y + 70),
                trait,
                font=small_font,
                fill=(225, 228, 234),
            )
            icon_x += 115
            shown += 1
        except Exception:
            log.warning("Could not render trait icon for %s", trait, exc_info=True)

    if shown == 0:
        draw.text((85, 838), "No anti-trait category listed", font=body_font, fill=(205, 210, 218))

    # Keep the rolled quality information, but remove the old DRAW RESULT block.
    quality_stars = {"C": "★", "B": "★★", "A": "★★★", "S": "★★★★", "SS": "★★★★★"}
    info_y = 955
    draw.text((85, info_y), "Quality:", font=stat_font, fill="white")
    draw.text(
        (260, info_y + 2),
        f"{pull.quality}  {quality_stars[pull.quality]}  ({quality_cfg['label']})",
        font=body_font,
        fill=(215, 220, 228),
    )
    draw.text((85, info_y + 55), "Multiplier:", font=stat_font, fill="white")
    draw.text(
        (260, info_y + 57),
        f"x{quality_cfg['multiplier']:.2f}",
        font=body_font,
        fill=(215, 220, 228),
    )

    output = BytesIO()
    card.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


async def create_rendered_battle_cat_card(
    *,
    minimum_rarity: Optional[str] = None,
    fixed_rarity: Optional[str] = None,
) -> dict[str, Any]:
    pull = await draw_battle_cat(
        minimum_rarity=minimum_rarity,
        fixed_rarity=fixed_rarity,
    )
    art, trait_bytes = await asyncio.gather(
        download_cat_image_bytes(pull.name, pull.image_url),
        download_trait_icon_bytes(pull.trait_icons),
    )
    rendered = await asyncio.to_thread(
        build_battle_cat_card_png,
        art,
        pull=pull,
        trait_icon_bytes=trait_bytes,
    )
    return {"pull": pull, "png": rendered.getvalue()}


# ---------------------------------------------------------------------------
# Interactive pack UI
# ---------------------------------------------------------------------------
class BattleCatDeckView(discord.ui.View):
    def __init__(
        self, *, owner_id: int, cards: list[dict[str, Any]], pack_image_url: Optional[str] = None
    ) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.cards = cards
        self.current_index = 0
        self.opened = False
        self.pack_image_url = pack_image_url or random.choice(BATTLE_CAT_PACK_IMAGES)

        for index, card in enumerate(cards, start=1):
            card["filename"] = f"battle_cat_card_{owner_id}_{index}.png"
            card.setdefault("is_favorite", False)

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
        on_summary = self.opened and self.current_index >= len(self.cards)
        self.back_button.disabled = not self.opened or self.current_index == 0
        self.next_button.disabled = not self.opened or on_summary
        self.next_button.label = (
            "Summary" if self.opened and self.current_index == len(self.cards) - 1 else "Next"
        )
        self.favorite_button.disabled = not self.opened or on_summary
        if self.opened and not on_summary:
            self.favorite_button.label = (
                "⭐ Unfavorite" if self.cards[self.current_index].get("is_favorite") else "☆ Favorite"
            )
        elif on_summary:
            self.favorite_button.label = "☆ Favorite"

    def cover_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Battle Cats Rare Capsule Card Pack",
            description=(
                f"Contains **{len(self.cards)} Battle Cat cards**.\n"
                f"Card {len(self.cards)} is guaranteed to be **Super Rare or better**.\n\n"
                "Press **Open Pack** to reveal card 1."
            ),
            color=discord.Color.orange(),
        )
        embed.set_image(url=self.pack_image_url)
        return embed

    def current_file(self) -> discord.File:
        card = self.cards[self.current_index]
        return discord.File(BytesIO(card["png"]), filename=card["filename"])

    def summary_embed(self) -> discord.Embed:
        lines: list[str] = []
        rarity_counts: dict[str, int] = {}
        for index, card in enumerate(self.cards, start=1):
            pull: BattleCatPull = card["pull"]
            rarity_cfg = BATTLE_CAT_RARITIES[pull.rarity]
            rarity_counts[pull.rarity] = rarity_counts.get(pull.rarity, 0) + 1
            collab = " • COLLAB" if pull.is_collab else ""
            lines.append(
                f"**{index}.** {rarity_cfg['emoji']} **{pull.name}** — "
                f"{pull.rarity} • Quality `{pull.quality}`{collab}"
            )

        order = ["Rare", "Super Rare", "Uber Rare", "Bana Rare"]
        counts_text = " • ".join(
            f"{BATTLE_CAT_RARITIES[rarity]['emoji']} {rarity}: **{rarity_counts.get(rarity, 0)}**"
            for rarity in order
            if rarity_counts.get(rarity, 0)
        )
        embed = discord.Embed(
            title="Pack Summary",
            description="\n".join(lines),
            color=discord.Color.orange(),
        )
        if counts_text:
            embed.add_field(name="Rarity Breakdown", value=counts_text, inline=False)
        embed.set_footer(text=f"All {len(self.cards)} cards are stored in /cats_inventory")
        return embed

    def current_embed(self) -> discord.Embed:
        if self.current_index >= len(self.cards):
            return self.summary_embed()

        card = self.cards[self.current_index]
        pull: BattleCatPull = card["pull"]
        rarity_cfg = BATTLE_CAT_RARITIES[pull.rarity]
        guaranteed = self.current_index == len(self.cards) - 1

        trait_text = ", ".join(pull.traits) if pull.traits else "None listed"
        description = (
            f"**Level:** `1`\n"
            f"**Rarity:** {rarity_cfg['emoji']} {pull.rarity} {rarity_cfg['stars']}\n"
            f"**Quality:** `{pull.quality}`\n"
            f"**Targets:** {trait_text}"
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
        self.current_index = min(len(self.cards), self.current_index + 1)
        self._sync_buttons()
        if self.current_index >= len(self.cards):
            await interaction.response.edit_message(
                embed=self.summary_embed(),
                attachments=[],
                view=self,
            )
        else:
            await interaction.response.edit_message(
                embed=self.current_embed(),
                attachments=[self.current_file()],
                view=self,
            )

    @discord.ui.button(label="☆ Favorite", style=discord.ButtonStyle.success, row=0)
    async def favorite_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self.opened or self.current_index >= len(self.cards):
            return
        card = self.cards[self.current_index]
        card_id = card.get("owned_card_id")
        if card_id is None:
            await interaction.response.send_message("This card has not been stored yet.", ephemeral=True)
            return
        new_value = not bool(card.get("is_favorite"))
        changed = set_owned_battle_cat_favorite(
            guild_id=interaction.guild_id,
            user_id=self.owner_id,
            card_id=int(card_id),
            is_favorite=new_value,
        )
        if not changed:
            await interaction.response.send_message("Card not found in your inventory.", ephemeral=True)
            return
        card["is_favorite"] = new_value
        self._sync_buttons()
        await interaction.response.edit_message(view=self)

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
# Stored Battle Cats inventory UI
# ---------------------------------------------------------------------------
def _favorite_marker(card: dict[str, Any]) -> str:
    return "⭐ " if card.get("is_favorite") else ""


def create_cats_inventory_page_embed(
    *, cards: list[dict[str, Any]], page: int, owner: discord.Member | discord.User,
    per_page: int = 5, rarity_filter: str = "All"
) -> discord.Embed:
    total_pages = max(1, (len(cards) + per_page - 1) // per_page)
    start = page * per_page
    page_cards = cards[start:start + per_page]
    embed = discord.Embed(
        title="Battle Cats Inventory",
        description=(
            f"⭐ Favorites are shown first. **Rarity filter:** `{rarity_filter}`\n"
            "Press a numbered button to view a card."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_author(name=f"{owner.display_name}'s Battle Cats Cards", icon_url=owner.display_avatar.url)
    for slot, card in enumerate(page_cards, start=1):
        rarity_cfg = BATTLE_CAT_RARITIES.get(card["rarity"], BATTLE_CAT_RARITIES["Rare"])
        collab = " • COLLAB" if card.get("is_collab") else ""
        embed.add_field(
            name=f"{slot}. {_favorite_marker(card)}{rarity_cfg['emoji']} {card['cat_name']}",
            value=(
                f"**{card['rarity']}** {rarity_cfg['stars']} • Quality `{card['quality']}`{collab}\n"
                f"Level: **{card.get('level', 1)}** • Card ID: `{card['id']}`"
            ),
            inline=False,
        )
    embed.set_footer(text=f"Page {page + 1} of {total_pages} • {len(cards)} total cards")
    return embed


def create_cats_inventory_detail_embed(
    *, card: dict[str, Any], owner: discord.Member | discord.User, position: int, total_cards: int
) -> discord.Embed:
    rarity_cfg = BATTLE_CAT_RARITIES.get(card["rarity"], BATTLE_CAT_RARITIES["Rare"])
    quality_cfg = BATTLE_CAT_QUALITIES.get(card["quality"], BATTLE_CAT_QUALITIES["C"])
    traits = ", ".join(card.get("traits") or ()) or "None listed"
    embed = discord.Embed(
        title=f"{_favorite_marker(card)}{rarity_cfg['emoji']} {card['cat_name']}",
        description=(
            f"**Card ID:** `{card['id']}`\n"
            f"**Level:** `{card.get('level', 1)}`\n"
            f"**Rarity:** {card['rarity']} {rarity_cfg['stars']}\n"
            f"**Quality:** `{card['quality']}` ({quality_cfg['label']})\n"
            f"**Multiplier:** x{quality_cfg['multiplier']:.2f}\n"
            f"**Collab:** {'Yes' if card.get('is_collab') else 'No'}\n"
            f"**Targets:** {traits}"
        ),
        color=rarity_cfg["color"],
    )
    embed.set_author(name=f"Owned by {owner.display_name}", icon_url=owner.display_avatar.url)
    if card.get("image_url"):
        embed.set_image(url=card["image_url"])
    if card.get("obtained_at") is not None:
        embed.add_field(
            name="Obtained",
            value=discord.utils.format_dt(card["obtained_at"], style="F"),
            inline=False,
        )
    embed.set_footer(text=f"Card {position + 1} of {total_cards}")
    return embed


class CatsRarityFilterSelect(discord.ui.Select):
    def __init__(self, inventory_view: "CatsInventoryView") -> None:
        self.inventory_view = inventory_view
        options = [
            discord.SelectOption(label="All rarities", value="All", emoji="📚", default=True),
            discord.SelectOption(label="Rare", value="Rare", emoji="⚪"),
            discord.SelectOption(label="Super Rare", value="Super Rare", emoji="🟢"),
            discord.SelectOption(label="Uber Rare", value="Uber Rare", emoji="🟡"),
            discord.SelectOption(label="Legend / Bana Rare", value="Bana Rare", emoji="🌈"),
        ]
        super().__init__(
            placeholder="Filter inventory by rarity",
            min_values=1,
            max_values=1,
            options=options,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        rarity = self.values[0]
        self.inventory_view.apply_rarity_filter(rarity)
        for option in self.options:
            option.default = option.value == rarity
        self.inventory_view._sync_buttons()
        await interaction.response.edit_message(
            embed=self.inventory_view.current_embed(),
            view=self.inventory_view,
        )


class CatsInventoryView(discord.ui.View):
    PER_PAGE = 5

    def __init__(
        self, *, owner_id: int, owner: discord.Member | discord.User,
        cards: list[dict[str, Any]], guild_id: int
    ) -> None:
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.owner = owner
        self.all_cards = cards
        self.cards = cards
        self.guild_id = guild_id
        self.rarity_filter = "All"
        self.page = 0
        self.selected_index: Optional[int] = None
        self.add_item(CatsRarityFilterSelect(self))
        self._sync_buttons()

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.cards) + self.PER_PAGE - 1) // self.PER_PAGE)

    def page_cards(self) -> list[dict[str, Any]]:
        start = self.page * self.PER_PAGE
        return self.cards[start:start + self.PER_PAGE]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This is not your Battle Cats inventory.", ephemeral=True)
            return False
        return True

    def apply_rarity_filter(self, rarity: str) -> None:
        self.rarity_filter = rarity
        if rarity == "All":
            self.cards = list(self.all_cards)
        else:
            self.cards = [card for card in self.all_cards if card.get("rarity") == rarity]
        self.page = 0
        self.selected_index = None

    def _sync_buttons(self) -> None:
        page_count = len(self.page_cards())
        slots = [self.slot1, self.slot2, self.slot3, self.slot4, self.slot5]
        for index, button in enumerate(slots):
            button.disabled = self.selected_index is not None or index >= page_count
            button.label = str(index + 1)
        self.first_page.disabled = self.selected_index is not None or self.page == 0
        self.previous_page.disabled = self.selected_index is not None or self.page == 0
        self.next_page.disabled = self.selected_index is not None or self.page >= self.total_pages - 1
        self.back_button.disabled = self.selected_index is None
        self.favorite_button.disabled = self.selected_index is None
        if self.selected_index is not None:
            self.favorite_button.label = (
                "⭐ Unfavorite" if self.cards[self.selected_index].get("is_favorite") else "☆ Favorite"
            )

    def current_embed(self) -> discord.Embed:
        if self.selected_index is None:
            return create_cats_inventory_page_embed(
                cards=self.cards, page=self.page, owner=self.owner, per_page=self.PER_PAGE,
                rarity_filter=self.rarity_filter
            )
        return create_cats_inventory_detail_embed(
            card=self.cards[self.selected_index], owner=self.owner,
            position=self.selected_index, total_cards=len(self.cards)
        )

    async def _select(self, interaction: discord.Interaction, slot: int) -> None:
        index = self.page * self.PER_PAGE + slot
        if index >= len(self.cards):
            await interaction.response.send_message("No card is in that slot.", ephemeral=True)
            return
        self.selected_index = index
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="1", style=discord.ButtonStyle.primary, row=0)
    async def slot1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._select(interaction, 0)

    @discord.ui.button(label="2", style=discord.ButtonStyle.primary, row=0)
    async def slot2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._select(interaction, 1)

    @discord.ui.button(label="3", style=discord.ButtonStyle.primary, row=0)
    async def slot3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._select(interaction, 2)

    @discord.ui.button(label="4", style=discord.ButtonStyle.primary, row=0)
    async def slot4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._select(interaction, 3)

    @discord.ui.button(label="5", style=discord.ButtonStyle.primary, row=0)
    async def slot5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._select(interaction, 4)

    @discord.ui.button(label="First", emoji="⏮️", style=discord.ButtonStyle.secondary, row=1)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 0
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_index = None
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="☆ Favorite", style=discord.ButtonStyle.success, row=1)
    async def favorite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.selected_index is None:
            return
        card = self.cards[self.selected_index]
        new_value = not bool(card.get("is_favorite"))
        changed = set_owned_battle_cat_favorite(
            guild_id=self.guild_id, user_id=self.owner_id, card_id=card["id"], is_favorite=new_value
        )
        if not changed:
            await interaction.response.send_message("Card not found.", ephemeral=True)
            return
        card["is_favorite"] = new_value
        selected_id = card["id"]
        self.all_cards.sort(
            key=lambda item: (
                not bool(item.get("is_favorite")),
                -(item["obtained_at"].timestamp() if item.get("obtained_at") else 0),
                -item["id"],
            )
        )
        if self.rarity_filter == "All":
            self.cards = list(self.all_cards)
        else:
            self.cards = [item for item in self.all_cards if item.get("rarity") == self.rarity_filter]
        self.selected_index = next(i for i, item in enumerate(self.cards) if item["id"] == selected_id)
        self.page = self.selected_index // self.PER_PAGE
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="Next", emoji="➡️", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True


class DrawFavoriteView(discord.ui.View):
    """Favorite/unfavorite a newly drawn single card directly from its result."""
    def __init__(self, *, guild_id: int, owner_id: int, card_id: int) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.card_id = card_id
        self.is_favorite = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the owner can favorite this card.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="☆ Favorite", style=discord.ButtonStyle.secondary)
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        new_value = not self.is_favorite
        changed = set_owned_battle_cat_favorite(
            guild_id=self.guild_id, user_id=self.owner_id, card_id=self.card_id, is_favorite=new_value
        )
        if not changed:
            await interaction.response.send_message("Card not found in your inventory.", ephemeral=True)
            return
        self.is_favorite = new_value
        button.label = "⭐ Favorited" if new_value else "☆ Favorite"
        button.style = discord.ButtonStyle.success if new_value else discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self)


class PullConfirmationView(discord.ui.View):
    """Owner-only confirmation gate. No limit/ticket is consumed until Confirm."""
    def __init__(self, *, owner_id: int, executor) -> None:
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.executor = executor
        self.used = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This confirmation is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.used:
            await interaction.response.send_message("This confirmation has already been used.", ephemeral=True)
            return
        self.used = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await self.executor(interaction)

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.used = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Pull cancelled.", embed=None, view=self)


async def _execute_ticket_pull(
    interaction: discord.Interaction, *, ticket_type: str, fixed_rarity: str
) -> None:
    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return
    user_id = interaction.user.id
    consumed = False
    stored = False
    try:
        remaining = consume_battle_cat_ticket(guild_id=guild_id, user_id=user_id, ticket_type=ticket_type)
        if remaining is None:
            await interaction.followup.send(f"You do not have a **{TICKET_LABELS[ticket_type]}**.", ephemeral=True)
            return
        consumed = True
        card = await create_rendered_battle_cat_card(fixed_rarity=fixed_rarity)
        pull: BattleCatPull = card["pull"]
        card_id = save_owned_battle_cat(guild_id=guild_id, user_id=user_id, pull=pull)
        stored = True
        rarity_cfg = BATTLE_CAT_RARITIES[pull.rarity]
        filename = f"battle_cat_{ticket_type}_{user_id}_{card_id}.png"
        file = discord.File(BytesIO(card["png"]), filename=filename)
        embed = discord.Embed(
            title=f"{rarity_cfg['emoji']} {pull.name}",
            description=(
                f"**Level:** `1`\n"
                f"**Rarity:** {pull.rarity} {rarity_cfg['stars']}\n"
                f"**Quality:** `{pull.quality}`\n"
                f"**Targets:** {', '.join(pull.traits) if pull.traits else 'None listed'}\n"
                f"**Collab:** {'Yes' if pull.is_collab else 'No'}\n\n"
                f"Added to `/cats_inventory` as card ID `{card_id}`.\n"
                f"**{TICKET_LABELS[ticket_type]}s remaining:** `{remaining}`"
            ),
            color=rarity_cfg["color"],
        )
        embed.set_image(url=f"attachment://{filename}")
        await interaction.followup.send(
            embed=embed, file=file,
            view=DrawFavoriteView(guild_id=guild_id, owner_id=user_id, card_id=card_id),
        )
    except Exception as error:
        log.exception("Failed %s Battle Cats ticket pull: %s", ticket_type, error)
        await interaction.followup.send(
            f"An error occurred during the {TICKET_LABELS[ticket_type]} pull. Your ticket was returned.",
            ephemeral=True,
        )
    finally:
        if consumed and not stored:
            try:
                refund_battle_cat_ticket(guild_id=guild_id, user_id=user_id, ticket_type=ticket_type)
            except Exception:
                log.exception("Failed to refund %s ticket for user %s", ticket_type, user_id)


async def _execute_normal_card_pull(interaction: discord.Interaction) -> None:
    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return
    user_id = interaction.user.id
    reserved = False
    stored = False
    try:
        count = reserve_battle_cat_daily_use(guild_id=guild_id, user_id=user_id, kind="card")
        if count is None:
            reset = next_utc_reset_timestamp()
            await interaction.followup.send(
                f"You have used all **{BATTLE_CAT_CARD_DAILY_LIMIT}** Battle Cat card draws today. "
                f"Draws reset <t:{reset}:R> at <t:{reset}:F>.", ephemeral=True
            )
            return
        reserved = True
        card = await create_rendered_battle_cat_card()
        pull: BattleCatPull = card["pull"]
        card_id = save_owned_battle_cat(guild_id=guild_id, user_id=user_id, pull=pull)
        stored = True
        rarity_cfg = BATTLE_CAT_RARITIES[pull.rarity]
        filename = f"battle_cat_{user_id}_{card_id}.png"
        file = discord.File(BytesIO(card["png"]), filename=filename)
        remaining = BATTLE_CAT_CARD_DAILY_LIMIT - count
        embed = discord.Embed(
            title=f"{rarity_cfg['emoji']} {pull.name}",
            description=(
                f"**Level:** `1`\n"
                f"**Rarity:** {pull.rarity} {rarity_cfg['stars']}\n"
                f"**Quality:** `{pull.quality}`\n"
                f"**Targets:** {', '.join(pull.traits) if pull.traits else 'None listed'}\n"
                f"**Collab:** {'Yes' if pull.is_collab else 'No'}\n\n"
                f"Added to `/cats_inventory` as card ID `{card_id}`."
            ),
            color=rarity_cfg["color"],
        )
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"{remaining}/{BATTLE_CAT_CARD_DAILY_LIMIT} single-card draws remaining today")
        await interaction.followup.send(
            embed=embed, file=file,
            view=DrawFavoriteView(guild_id=guild_id, owner_id=user_id, card_id=card_id),
        )
    except Exception as error:
        log.exception("Failed to draw/store Battle Cat card: %s", error)
        await interaction.followup.send(
            "An error occurred while drawing or storing the Battle Cat card. Your daily draw was returned.",
            ephemeral=True,
        )
    finally:
        if reserved and not stored:
            try:
                release_battle_cat_daily_use(guild_id=guild_id, user_id=user_id, kind="card")
            except Exception:
                log.exception("Failed to return Battle Cat card draw for user %s", user_id)


async def _execute_deck_pull(
    interaction: discord.Interaction, *, pack_image_url: Optional[str] = None
) -> None:
    """Open a pack using the free daily allowance first, then a Pack Ticket."""
    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    user_id = interaction.user.id
    reserved_daily = False
    consumed_pack_ticket = False
    stored = False
    payment_source = "daily"
    daily_count: Optional[int] = None
    ticket_remaining: Optional[int] = None

    try:
        # Always spend the free daily pack first. If that allowance is already
        # exhausted, fall back to one Pack Ticket instead of blocking the user.
        daily_count = reserve_battle_cat_daily_use(
            guild_id=guild_id, user_id=user_id, kind="deck"
        )

        if daily_count is not None:
            reserved_daily = True
        else:
            ticket_remaining = consume_battle_cat_ticket(
                guild_id=guild_id, user_id=user_id, ticket_type="pack"
            )
            if ticket_remaining is None:
                reset = next_utc_reset_timestamp()
                await interaction.followup.send(
                    (
                        f"You have used all **{BATTLE_CAT_DECK_DAILY_LIMIT}** free pack "
                        f"opening(s) today and you do not have a **Pack Ticket**.\n"
                        f"Free pack openings reset <t:{reset}:R> at <t:{reset}:F>."
                    ),
                    ephemeral=True,
                )
                return
            consumed_pack_ticket = True
            payment_source = "ticket"

        cards = await create_battle_cat_deck_cards()
        card_ids = save_owned_battle_cat_pack(
            guild_id=guild_id, user_id=user_id, cards=cards
        )
        for card, card_id in zip(cards, card_ids):
            card["owned_card_id"] = card_id
        stored = True

        view = BattleCatDeckView(
            owner_id=user_id, cards=cards, pack_image_url=pack_image_url
        )
        cover = view.cover_embed()

        if payment_source == "daily":
            remaining = max(0, BATTLE_CAT_DECK_DAILY_LIMIT - int(daily_count or 0))
            payment_text = (
                f"Used your **free daily pack opening**.\n"
                f"**{remaining}/{BATTLE_CAT_DECK_DAILY_LIMIT}** free pack openings remaining today."
            )
        else:
            payment_text = (
                "Your free daily pack allowance was already exhausted, so this opening "
                "used **1 Pack Ticket**.\n"
                f"**Pack Tickets remaining:** `{ticket_remaining}`"
            )

        cover.description = (cover.description or "") + (
            f"\n\nAll **{len(cards)} cards** have been added to `/cats_inventory`."
            f"\n{payment_text}"
        )
        await interaction.followup.send(embed=cover, view=view)

    except Exception as error:
        log.exception("Failed to create/store Battle Cats deck: %s", error)
        if payment_source == "ticket":
            failure_text = (
                "An error occurred while creating or storing the Battle Cats card pack. "
                "Your Pack Ticket was returned."
            )
        else:
            failure_text = (
                "An error occurred while creating or storing the Battle Cats card pack. "
                "Your daily pack opening was returned."
            )
        await interaction.followup.send(failure_text, ephemeral=True)

    finally:
        if not stored:
            if reserved_daily:
                try:
                    release_battle_cat_daily_use(
                        guild_id=guild_id, user_id=user_id, kind="deck"
                    )
                except Exception:
                    log.exception(
                        "Failed to return Battle Cats deck opening for user %s", user_id
                    )
            if consumed_pack_ticket:
                try:
                    refund_battle_cat_ticket(
                        guild_id=guild_id, user_id=user_id, ticket_type="pack"
                    )
                except Exception:
                    log.exception(
                        "Failed to refund Pack Ticket for user %s", user_id
                    )


# ---------------------------------------------------------------------------
# Slash-command registration
# ---------------------------------------------------------------------------
def register_battle_cats_commands(
    bot: discord.ext.commands.Bot,
    guild: discord.Object,
) -> None:
    """Register stored Battle Cats card commands on an existing discord.py bot."""
    ensure_battle_cats_schema()

    @bot.tree.command(
        name="admin_reset_cat_limit",
        description="Admin: reset a user's Battle Cats card or pack daily limit",
        guild=guild,
    )
    @app_commands.describe(user="User whose limit should be reset", limit_type="Which daily limit to reset")
    @app_commands.choices(limit_type=[
        app_commands.Choice(name="Card draws", value="card"),
        app_commands.Choice(name="Card packs", value="deck"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_reset_cat_limit(
        interaction: discord.Interaction,
        user: discord.Member,
        limit_type: app_commands.Choice[str],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        reset_battle_cat_daily_limit(
            guild_id=interaction.guild_id,
            user_id=user.id,
            kind=limit_type.value,
        )
        label = "card draw" if limit_type.value == "card" else "card pack"
        await interaction.response.send_message(
            f"Reset today's **{label}** limit for {user.mention}.",
            ephemeral=True,
        )

    @bot.tree.command(
        name="admin_add_cat_tickets",
        description="Admin: add Battle Cats tickets to a user",
        guild=guild,
    )
    @app_commands.describe(user="User receiving tickets", ticket_type="Ticket type", amount="Number of tickets to add")
    @app_commands.choices(ticket_type=[
        app_commands.Choice(name="Rare Ticket", value="rare"),
        app_commands.Choice(name="Pack Ticket", value="pack"),
        app_commands.Choice(name="Platinum Ticket", value="platinum"),
        app_commands.Choice(name="Legend Ticket", value="legend"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_add_cat_tickets(
        interaction: discord.Interaction,
        user: discord.Member,
        ticket_type: app_commands.Choice[str],
        amount: app_commands.Range[int, 1, 100000],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        new_balance = add_battle_cat_tickets(
            guild_id=interaction.guild_id,
            user_id=user.id,
            ticket_type=ticket_type.value,
            amount=int(amount),
        )
        await interaction.response.send_message(
            f"Added **{amount}x {TICKET_LABELS[ticket_type.value]}** to {user.mention}. "
            f"New balance: **{new_balance}**.",
            ephemeral=True,
        )

    @bot.tree.command(
        name="cat_tickets",
        description="See your Battle Cats ticket balances",
        guild=guild,
    )
    async def cat_tickets(interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        balances = get_battle_cat_tickets(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
        )
        embed = discord.Embed(
            title="Battle Cats Tickets",
            description=(
                f"🎟️ **Rare Ticket:** `{balances['rare']}`\n"
                f"📦 **Pack Ticket:** `{balances['pack']}`\n"
                f"💿 **Platinum Ticket:** `{balances['platinum']}`\n"
                f"🌈 **Legend Ticket:** `{balances['legend']}`"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(
        name="platinum_pull",
        description="Use 1 Platinum Ticket for a guaranteed Uber Rare Battle Cat card",
        guild=guild,
    )
    async def platinum_pull(interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        balances = get_battle_cat_tickets(guild_id=interaction.guild_id, user_id=interaction.user.id)
        embed = discord.Embed(
            title="Confirm Platinum Pull",
            description=(
                "Guaranteed **Uber Rare** Battle Cat card.\n\n"
                f"💿 **Platinum Tickets:** `{balances['platinum']}`\n"
                "**Cost:** `1 Platinum Ticket`\n\n"
                "The ticket is consumed only after you press **Confirm**."
            ),
            color=discord.Color.gold(),
        )
        embed.set_image(url=PLATINUM_PULL_IMAGE)
        async def executor(component_interaction: discord.Interaction) -> None:
            await _execute_ticket_pull(component_interaction, ticket_type="platinum", fixed_rarity="Uber Rare")
        await interaction.response.send_message(
            embed=embed, view=PullConfirmationView(owner_id=interaction.user.id, executor=executor)
        )

    @bot.tree.command(
        name="legend_pull",
        description="Use 1 Legend Ticket: 95% Uber Rare, 5% Legend / Bana Rare",
        guild=guild,
    )
    async def legend_pull(interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        balances = get_battle_cat_tickets(guild_id=interaction.guild_id, user_id=interaction.user.id)
        embed = discord.Embed(
            title="Confirm Legend Pull",
            description=(
                "**Legend Ticket odds:**\n"
                "🟡 **Uber Rare:** `95%`\n"
                "🌈 **Legend / Bana Rare:** `5%`\n\n"
                f"🌈 **Legend Tickets:** `{balances['legend']}`\n"
                "**Cost:** `1 Legend Ticket`\n\n"
                "The ticket is consumed only after you press **Confirm**."
            ),
            color=discord.Color.red(),
        )
        embed.set_image(url=LEGEND_PULL_IMAGE)
        async def executor(component_interaction: discord.Interaction) -> None:
            rarity = "Bana Rare" if random.random() < 0.05 else "Uber Rare"
            await _execute_ticket_pull(
                component_interaction,
                ticket_type="legend",
                fixed_rarity=rarity,
            )
        await interaction.response.send_message(
            embed=embed, view=PullConfirmationView(owner_id=interaction.user.id, executor=executor)
        )

    @bot.tree.command(
        name="battle_cat_card",
        description="Draw and store a Battle Cats card (5 per UTC day)",
        guild=guild,
    )
    async def battle_cat_card(interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        counts = get_battle_cat_daily_counts(guild_id=interaction.guild_id, user_id=interaction.user.id)
        tickets = get_battle_cat_tickets(guild_id=interaction.guild_id, user_id=interaction.user.id)
        remaining = max(0, BATTLE_CAT_CARD_DAILY_LIMIT - counts["card"])
        embed = discord.Embed(
            title="Confirm Battle Cat Card Draw",
            description=(
                f"**Daily draws remaining:** `{remaining}/{BATTLE_CAT_CARD_DAILY_LIMIT}`\n"
                f"🎟️ **Rare Tickets owned:** `{tickets['rare']}`\n\n"
                "This normal draw uses your daily allowance; it does **not** consume a Rare Ticket.\n"
                "Your daily use is consumed only after you press **Confirm**."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_image(url=NORMAL_CARD_PULL_IMAGE)
        async def executor(component_interaction: discord.Interaction) -> None:
            await _execute_normal_card_pull(component_interaction)
        await interaction.response.send_message(
            embed=embed, view=PullConfirmationView(owner_id=interaction.user.id, executor=executor)
        )

    @bot.tree.command(
        name="open_battle_cat_deck",
        description="Open and store a seven-card Battle Cats pack (1 per UTC day)",
        guild=guild,
    )
    async def open_battle_cat_deck(interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        counts = get_battle_cat_daily_counts(guild_id=interaction.guild_id, user_id=interaction.user.id)
        tickets = get_battle_cat_tickets(guild_id=interaction.guild_id, user_id=interaction.user.id)
        remaining = max(0, BATTLE_CAT_DECK_DAILY_LIMIT - counts["deck"])
        pack_image = random.choice(BATTLE_CAT_PACK_IMAGES)
        embed = discord.Embed(
            title="Confirm Battle Cats Card Pack",
            description=(
                f"Contains **{BATTLE_CAT_PACK_SIZE} cards**.\n"
                f"**Daily packs remaining:** `{remaining}/{BATTLE_CAT_DECK_DAILY_LIMIT}`\n"
                f"📦 **Pack Tickets owned:** `{tickets['pack']}`\n\n"
                "The bot uses your **free daily pack opening first**. If no free pack "
                "opening remains, **1 Pack Ticket** will be consumed instead.\n"
                "Nothing is consumed until you press **Confirm**."
            ),
            color=discord.Color.orange(),
        )
        embed.set_image(url=pack_image)
        async def executor(component_interaction: discord.Interaction) -> None:
            await _execute_deck_pull(component_interaction, pack_image_url=pack_image)
        await interaction.response.send_message(
            embed=embed, view=PullConfirmationView(owner_id=interaction.user.id, executor=executor)
        )

    @bot.tree.command(
        name="cats_inventory",
        description="View the Battle Cats cards stored in your collection",
        guild=guild,
    )
    async def cats_inventory(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if interaction.guild_id is None:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return
        try:
            cards = get_owned_battle_cats(
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
            )
            if not cards:
                embed = discord.Embed(
                    title="Battle Cats Inventory",
                    description=(
                        "You do not own any Battle Cats cards yet.\n\n"
                        "Use `/battle_cat_card` or `/open_battle_cat_deck` to obtain cards."
                    ),
                    color=discord.Color.blurple(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            view = CatsInventoryView(
                owner_id=interaction.user.id,
                owner=interaction.user,
                cards=cards,
                guild_id=interaction.guild_id,
            )
            await interaction.followup.send(embed=view.current_embed(), view=view)
        except Exception as error:
            log.exception("Failed to load Battle Cats inventory: %s", error)
            await interaction.followup.send(
                "An error occurred while loading your Battle Cats inventory.",
                ephemeral=True,
            )


__all__ = [
    "BATTLE_CAT_RARITIES",
    "BATTLE_CAT_QUALITIES",
    "BATTLE_CAT_CARD_DAILY_LIMIT",
    "BATTLE_CAT_DECK_DAILY_LIMIT",
    "TICKET_COLUMNS",
    "TICKET_LABELS",
    "BATTLE_CATS_WIKI_API",
    "WIKI_CATEGORY_BY_RARITY",
    "BattleCatPull",
    "BattleCatDeckView",
    "CatsInventoryView",
    "build_battle_cat_card_png",
    "create_battle_cat_deck_cards",
    "create_rendered_battle_cat_card",
    "draw_battle_cat",
    "get_battle_cat_daily_counts",
    "get_battle_cat_tickets",
    "add_battle_cat_tickets",
    "consume_battle_cat_ticket",
    "reset_battle_cat_daily_limit",
    "ensure_battle_cats_schema",
    "fetch_battle_cat_roster",
    "fetch_collaboration_cat_titles",
    "fetch_wiki_cat_image_url",
    "fetch_wiki_cat_target_traits",
    "fetch_cat_trait_icons",
    "fetch_trait_icon_url",
    "get_owned_battle_cats",
    "register_battle_cats_commands",
    "roll_battle_cat_quality",
    "roll_battle_cat_rarity",
    "save_owned_battle_cat",
    "save_owned_battle_cat_pack",
]
