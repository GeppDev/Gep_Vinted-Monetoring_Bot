from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from vinted_scraper import AsyncVintedScraper

_log = logging.getLogger(__name__)

_USER_CACHE_TTL_SEC = 300.0


class ScraperPool:
    """Ein AsyncVintedScraper pro Vinted-Host (Cookie-Session)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._scrapers: dict[str, AsyncVintedScraper] = {}
        self._user_cache: dict[tuple[str, int], tuple[float, Optional[dict]]] = {}

    async def get(self, base_url: str) -> AsyncVintedScraper:
        key = base_url.rstrip("/")
        async with self._lock:
            if key not in self._scrapers:
                _log.info("Vinted-Session wird aufgebaut: %s", key)
                self._scrapers[key] = await AsyncVintedScraper.create(key)
            return self._scrapers[key]

    async def _reset_scraper(self, base_url: str) -> None:
        key = base_url.rstrip("/")
        async with self._lock:
            scraper = self._scrapers.pop(key, None)
        if scraper is None:
            return
        client = getattr(scraper, "_client", None)
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                _log.debug("HTTP-Client beim Session-Reset nicht sauber geschlossen", exc_info=True)

    async def search(
        self, base_url: str, params: dict[str, Any]
    ) -> Optional[list]:
        try:
            scraper = await self.get(base_url)
            return await scraper.search(params)
        except RuntimeError as e:
            msg = str(e).lower()
            if "session cookie" in msg or "cannot fetch cookie" in msg:
                _log.warning(
                    "Session-Cookie-Fehler, baue Vinted-Session neu auf (%s)",
                    base_url,
                )
                await self._reset_scraper(base_url)
                try:
                    scraper = await self.get(base_url)
                    return await scraper.search(params)
                except Exception:
                    _log.exception(
                        "Vinted-Suche nach Session-Reset fehlgeschlagen (%s)",
                        base_url,
                    )
                    return None
            _log.exception("Vinted-Suche fehlgeschlagen (%s)", base_url)
            return None
        except Exception:
            _log.exception("Vinted-Suche fehlgeschlagen (%s)", base_url)
            return None

    async def fetch_user(self, base_url: str, user_id: int) -> Optional[dict]:
        key = (base_url.rstrip("/"), int(user_id))
        now = time.monotonic()
        hit = self._user_cache.get(key)
        if hit and now - hit[0] < _USER_CACHE_TTL_SEC:
            return hit[1]
        try:
            scraper = await self.get(base_url)
            resp = await scraper.curl(f"/api/v2/users/{int(user_id)}")
            raw = getattr(resp, "json_data", None)
            if raw is None:
                raw = resp if isinstance(resp, dict) else {}
            user = raw.get("user") if isinstance(raw, dict) else None
            if isinstance(user, dict):
                self._user_cache[key] = (now, user)
                return user
        except Exception:
            _log.debug("Vinted Nutzerprofil %s", user_id, exc_info=True)
        return None

    async def close_all(self) -> None:
        async with self._lock:
            for scraper in self._scrapers.values():
                client = getattr(scraper, "_client", None)
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:
                        _log.exception("Fehler beim Schließen des HTTP-Clients")
            self._scrapers.clear()
            self._user_cache.clear()
