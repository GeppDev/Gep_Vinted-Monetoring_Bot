from __future__ import annotations

import logging
from typing import Optional

import discord
from discord.ext import commands

from gepvintage.config import Settings
from gepvintage.fast_assistant import FastAssistantService
from gepvintage.poll_service import PollService
from gepvintage.scraper_pool import ScraperPool
from gepvintage.storage import Storage

_log = logging.getLogger(__name__)


class GepVintageBot(commands.Bot):
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        pool: ScraperPool,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            command_prefix=settings.command_prefix,
            intents=intents,
            help_command=None,
        )
        self.settings = settings
        self.storage = storage
        self.pool = pool
        self.fast = FastAssistantService(settings)
        self.poll: Optional[PollService] = None

    async def setup_hook(self) -> None:
        from gepvintage.cog_fastbuy import FastbuyCog
        from gepvintage.cog_privat import PrivatCog
        from gepvintage.cog_uebersicht import UebersichtCog
        from gepvintage.cog_vinted import VintedCog

        self.poll = PollService(
            self,
            self.storage,
            self.pool,
            self.settings.default_poll_interval_sec,
        )
        await self.add_cog(VintedCog(self))
        await self.add_cog(FastbuyCog(self))
        await self.add_cog(PrivatCog(self))
        await self.add_cog(UebersichtCog(self))
        synced = await self.tree.sync()
        _log.info("%d Slash-Befehle synchronisiert", len(synced))
        self.fast.start()
        if self.poll:
            self.poll.start()

    async def close(self) -> None:
        if self.poll:
            await self.poll.stop()
        await self.fast.stop()
        await self.pool.close_all()
        await self.storage.close()
        await super().close()
