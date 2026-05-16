from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse

import discord
from vinted_scraper.models import VintedItem

from gepvintage.storage import Watch

_log = logging.getLogger(__name__)

_CATALOG_PATH_RE = re.compile(r"/catalog/(\d+)", re.I)

EMBED_COLOR = 0x09B1BA
EMBED_PRIORITY = 0xFF6B35
EMBED_BEST = 0xFFB300


def is_vinted_host(host: str) -> bool:
    h = (host or "").lower().strip()
    if not h:
        return False
    if h.endswith(".vinted.net") or h == "vinted.net":
        return True
    return ".vinted." in f".{h}."


def parse_vinted_catalog_url(url: str) -> tuple[str, list[tuple[str, str]]]:
    u = urlparse(url.strip())
    if not is_vinted_host(u.netloc):
        raise ValueError("Link muss eine Vinted-Katalog-/Such-URL sein (z. B. vinted.de, vinted.fr).")
    scheme = u.scheme or "https"
    base = f"{scheme}://{u.netloc}"
    raw_pairs = [
        (k, v)
        for k, v in parse_qsl(u.query, keep_blank_values=True)
        if k not in ("page", "per_page", "order")
    ]
    pairs: list[tuple[str, str]] = []
    for k, v in raw_pairs:
        kk = (k or "").strip()
        # Normalize known category param aliases to what vinted_scraper expects.
        # The wrapper documents `catalog_ids` (without []), so we map everything there.
        if kk in ("catalog", "catalog[]", "catalog_id"):
            pairs.append(("catalog_ids", v))
        else:
            pairs.append((kk, v))
    m = _CATALOG_PATH_RE.search(u.path or "")
    if m and not any(k.lower().startswith("catalog") for k, _ in pairs):
        pairs.append(("catalog_ids", m.group(1)))
    pairs.append(("order", "newest_first"))
    return base, pairs


def watch_signature(url: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """
    Canonical signature for comparing searches independent of query order.
    """
    base, pairs = parse_vinted_catalog_url(url)
    filtered = [(k.strip(), str(v).strip()) for k, v in pairs if k != "order"]
    filtered.sort(key=lambda x: (x[0], x[1]))
    return base.rstrip("/").lower(), tuple(filtered)


def url_pairs_to_api_params(
    pairs: list[tuple[str, str]], *, page: int = 1, per_page: int = 40
) -> dict[str, Any]:
    acc: dict[str, list[str]] = {}
    for k, v in pairs:
        acc.setdefault(k, []).append(v)
    flat: dict[str, Any] = {}
    for k, vals in acc.items():
        flat[k] = vals[0] if len(vals) == 1 else vals
    flat["page"] = str(page)
    flat["per_page"] = str(per_page)
    return flat


def _title(item: VintedItem) -> str:
    return (item.title or "Ohne Titel")[:256]


def _brand_label(item: VintedItem) -> str:
    if item.brand and getattr(item.brand, "title", None):
        return str(item.brand.title)
    if item.brand_title:
        return str(item.brand_title)
    return "—"


def _size_label(item: VintedItem) -> str:
    if item.size_title:
        return str(item.size_title)
    return "—"


def _price_label(item: VintedItem) -> str:
    if item.price is None:
        return "—"
    cur = (item.currency or "").strip()
    if cur:
        return f"{item.price:g} {cur}"
    return f"{item.price:g}"


def _condition_label(item: VintedItem) -> str:
    data = item.json_data if isinstance(item.json_data, dict) else {}
    raw = (
        getattr(item, "status", None)
        or data.get("status")
        or data.get("item_condition")
        or data.get("status_id")
    )
    if raw is None:
        return "—"
    txt = str(raw).strip().replace("_", " ")
    if not txt:
        return "—"
    return txt[:64]


def _image_url(item: VintedItem) -> Optional[str]:
    if not item.photos:
        return None
    ph = item.photos[0]
    # Prefer smaller variants first: they render faster in Discord previews.
    for attr in ("url", "thumbnail_url", "small_url", "medium_url", "full_size_url"):
        val = getattr(ph, attr, None)
        if isinstance(val, str) and val.startswith("http"):
            return val
    return None


def _listing_url(base_url: str, item: VintedItem) -> str:
    if item.url and item.url.startswith("http"):
        return item.url
    if item.path:
        return f"{base_url.rstrip('/')}{item.path}"
    return base_url


def seller_embed_fields(
    catalog_user: Optional[dict], api_user: Optional[dict]
) -> list[tuple[str, str, bool]]:
    """(name, value, inline) für eine übersichtliche Zeile untereinander."""
    cat = catalog_user or {}
    api = api_user or {}
    login = api.get("login") or cat.get("login")
    profile = api.get("profile_url") or cat.get("profile_url")
    if login and profile:
        prof_val = f"[**{login}**]({profile})"
    elif login:
        prof_val = f"**{login}**"
    else:
        prof_val = "—"

    city = api.get("city")
    cc = api.get("country_code")
    loc_parts = [str(x) for x in (city, cc) if x]
    region_val = " · ".join(loc_parts) if loc_parts else "—"

    fc = api.get("feedback_count")
    rep = api.get("feedback_reputation")
    pos = api.get("positive_feedback_count")
    neg = api.get("negative_feedback_count")
    if fc is not None:
        try:
            pct = f"{float(rep) * 100:.0f}%" if rep is not None else "—"
        except (TypeError, ValueError):
            pct = "—"
        shop_val = f"**{fc}** · {pct}\n👍 {pos or 0} · 👎 {neg or 0}"
    else:
        shop_val = "—"

    return [
        ("👤 Profil", prof_val[:1024], True),
        ("📍 Region", region_val[:1024], True),
        ("⭐ Bewertungen", shop_val[:1024], True),
    ]


def extract_listing_time_utc(item: VintedItem) -> Optional[datetime]:
    """Best-effort listing time in UTC using multiple possible API fields."""
    data = item.json_data
    if not isinstance(data, dict):
        return None
    for k in (
        "created_at_ts",
        "created_at_timestamp",
        "published_at_ts",
        "published_at_timestamp",
        "updated_at_ts",
        "updated_at_timestamp",
    ):
        val = data.get(k)
        if val is None:
            continue
        try:
            return datetime.fromtimestamp(int(val), tz=timezone.utc)
        except (OSError, ValueError, TypeError):
            pass
    for k in ("created_at", "published_at", "updated_at"):
        raw = data.get(k)
        if not isinstance(raw, str):
            continue
        s = raw.strip()
        if not s:
            continue
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    photos = data.get("photos")
    if not photos:
        p = data.get("photo")
        photos = [p] if p else []
    if not photos or not isinstance(photos[0], dict):
        return None
    ph0 = photos[0]
    hr = ph0.get("high_resolution")
    if isinstance(hr, dict) and hr.get("timestamp") is not None:
        try:
            return datetime.fromtimestamp(int(hr["timestamp"]), tz=timezone.utc)
        except (OSError, ValueError, TypeError):
            pass
    url = str(ph0.get("url") or "")
    m = re.search(r"/(\d{9,10})\.jpeg", url)
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
        except (OSError, ValueError):
            pass
    return None


def _listing_time_field_value(item: VintedItem) -> str:
    dt = extract_listing_time_utc(item)
    if dt is None:
        return "—"
    return discord.utils.format_dt(dt, style="R")


class ListingLinkView(discord.ui.View):
    """Link-Buttons: Artikel + optional Verkäuferprofil."""

    def __init__(
        self,
        listing_url: str,
        *,
        label: str = "Artikel",
        profile_url: Optional[str] = None,
        profile_label: str = "Profil",
    ) -> None:
        super().__init__(timeout=None)
        if listing_url.startswith("http"):
            self.add_item(
                discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label=(label or "Artikel")[:80],
                    url=listing_url,
                )
            )
        if profile_url and profile_url.startswith("http"):
            self.add_item(
                discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label=(profile_label or "Profil")[:80],
                    url=profile_url,
                )
            )


