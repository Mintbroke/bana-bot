import logging, sys
import discord
from datetime import datetime, timedelta
import psycopg2
import os
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,  # <- stdout so Railway won’t flag as error
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

# Set up database connection
def get_db_connection():
    return psycopg2.connect(
        os.getenv("DB_URL")
    )

def claim_daily(guild_id: int, user_id: int,
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
                log.info("Trying to claim daily")
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
                    RETURNING balance, num_tickets;
                    """,
                    (amount, now_utc, guild_id, user_id, now_utc, cooldown_secs)
                )
                row = cur.fetchone()
                log.info(row)
                if row:
                    return True, int(row[0]), int(row[1]), None

                # 2) If not granted, try to INSERT first-time claim
                cur.execute(
                    """
                    INSERT INTO test_table1 (guild_id, user_id, balance, num_tickets, last_daily)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (guild_id, user_id) DO NOTHING
                    RETURNING balance, num_tickets;
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

def scrape(guild_id: int, user_id: int,
           now_utc: datetime, cooldown: timedelta):
    """
    Returns: (granted: bool, balance: int, remaining_seconds: int | None)
    """
    con = get_db_connection()
    cooldown_secs = int(cooldown.total_seconds())
    try:
        with con:
            with con.cursor() as cur:
                # 1) Try to grant if row exists AND cooldown passed
                log.info("Trying to claim daily")
                cur.execute(
                    """
                    UPDATE test_table2
                    SET scraped = scraped + 1,
                        date = CURRENT_DATE,
                        last_scraped = %s
                    WHERE guild_id = %s
                    AND user_id  = %s
                    AND (last_scraped IS NULL
                            OR %s - last_scraped >= interval '10 minutes')
                    AND (date IS DISTINCT FROM CURRENT_DATE OR scraped < 30)
                    RETURNING scraped;
                    """,
                    (now_utc, guild_id, user_id, now_utc),
                )
                row = cur.fetchone()
                log.info(row)
                if row:
                    return True, int(row[0]), None

                # 2) If not granted, try to INSERT first-time claim
                cur.execute(
                    """
                    INSERT INTO test_table1 (guild_id, user_id, scraped, date, last_scraped)
                    VALUES (%s, %s, %s, CURRENT_DATE, %s)
                    ON CONFLICT (guild_id, user_id) DO NOTHING
                    RETURNING scraped;
                    """,
                    (guild_id, user_id, 1, now_utc)
                )
                row = cur.fetchone()
                if row:
                    # Insert succeeded → first-ever claim granted
                    return True, int(row[0]), None

                # 3) Row exists but cooldown not met → compute remaining
                cur.execute(
                    "SELECT scraped, last_scraped FROM test_table1 WHERE guild_id=%s AND user_id=%s",
                    (guild_id, user_id)
                )
                scraped, last_scraped = cur.fetchone()
                delta = (now_utc - last_scraped).total_seconds()
                remaining = max(0, cooldown_secs - int(delta))
                return False, int(scraped), remaining
    finally:
        con.close()