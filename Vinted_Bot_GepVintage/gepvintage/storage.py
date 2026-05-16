from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id TEXT PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    label TEXT,
    vinted_url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    seeded INTEGER NOT NULL DEFAULT 0,
    max_price REAL,
    min_price REAL,
    keywords_include TEXT NOT NULL DEFAULT '[]',
    keywords_exclude TEXT NOT NULL DEFAULT '[]',
    brands TEXT NOT NULL DEFAULT '[]',
    extra_json TEXT NOT NULL DEFAULT '{}',
    poll_interval_sec INTEGER,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS seen (
    watch_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (watch_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_watches_guild ON watches(guild_id);
CREATE INDEX IF NOT EXISTS idx_seen_watch ON seen(watch_id);

-- Deduplicate across overlapping watches: a listing should only be posted once per guild.
CREATE TABLE IF NOT EXISTS sent_guild (
    guild_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (guild_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_sent_guild_ts ON sent_guild(guild_id, ts);

CREATE TABLE IF NOT EXISTS guild_fast_assistant (
    guild_id INTEGER PRIMARY KEY,
    fast_open_enabled INTEGER NOT NULL DEFAULT 0,
    sound_enabled INTEGER NOT NULL DEFAULT 0,
    max_opens_per_minute INTEGER NOT NULL DEFAULT 6,
    max_parallel_tabs INTEGER NOT NULL DEFAULT 2,
    min_interval_sec REAL NOT NULL DEFAULT 2.5,
    priority_max_price REAL,
    priority_brands TEXT NOT NULL DEFAULT '[]',
    priority_keywords TEXT NOT NULL DEFAULT '[]',
    priority_keywords_all INTEGER NOT NULL DEFAULT 0,
    best_deal_max_price REAL
);

"""


@dataclass
class FastAssistantConfig:
    guild_id: int
    fast_open_enabled: bool
    sound_enabled: bool
    max_opens_per_minute: int
    max_parallel_tabs: int
    min_interval_sec: float
    priority_max_price: Optional[float]
    priority_brands: list[str]
    priority_keywords: list[str]
    priority_keywords_all: bool
    best_deal_max_price: Optional[float]


@dataclass
class Watch:
    id: str
    guild_id: int
    channel_id: int
    label: Optional[str]
    vinted_url: str
    enabled: bool
    seeded: bool
    max_price: Optional[float]
    min_price: Optional[float]
    keywords_include: list[str]
    keywords_exclude: list[str]
    brands: list[str]
    extra_json: dict[str, Any]
    poll_interval_sec: Optional[int]
    created_at: float
    owner_user_id: Optional[int]
    alerts_sent: int


def _loads_list(raw: str) -> list[str]:
    try:
        v = json.loads(raw or "[]")
        return [str(x).strip() for x in v if str(x).strip()]
    except json.JSONDecodeError:
        return []


def _loads_dict(raw: str) -> dict[str, Any]:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}


def _default_fast_cfg(guild_id: int) -> FastAssistantConfig:
    return FastAssistantConfig(
        guild_id=guild_id,
        fast_open_enabled=False,
        sound_enabled=False,
        max_opens_per_minute=6,
        max_parallel_tabs=2,
        min_interval_sec=2.5,
        priority_max_price=None,
        priority_brands=[],
        priority_keywords=[],
        priority_keywords_all=False,
        best_deal_max_price=None,
    )


def _row_to_fast(row: aiosqlite.Row) -> FastAssistantConfig:
    return FastAssistantConfig(
        guild_id=int(row["guild_id"]),
        fast_open_enabled=bool(row["fast_open_enabled"]),
        sound_enabled=bool(row["sound_enabled"]),
        max_opens_per_minute=int(row["max_opens_per_minute"]),
        max_parallel_tabs=int(row["max_parallel_tabs"]),
        min_interval_sec=float(row["min_interval_sec"]),
        priority_max_price=row["priority_max_price"],
        priority_brands=_loads_list(row["priority_brands"]),
        priority_keywords=_loads_list(row["priority_keywords"]),
        priority_keywords_all=bool(row["priority_keywords_all"]),
        best_deal_max_price=row["best_deal_max_price"],
    )


def _row_to_watch(row: aiosqlite.Row) -> Watch:
    try:
        oid = row["owner_user_id"]
    except (KeyError, IndexError):
        oid = None
    try:
        asent = int(row["alerts_sent"] or 0)
    except (KeyError, IndexError):
        asent = 0
    return Watch(
        id=row["id"],
        guild_id=int(row["guild_id"]),
        channel_id=int(row["channel_id"]),
        label=row["label"],
        vinted_url=row["vinted_url"],
        enabled=bool(row["enabled"]),
        seeded=bool(row["seeded"]),
        max_price=row["max_price"],
        min_price=row["min_price"],
        keywords_include=_loads_list(row["keywords_include"]),
        keywords_exclude=_loads_list(row["keywords_exclude"]),
        brands=_loads_list(row["brands"]),
        extra_json=_loads_dict(row["extra_json"]),
        poll_interval_sec=row["poll_interval_sec"],
        created_at=float(row["created_at"]),
        owner_user_id=int(oid) if oid is not None else None,
        alerts_sent=asent,
    )


class Storage:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def connect(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        await self._migrate_watches_owner()
        await self._migrate_sent_guild()

    async def _migrate_sent_guild(self) -> None:
        # Ensure new table exists on older DBs even if SCHEMA was created previously.
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_guild (
                guild_id INTEGER NOT NULL,
                item_id TEXT NOT NULL,
                ts REAL NOT NULL,
                PRIMARY KEY (guild_id, item_id)
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sent_guild_ts ON sent_guild(guild_id, ts)"
        )
        await self._db.commit()

    async def _migrate_watches_owner(self) -> None:
        cur = await self._db.execute("PRAGMA table_info(watches)")
        cols = {str(r[1]) for r in await cur.fetchall()}
        if "owner_user_id" not in cols:
            await self._db.execute(
                "ALTER TABLE watches ADD COLUMN owner_user_id INTEGER"
            )
            await self._db.commit()
        if "alerts_sent" not in cols:
            await self._db.execute(
                "ALTER TABLE watches ADD COLUMN alerts_sent INTEGER NOT NULL DEFAULT 0"
            )
            await self._db.commit()
        await self._db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_watches_private_owner
            ON watches(guild_id, owner_user_id)
            WHERE owner_user_id IS NOT NULL
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        await self._db.close()

    async def get_fast_assistant(self, guild_id: int) -> FastAssistantConfig:
        cur = await self._db.execute(
            "SELECT * FROM guild_fast_assistant WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        if row:
            return _row_to_fast(row)
        return _default_fast_cfg(guild_id)

    async def upsert_fast_assistant(
        self,
        guild_id: int,
        *,
        fast_open_enabled: Optional[bool] = None,
        sound_enabled: Optional[bool] = None,
        max_opens_per_minute: Optional[int] = None,
        max_parallel_tabs: Optional[int] = None,
        min_interval_sec: Optional[float] = None,
        priority_max_price: Optional[float] = None,
        priority_brands: Optional[Sequence[str]] = None,
        priority_keywords: Optional[Sequence[str]] = None,
        priority_keywords_all: Optional[bool] = None,
        best_deal_max_price: Optional[float] = None,
        clear_priority_max_price: bool = False,
        clear_best_deal_max_price: bool = False,
    ) -> FastAssistantConfig:
        cur = await self._db.execute(
            "SELECT * FROM guild_fast_assistant WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        base = _row_to_fast(row) if row else _default_fast_cfg(guild_id)
        mp = None if clear_priority_max_price else (
            priority_max_price if priority_max_price is not None else base.priority_max_price
        )
        bd = None if clear_best_deal_max_price else (
            best_deal_max_price if best_deal_max_price is not None else base.best_deal_max_price
        )
        cfg = FastAssistantConfig(
            guild_id=guild_id,
            fast_open_enabled=(
                fast_open_enabled if fast_open_enabled is not None else base.fast_open_enabled
            ),
            sound_enabled=sound_enabled if sound_enabled is not None else base.sound_enabled,
            max_opens_per_minute=(
                max_opens_per_minute
                if max_opens_per_minute is not None
                else base.max_opens_per_minute
            ),
            max_parallel_tabs=(
                max_parallel_tabs if max_parallel_tabs is not None else base.max_parallel_tabs
            ),
            min_interval_sec=(
                min_interval_sec if min_interval_sec is not None else base.min_interval_sec
            ),
            priority_max_price=mp,
            priority_brands=list(priority_brands)
            if priority_brands is not None
            else base.priority_brands,
            priority_keywords=list(priority_keywords)
            if priority_keywords is not None
            else base.priority_keywords,
            priority_keywords_all=(
                priority_keywords_all
                if priority_keywords_all is not None
                else base.priority_keywords_all
            ),
            best_deal_max_price=bd,
        )
        await self._db.execute(
            """
            INSERT INTO guild_fast_assistant (
              guild_id, fast_open_enabled, sound_enabled, max_opens_per_minute,
              max_parallel_tabs, min_interval_sec, priority_max_price, priority_brands,
              priority_keywords, priority_keywords_all, best_deal_max_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
              fast_open_enabled = excluded.fast_open_enabled,
              sound_enabled = excluded.sound_enabled,
              max_opens_per_minute = excluded.max_opens_per_minute,
              max_parallel_tabs = excluded.max_parallel_tabs,
              min_interval_sec = excluded.min_interval_sec,
              priority_max_price = excluded.priority_max_price,
              priority_brands = excluded.priority_brands,
              priority_keywords = excluded.priority_keywords,
              priority_keywords_all = excluded.priority_keywords_all,
              best_deal_max_price = excluded.best_deal_max_price
            """,
            (
                guild_id,
                1 if cfg.fast_open_enabled else 0,
                1 if cfg.sound_enabled else 0,
                cfg.max_opens_per_minute,
                cfg.max_parallel_tabs,
                cfg.min_interval_sec,
                cfg.priority_max_price,
                json.dumps(cfg.priority_brands),
                json.dumps(cfg.priority_keywords),
                1 if cfg.priority_keywords_all else 0,
                cfg.best_deal_max_price,
            ),
        )
        await self._db.commit()
        return cfg

    async def add_watch(
        self,
        guild_id: int,
        channel_id: int,
        vinted_url: str,
        label: Optional[str] = None,
        poll_interval_sec: Optional[int] = None,
        owner_user_id: Optional[int] = None,
        *,
        enabled: bool = True,
    ) -> Watch:
        wid = str(uuid.uuid4())[:12]
        now = time.time()
        await self._db.execute(
            """
            INSERT INTO watches (
              id, guild_id, channel_id, label, vinted_url, enabled, seeded,
              max_price, min_price, keywords_include, keywords_exclude, brands,
              extra_json, poll_interval_sec, created_at, owner_user_id, alerts_sent
            ) VALUES (?, ?, ?, ?, ?, ?, 0, NULL, NULL, '[]', '[]', '[]', '{}', ?, ?, ?, 0)
            """,
            (
                wid,
                guild_id,
                channel_id,
                label,
                vinted_url,
                1 if enabled else 0,
                poll_interval_sec,
                now,
                owner_user_id,
            ),
        )
        await self._db.commit()
        out = await self.get_watch(wid)
        assert out is not None
        return out

    async def get_private_watch(
        self, guild_id: int, owner_user_id: int
    ) -> Optional[Watch]:
        cur = await self._db.execute(
            """
            SELECT * FROM watches
            WHERE guild_id = ? AND owner_user_id = ?
            LIMIT 1
            """,
            (guild_id, owner_user_id),
        )
        row = await cur.fetchone()
        return _row_to_watch(row) if row else None

    async def upsert_private_watch(
        self,
        guild_id: int,
        owner_user_id: int,
        channel_id: int,
        vinted_url: str,
        *,
        label: Optional[str] = None,
    ) -> Watch:
        ex = await self.get_private_watch(guild_id, owner_user_id)
        if ex:
            await self._db.execute(
                """
                UPDATE watches SET
                  channel_id = ?, vinted_url = ?, seeded = 0,
                  label = COALESCE(?, label)
                WHERE id = ? AND guild_id = ?
                """,
                (channel_id, vinted_url, label, ex.id, guild_id),
            )
            await self._db.execute("DELETE FROM seen WHERE watch_id = ?", (ex.id,))
            await self._db.commit()
            out = await self.get_watch(ex.id)
            assert out is not None
            return out
        return await self.add_watch(
            guild_id,
            channel_id,
            vinted_url,
            label=label or "Privat",
            poll_interval_sec=None,
            owner_user_id=owner_user_id,
            enabled=False,
        )

    async def delete_private_watch(
        self, guild_id: int, owner_user_id: int
    ) -> Optional[str]:
        w = await self.get_private_watch(guild_id, owner_user_id)
        if not w:
            return None
        await self._db.execute("DELETE FROM seen WHERE watch_id = ?", (w.id,))
        await self._db.execute(
            "DELETE FROM watches WHERE id = ? AND guild_id = ?",
            (w.id, guild_id),
        )
        await self._db.commit()
        return w.id

    async def delete_watch(self, watch_id: str, guild_id: int) -> bool:
        cur = await self._db.execute(
            "DELETE FROM watches WHERE id = ? AND guild_id = ?",
            (watch_id, guild_id),
        )
        ok = cur.rowcount > 0
        if ok:
            await self._db.execute("DELETE FROM seen WHERE watch_id = ?", (watch_id,))
        await self._db.commit()
        return ok

    async def get_watch(self, watch_id: str) -> Optional[Watch]:
        cur = await self._db.execute("SELECT * FROM watches WHERE id = ?", (watch_id,))
        row = await cur.fetchone()
        return _row_to_watch(row) if row else None

    async def increment_alerts_sent(self, watch_id: str) -> None:
        await self._db.execute(
            "UPDATE watches SET alerts_sent = COALESCE(alerts_sent, 0) + 1 WHERE id = ?",
            (watch_id,),
        )
        await self._db.commit()

    async def count_seen_for_watch(self, watch_id: str) -> int:
        cur = await self._db.execute(
            "SELECT COUNT(*) AS n FROM seen WHERE watch_id = ?",
            (watch_id,),
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def guild_stats(self, guild_id: int) -> dict[str, Any]:
        cur = await self._db.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN owner_user_id IS NULL THEN 1 ELSE 0 END) AS server_w,
              SUM(CASE WHEN owner_user_id IS NOT NULL THEN 1 ELSE 0 END) AS priv_w,
              SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled_n,
              COALESCE(SUM(alerts_sent), 0) AS alerts
            FROM watches WHERE guild_id = ?
            """,
            (guild_id,),
        )
        row = await cur.fetchone()
        if not row:
            return {
                "total": 0,
                "server_watches": 0,
                "private_watches": 0,
                "enabled": 0,
                "alerts_sent": 0,
            }
        return {
            "total": int(row["total"] or 0),
            "server_watches": int(row["server_w"] or 0),
            "private_watches": int(row["priv_w"] or 0),
            "enabled": int(row["enabled_n"] or 0),
            "alerts_sent": int(row["alerts"] or 0),
        }

    async def list_watches(self, guild_id: int) -> list[Watch]:
        cur = await self._db.execute(
            "SELECT * FROM watches WHERE guild_id = ? ORDER BY created_at ASC",
            (guild_id,),
        )
        rows = await cur.fetchall()
        return [_row_to_watch(r) for r in rows]

    async def list_enabled_watches(self) -> list[Watch]:
        cur = await self._db.execute(
            "SELECT * FROM watches WHERE enabled = 1 ORDER BY guild_id, created_at ASC"
        )
        rows = await cur.fetchall()
        return [_row_to_watch(r) for r in rows]

    async def set_enabled(self, watch_id: str, guild_id: int, enabled: bool) -> bool:
        cur = await self._db.execute(
            "UPDATE watches SET enabled = ? WHERE id = ? AND guild_id = ?",
            (1 if enabled else 0, watch_id, guild_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def set_enabled_all(self, guild_id: int, enabled: bool) -> int:
        cur = await self._db.execute(
            "UPDATE watches SET enabled = ? WHERE guild_id = ?",
            (1 if enabled else 0, guild_id),
        )
        await self._db.commit()
        return cur.rowcount

    async def set_channel(self, watch_id: str, guild_id: int, channel_id: int) -> bool:
        cur = await self._db.execute(
            "UPDATE watches SET channel_id = ? WHERE id = ? AND guild_id = ?",
            (channel_id, watch_id, guild_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def update_filters(
        self,
        watch_id: str,
        guild_id: int,
        *,
        max_price: Optional[float] = None,
        min_price: Optional[float] = None,
        keywords_include: Optional[Sequence[str]] = None,
        keywords_exclude: Optional[Sequence[str]] = None,
        brands: Optional[Sequence[str]] = None,
        extra_json: Optional[dict[str, Any]] = None,
        clear_max_price: bool = False,
        clear_min_price: bool = False,
    ) -> bool:
        w = await self.get_watch(watch_id)
        if not w or w.guild_id != guild_id:
            return False
        max_p = None if clear_max_price else (max_price if max_price is not None else w.max_price)
        min_p = None if clear_min_price else (min_price if min_price is not None else w.min_price)
        inc = list(keywords_include) if keywords_include is not None else w.keywords_include
        exc = list(keywords_exclude) if keywords_exclude is not None else w.keywords_exclude
        br = list(brands) if brands is not None else w.brands
        extra = dict(extra_json) if extra_json is not None else w.extra_json
        await self._db.execute(
            """
            UPDATE watches SET
              max_price = ?, min_price = ?,
              keywords_include = ?, keywords_exclude = ?, brands = ?, extra_json = ?
            WHERE id = ? AND guild_id = ?
            """,
            (
                max_p,
                min_p,
                json.dumps(inc),
                json.dumps(exc),
                json.dumps(br),
                json.dumps(extra),
                watch_id,
                guild_id,
            ),
        )
        await self._db.commit()
        return True

    async def set_poll_interval(
        self, watch_id: str, guild_id: int, seconds: Optional[int]
    ) -> bool:
        cur = await self._db.execute(
            "UPDATE watches SET poll_interval_sec = ? WHERE id = ? AND guild_id = ?",
            (seconds, watch_id, guild_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def set_seeded(self, watch_id: str, seeded: bool) -> None:
        await self._db.execute(
            "UPDATE watches SET seeded = ? WHERE id = ?",
            (1 if seeded else 0, watch_id),
        )
        await self._db.commit()

    async def is_seen(self, watch_id: str, item_id: str) -> bool:
        cur = await self._db.execute(
            "SELECT 1 FROM seen WHERE watch_id = ? AND item_id = ? LIMIT 1",
            (watch_id, str(item_id)),
        )
        return await cur.fetchone() is not None

    async def seen_many(self, watch_id: str, item_ids: Sequence[str]) -> set[str]:
        """Return subset of item_ids that are already seen for watch_id."""
        ids = [str(i) for i in item_ids if i is not None]
        if not ids:
            return set()
        out: set[str] = set()
        # SQLite has a parameter limit (commonly 999). Chunk to be safe.
        chunk_size = 800
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            ph = ",".join(["?"] * len(chunk))
            cur = await self._db.execute(
                f"SELECT item_id FROM seen WHERE watch_id = ? AND item_id IN ({ph})",
                (watch_id, *chunk),
            )
            rows = await cur.fetchall()
            out.update(str(r["item_id"]) for r in rows)
        return out

    async def mark_seen(self, watch_id: str, item_ids: Sequence[str]) -> None:
        now = time.time()
        await self._db.executemany(
            "INSERT OR IGNORE INTO seen (watch_id, item_id, ts) VALUES (?, ?, ?)",
            [(watch_id, str(iid), now) for iid in item_ids],
        )
        await self._db.commit()
        await self._prune_seen(watch_id)

    async def is_sent_guild(self, guild_id: int, item_id: str) -> bool:
        cur = await self._db.execute(
            "SELECT 1 FROM sent_guild WHERE guild_id = ? AND item_id = ? LIMIT 1",
            (int(guild_id), str(item_id)),
        )
        return await cur.fetchone() is not None

    async def sent_guild_many(self, guild_id: int, item_ids: Sequence[str]) -> set[str]:
        """Return subset of item_ids already sent for this guild."""
        ids = [str(i) for i in item_ids if i is not None]
        if not ids:
            return set()
        out: set[str] = set()
        chunk_size = 800
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            ph = ",".join(["?"] * len(chunk))
            cur = await self._db.execute(
                f"SELECT item_id FROM sent_guild WHERE guild_id = ? AND item_id IN ({ph})",
                (int(guild_id), *chunk),
            )
            rows = await cur.fetchall()
            out.update(str(r["item_id"]) for r in rows)
        return out

    async def mark_sent_guild(self, guild_id: int, item_id: str) -> None:
        now = time.time()
        await self._db.execute(
            "INSERT OR IGNORE INTO sent_guild (guild_id, item_id, ts) VALUES (?, ?, ?)",
            (int(guild_id), str(item_id), now),
        )
        await self._db.commit()
        await self._prune_sent_guild(guild_id)

    async def reserve_sent_guild(self, guild_id: int, item_id: str) -> bool:
        """
        Atomically reserve a listing for sending (dedupe across concurrent watches).

        Returns True only for the first caller that inserts (guild_id, item_id).
        """
        now = time.time()
        cur = await self._db.execute(
            "INSERT OR IGNORE INTO sent_guild (guild_id, item_id, ts) VALUES (?, ?, ?)",
            (int(guild_id), str(item_id), now),
        )
        await self._db.commit()
        inserted = getattr(cur, "rowcount", 0) == 1
        if inserted:
            await self._prune_sent_guild(guild_id)
        return inserted

    async def _prune_sent_guild(self, guild_id: int, keep: int = 6000) -> None:
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM sent_guild WHERE guild_id = ?", (int(guild_id),)
        )
        row = await cur.fetchone()
        n = int(row[0]) if row else 0
        if n <= keep:
            return
        to_drop = n - keep
        await self._db.execute(
            """
            DELETE FROM sent_guild
            WHERE guild_id = ? AND item_id IN (
              SELECT item_id FROM sent_guild WHERE guild_id = ? ORDER BY ts ASC LIMIT ?
            )
            """,
            (int(guild_id), int(guild_id), int(to_drop)),
        )
        await self._db.commit()

    async def _prune_seen(self, watch_id: str, keep: int = 4000) -> None:
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM seen WHERE watch_id = ?", (watch_id,)
        )
        row = await cur.fetchone()
        n = int(row[0]) if row else 0
        if n <= keep:
            return
        to_drop = n - keep
        await self._db.execute(
            """
            DELETE FROM seen WHERE watch_id = ? AND item_id IN (
              SELECT item_id FROM seen WHERE watch_id = ? ORDER BY ts ASC LIMIT ?
            )
            """,
            (watch_id, watch_id, to_drop),
        )
        await self._db.commit()
