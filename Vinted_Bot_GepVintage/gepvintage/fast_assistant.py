from __future__ import annotations

import asyncio
import logging
import os
import sys
import webbrowser
from collections import deque
from typing import Optional

from vinted_scraper.models import VintedItem

from gepvintage.config import Settings
from gepvintage.storage import FastAssistantConfig
from gepvintage.vinted_util import _brand_label

_log = logging.getLogger(__name__)


def _play_alert(sound_path: Optional[str], *, repeat: int = 1) -> None:
    repeat = max(1, min(repeat, 3))
    path = (sound_path or "").strip()
    for _ in range(repeat):
        played = False
        if path and os.path.isfile(path):
            try:
                if sys.platform == "win32":
                    import winsound

                    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    played = True
                else:
                    import subprocess

                    if sys.platform == "darwin":
                        subprocess.Popen(
                            ["afplay", path],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        played = True
                    else:
                        for cmd in (["paplay", path], ["aplay", path]):
                            try:
                                subprocess.Popen(
                                    cmd,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                )
                                played = True
                                break
                            except OSError:
                                continue
            except Exception:
                _log.debug("Sound playback failed", exc_info=True)
        if not played and sys.platform == "win32":
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                print("\a", end="", flush=True)
        elif not played:
            print("\a", end="", flush=True)


def priority_matches(cfg: FastAssistantConfig, item: VintedItem) -> bool:
    checks: list[bool] = []
    if cfg.priority_max_price is not None:
        if item.price is None or item.price > cfg.priority_max_price:
            return False
        checks.append(True)
    if cfg.priority_brands:
        b = _brand_label(item).lower()
        if not any(t.lower() in b or b == t.lower() for t in cfg.priority_brands):
            return False
        checks.append(True)
    if cfg.priority_keywords:
        t = (item.title or "").lower()
        kws = [k.lower() for k in cfg.priority_keywords if k.strip()]
        if kws:
            if cfg.priority_keywords_all:
                if not all(k in t for k in kws):
                    return False
            else:
                if not any(k in t for k in kws):
                    return False
            checks.append(True)
    if not checks:
        return False
    return True


def best_deal_matches(cfg: FastAssistantConfig, item: VintedItem) -> bool:
    if cfg.best_deal_max_price is None or item.price is None:
        return False
    return item.price <= cfg.best_deal_max_price


def fast_open_decision(cfg: FastAssistantConfig, item: VintedItem) -> tuple[bool, bool]:
    """(open_browser, aggressive_sound/embed)."""
    pri = priority_matches(cfg, item)
    best = best_deal_matches(cfg, item)
    if pri:
        return True, best
    if cfg.best_deal_max_price is not None and best:
        return True, True
    return False, False


class FastAssistantService:
    """Lokaler Fast-Open + Sound (Bot-Prozess = Rechner des Nutzers)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._q: asyncio.PriorityQueue[
            tuple[int, int, str, bool, FastAssistantConfig]
        ] = asyncio.PriorityQueue()
        self._guild_sems: dict[int, asyncio.Semaphore] = {}
        self._guild_cap: dict[int, int] = {}
        self._seq = 0
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()
        self._opens_mono: deque[float] = deque()
        self._last_open_mono: float = 0.0

    def _guild_sem(self, guild_id: int, want_parallel: int) -> asyncio.Semaphore:
        cap = max(
            1,
            min(int(want_parallel), self._settings.fast_assistant_max_parallel_tabs),
        )
        if guild_id not in self._guild_sems or self._guild_cap.get(guild_id) != cap:
            self._guild_sems[guild_id] = asyncio.Semaphore(cap)
            self._guild_cap[guild_id] = cap
        return self._guild_sems[guild_id]

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._worker(), name="fast-assistant")

    async def stop(self) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def enqueue(
        self,
        url: str,
        *,
        guild_cfg: FastAssistantConfig,
        aggressive: bool,
    ) -> None:
        if not self._settings.fast_assistant_enabled:
            return
        if not guild_cfg.fast_open_enabled:
            return
        if not url.startswith("http"):
            return
        async with self._lock:
            self._seq += 1
            seq = self._seq
        pri = 0 if aggressive else 1
        await self._q.put((pri, seq, url, aggressive, guild_cfg))

    async def _rate_wait(self, cfg: FastAssistantConfig, aggressive: bool) -> None:
        max_per = max(
            1, min(cfg.max_opens_per_minute, self._settings.fast_assistant_max_per_minute)
        )
        min_gap = max(
            self._settings.fast_assistant_min_interval_sec, float(cfg.min_interval_sec)
        )
        if aggressive:
            min_gap = max(
                self._settings.fast_assistant_min_interval_sec * 0.5,
                min_gap * 0.55,
            )
        while True:
            now = asyncio.get_running_loop().time()
            while self._opens_mono and now - self._opens_mono[0] > 60.0:
                self._opens_mono.popleft()
            if len(self._opens_mono) >= max_per:
                wait = 60.0 - (now - self._opens_mono[0])
                await asyncio.sleep(max(0.05, wait))
                continue
            gap = now - self._last_open_mono
            if gap < min_gap:
                await asyncio.sleep(min_gap - gap)
            return

    async def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                _pri, _seq, url, aggressive, gcfg = await asyncio.wait_for(
                    self._q.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                await self._rate_wait(gcfg, aggressive)
                if gcfg.sound_enabled and self._settings.fast_assistant_sound_enabled:
                    rep = 2 if aggressive else 1
                    asyncio.create_task(
                        asyncio.to_thread(
                            _play_alert,
                            self._settings.fast_assistant_sound_path or None,
                            repeat=rep,
                        )
                    )
                gsem = self._guild_sem(gcfg.guild_id, gcfg.max_parallel_tabs)
                async with gsem:
                    await asyncio.to_thread(webbrowser.open, url, new=2)
                now = asyncio.get_running_loop().time()
                self._opens_mono.append(now)
                self._last_open_mono = now
                _log.info("Fast open: %s aggressive=%s", url[:80], aggressive)
            except asyncio.CancelledError:
                break
            except Exception:
                _log.exception("Fast assistant job failed")
            finally:
                try:
                    self._q.task_done()
                except ValueError:
                    pass