def passes_filters(watch: Watch, item: VintedItem) -> bool:
    title = (item.title or "").lower()
    price = item.price

    if watch.min_price is not None:
        if price is None or price < watch.min_price:
            return False
    if watch.max_price is not None:
        if price is None or price > watch.max_price:
            return False

    for word in watch.keywords_include:
        if word.lower() not in title:
            return False
    for word in watch.keywords_exclude:
        if word.lower() in title:
            return False

    if watch.brands:
        b = _brand_label(item).lower()
        if not any(token.lower() in b or b == token.lower() for token in watch.brands):
            return False

    ex = watch.extra_json or {}
    pat = ex.get("title_regex")
    if pat:
        try:
            if not re.search(str(pat), item.title or "", re.I):
                return False
        except re.error:
            _log.warning("Ungültiges title_regex für Watch %s", watch.id)

    return True


def build_listing_embed(
    watch: Watch,
    item: VintedItem,
    base_url: str,
    *,
    highlight: Optional[str] = None,
    catalog_user: Optional[dict] = None,
    seller_api: Optional[dict] = None,
) -> discord.Embed:
    url = _listing_url(base_url, item)
    color = EMBED_COLOR
    tag = ""
    author_name: Optional[str] = None
    if highlight == "best":
        color = EMBED_BEST
        tag = " · BEST DEAL"
        author_name = "⭐ BEST DEAL"
    elif highlight == "priority":
        color = EMBED_PRIORITY
        tag = " · PRIORITY"
        author_name = "⚡ Priority"

    emb = discord.Embed(
        title=_title(item),
        url=url,
        color=color,
        description=None,
    )
    if author_name:
        emb.set_author(name=author_name)

    emb.add_field(
        name="💶 Preis",
        value=f"**{_price_label(item)}**",
        inline=True,
    )
    emb.add_field(
        name="🏷️ Marke",
        value=_brand_label(item)[:1024],
        inline=True,
    )
    emb.add_field(
        name="📏 Größe",
        value=_size_label(item)[:1024],
        inline=True,
    )
    emb.add_field(
        name="📦 Zustand",
        value=_condition_label(item),
        inline=True,
    )

    emb.add_field(
        name="🕐 Hochgeladen",
        value=_listing_time_field_value(item)[:1024],
        inline=False,
    )

    dt = extract_listing_time_utc(item)
    if dt is not None:
        emb.timestamp = dt

    img = _image_url(item)
    if img:
        emb.set_image(url=img)

    if catalog_user or seller_api:
        for name, val, inline in seller_embed_fields(catalog_user, seller_api):
            emb.add_field(name=name, value=val, inline=inline)

    iid = getattr(item, "id", None)
    label = watch.label or watch.id
    foot = f"GepVintage · Watch `{label}`"
    if iid is not None:
        foot += f" · Artikel-ID `{iid}`"
    foot += tag
    emb.set_footer(text=foot[:2048])
    return emb
