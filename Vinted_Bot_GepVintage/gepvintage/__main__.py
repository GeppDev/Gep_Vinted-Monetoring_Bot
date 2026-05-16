from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from gepvintage.bot import GepVintageBot
from gepvintage.config import load_settings
from gepvintage.scraper_pool import ScraperPool
from gepvintage.storage import Storage


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    db_path = Path(settings.data_dir) / "gepvintage.sqlite3"

    async def runner() -> None:
        storage = Storage(db_path)
        await storage.connect()
        pool = ScraperPool()
        bot = GepVintageBot(settings, storage, pool)
        await bot.start(settings.discord_token)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
