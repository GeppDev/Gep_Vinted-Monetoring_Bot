from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional

import discord
from discord.ext import commands

from gepvintage.fast_assistant import fast_open_decision
from gepvintage.scraper_pool import ScraperPool
from gepvintage.storage import Storage, Watch
from gepvintage.vinted_util import (
    ListingLinkView,
    build_listing_embed,
    parse_vinted_catalog_url,
    passes_filters,
    url_pairs_to_api_params,
)

_log = logging.getLogger(__name__)


class PollService:
    def __init__(
        self,
        bot: commands.Bot,
        storage: Storage,
        pool: ScraperPool,
        default_interval: int,
    ) -> None:
        self.bot = bot
        self.storage = storage
        self.pool = pool
        self.default_interval = default_interval
        self._task: Optional[asyncio.Task[None]] = None
        self._fail_streak: dict[str, int] = {}
        self._next_due: dict[str, float] = {}

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="vinted-poll")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def _interval_sec(self, w: Watch) -> float:
        base = w.poll_interval_sec or self.default_interval
        base = max(8, min(int(base), 120))
        return float(base) + random.uniform(0, 3.5)

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                now = time.monotonic()
                watches = await self.storage.list_enabled_watches()
                active_ids = {w.id for w in watches}
                for wid in list(self._next_due.keys()):
                    if wid not in active_ids:
                        self._next_due.pop(wid, None)
                progressed = False
                for w in watches:
                    due = self._next_due.get(w.id, 0.0)
                    if now < due:
                        continue
                    await self._tick_watch(w)
                    self._next_due[w.id] = time.monotonic() + self._interval_sec(w)
                    progressed = True
                    await asyncio.sleep(random.uniform(0.35, 0.9))
                if not watches:
                    await asyncio.sleep(float(self.default_interval))
                elif not progressed:
                    nxt = min(self._next_due.get(w.id, now) for w in watches)
                    await asyncio.sleep(max(0.25, min(nxt - time.monotonic(), 5.0)))
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("Poll-Runde fehlgeschlagen")
                await asyncio.sleep(5.0)

    async def _tick_watch(self, w: Watch) -> None:
        try:
            base, pairs = parse_vinted_catalog_url(w.vinted_url)
        except ValueError as e:
            _log.warning("Watch %s: %s", w.id, e)
            return

        params = url_pairs_to_api_params(pairs, page=1, per_page=40)
        items = await self.pool.search(base, params)
        if items is None:
            streak = self._fail_streak.get(w.id, 0) + 1
            self._fail_streak[w.id] = streak
            await asyncio.sleep(min(2**min(streak, 5), 60))
            return
        self._fail_streak[w.id] = 0

        ids = [str(it.id) for it in items if it.id is not None]
        if not ids:
            return

        if not w.seeded:
            await self.storage.mark_seen(w.id, ids)
            await self.storage.set_seeded(w.id, True)
            _log.info("Watch %s: initialer Snapshot (%d Items)", w.id, len(ids))
            return

        seen = await self.storage.seen_many(w.id, ids)
        new_ids = [i for i in ids if i not in seen]
        if not new_ids:
            return

        channel = self.bot.get_channel(w.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(w.channel_id)
            except Exception:
                _log.warning("Kanal %s für Watch %s nicht erreichbar", w.channel_id, w.id)
                await self.storage.mark_seen(w.id, new_ids)
                return

        if not isinstance(channel, discord.abc.Messageable):
            await self.storage.mark_seen(w.id, new_ids)
            return

        fcfg = await self.storage.get_fast_assistant(w.guild_id)
        to_mark: list[str] = []
        for it in items:
            if it.id is None:
                continue
            sid = str(it.id)
            if sid not in new_ids:
                continue
            if not passes_filters(w, it):
                to_mark.append(sid)
                continue
            # Deduplicate across overlapping watches in the same guild (atomic).
            # This prevents races when multiple watches hit the same listing at once.
            reserved = await self.storage.reserve_sent_guild(w.guild_id, sid)
            if not reserved:
                to_mark.append(sid)
                continue
            try:
                do_fast, agg = fast_open_decision(fcfg, it)
                hl = "best" if agg else ("priority" if do_fast else None)
                raw = it.json_data if isinstance(it.json_data, dict) else {}
                cu = raw.get("user") if isinstance(raw.get("user"), dict) else None
                api_u = None
                if cu and cu.get("id") is not None:
                    api_u = await self.pool.fetch_user(base, int(cu["id"]))
                emb = build_listing_embed(
                    w, it, base, highlight=hl, catalog_user=cu, seller_api=api_u
                )
                link = (emb.url or "").strip()
                if do_fast and link.startswith("http"):
                    await self.bot.fast.enqueue(link, guild_cfg=fcfg, aggressive=agg)
                prof = None
                if isinstance(api_u, dict) and api_u.get("profile_url"):
                    prof = str(api_u["profile_url"])
                elif isinstance(cu, dict) and cu.get("profile_url"):
                    prof = str(cu["profile_url"])
                if link.startswith("http"):
                    await channel.send(
                        embed=emb,
                        view=ListingLinkView(link, profile_url=prof),
                    )
                else:
                    await channel.send(embed=emb)
                await self.storage.increment_alerts_sent(w.id)
                to_mark.append(sid)
                _log.info("Neu: %s | %s | watch=%s", sid, it.title, w.id)
            except discord.HTTPException:
                _log.exception("Discord senden fehlgeschlagen watch=%s", w.id)
                break

        if to_mark:
            await self.storage.mark_seen(w.id, to_mark)
