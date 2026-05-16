from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from gepvintage.bot import GepVintageBot
from gepvintage.vinted_util import parse_vinted_catalog_url, watch_signature


def _split_csv(s: Optional[str]) -> Optional[list[str]]:
    if s is None or not str(s).strip():
        return None
    return [p.strip() for p in str(s).split(",") if p.strip()]


def _chunk_text(lines: list[str], *, max_len: int = 1900) -> list[str]:
    chunks: list[str] = []
    cur = ""
    for line in lines:
        piece = line if not cur else f"\n\n{line}"
        if len(cur) + len(piece) <= max_len:
            cur += piece
            continue
        if cur:
            chunks.append(cur)
            cur = ""
        if len(line) <= max_len:
            cur = line
        else:
            # Hard split very long single lines (should be rare).
            for i in range(0, len(line), max_len):
                part = line[i : i + max_len]
                if i == 0:
                    cur = part
                else:
                    chunks.append(cur)
                    cur = part
    if cur:
        chunks.append(cur)
    return chunks or ["Keine Watches."]


class VintedCog(commands.Cog, name="Vinted"):
    """Slash- und Prefix-Befehle für den Vinted-Monitor."""

    def __init__(self, bot: GepVintageBot) -> None:
        self.bot = bot

    @commands.hybrid_group(name="vinted", invoke_without_command=True)
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def vinted(self, ctx: commands.Context) -> None:
        await ctx.reply(
            "Befehle: `add`, `remove`, `list`, `filter`, `start`, `stop`, `status`, `channel`, `interval`. "
            "Nutze Slash `/vinted` oder `{0}vinted`.".format(ctx.prefix or self.bot.settings.command_prefix),
            ephemeral=ctx.interaction is not None,
        )

    @vinted.command(name="add", description="Vinted-Such-URL überwachen")
    @app_commands.describe(
        url="Vollständige Vinted-Katalog-URL (mit Filtern in der Adresszeile)",
        channel="Zielkanal (Standard: aktueller Kanal)",
        label="Optionaler Anzeigename",
        interval="Abfrage-Intervall in Sekunden (8–120)",
    )
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def vinted_add(
        self,
        ctx: commands.Context,
        url: str,
        channel: Optional[discord.TextChannel] = None,
        label: Optional[str] = None,
        interval: Optional[app_commands.Range[int, 8, 120]] = None,
    ) -> None:
        assert ctx.guild is not None
        eph = ctx.interaction is not None
        # Slash/Hybrid interactions must be acknowledged quickly; otherwise Discord returns
        # "Unknown interaction" when we try to respond after I/O (DB / network).
        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer(ephemeral=eph)
        ch = channel or ctx.channel
        if not isinstance(ch, discord.TextChannel):
            if ctx.interaction is not None and ctx.interaction.response.is_done():
                await ctx.interaction.followup.send("Bitte einen Textkanal angeben.", ephemeral=True)
            else:
                await ctx.reply("Bitte einen Textkanal angeben.", ephemeral=True)
            return
        try:
            parse_vinted_catalog_url(url)
        except ValueError as e:
            if ctx.interaction is not None and ctx.interaction.response.is_done():
                await ctx.interaction.followup.send(str(e), ephemeral=True)
            else:
                await ctx.reply(str(e), ephemeral=True)
            return
        # Prevent duplicate watches for the same effective search (query order independent).
        sig = watch_signature(url)
        existing = await self.bot.storage.list_watches(ctx.guild.id)
        for ex in existing:
            try:
                if watch_signature(ex.vinted_url) == sig:
                    msg = (
                        f"Diese Suche existiert schon als Watch **`{ex.id}`** "
                        f"(Kanal <#{ex.channel_id}>)."
                    )
                    if ctx.interaction is not None and ctx.interaction.response.is_done():
                        await ctx.interaction.followup.send(msg, ephemeral=True)
                    else:
                        await ctx.reply(msg, ephemeral=True)
                    return
            except Exception:
                # Ignore malformed historical URLs and continue checking.
                continue
        w = await self.bot.storage.add_watch(
            ctx.guild.id,
            ch.id,
            url.strip(),
            label=label,
            poll_interval_sec=interval,
        )
        msg = (
            f"Watch **`{w.id}`** aktiv → {ch.mention}. "
            f"Erster Lauf merkt bestehende Treffer ohne Benachrichtigung."
        )
        if ctx.interaction is not None and ctx.interaction.response.is_done():
            await ctx.interaction.followup.send(msg, ephemeral=eph)
        else:
            await ctx.reply(msg, ephemeral=eph)

    @vinted.command(name="remove", description="Watch löschen")
    @app_commands.describe(watch_id="Kurz-ID aus `/vinted list`")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def vinted_remove(self, ctx: commands.Context, watch_id: str) -> None:
        assert ctx.guild is not None
        ok = await self.bot.storage.delete_watch(watch_id.strip(), ctx.guild.id)
        if not ok:
            await ctx.reply("Unbekannte Watch-ID.", ephemeral=True)
            return
        await ctx.reply("Watch entfernt.", ephemeral=ctx.interaction is not None)

    @vinted.command(name="list", description="Alle Watches dieses Servers")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def vinted_list(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        rows = await self.bot.storage.list_watches(ctx.guild.id)
        if not rows:
            await ctx.reply("Keine Watches.", ephemeral=ctx.interaction is not None)
            return
        lines = []
        for w in rows:
            st = "an" if w.enabled else "aus"
            lab = w.label or "—"
            lines.append(
                f"`{w.id}` · {st} · <#{w.channel_id}> · {lab}\n↳ {w.vinted_url[:120]}{'…' if len(w.vinted_url)>120 else ''}"
            )
        chunks = _chunk_text(lines, max_len=1900)
        eph = ctx.interaction is not None
        if ctx.interaction is not None:
            if not ctx.interaction.response.is_done():
                await ctx.defer(ephemeral=True)
            for c in chunks:
                await ctx.interaction.followup.send(c, ephemeral=True)
            return
        await ctx.reply(chunks[0], ephemeral=eph)
        for c in chunks[1:]:
            await ctx.send(c)

    @vinted.command(name="filter", description="Zusatzfilter (Preis, Keywords, Marken)")
    @app_commands.describe(
        watch_id="Watch-ID",
        max_price="Höchstpreis (leer lassen, optional mit clear_* zurücksetzen)",
        min_price="Mindestpreis",
        include_keywords="Komma-getrennt: alle müssen im Titel vorkommen",
        exclude_keywords="Komma-getrennt: keines darf vorkommen",
        brands="Komma-getrennt: eine Marke muss passen (Teilstring)",
        clear_max_price="Obergrenze löschen",
        clear_min_price="Untergrenze löschen",
    )
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def vinted_filter(
        self,
        ctx: commands.Context,
        watch_id: str,
        max_price: Optional[float] = None,
        min_price: Optional[float] = None,
        include_keywords: Optional[str] = None,
        exclude_keywords: Optional[str] = None,
        brands: Optional[str] = None,
        clear_max_price: bool = False,
        clear_min_price: bool = False,
    ) -> None:
        assert ctx.guild is not None
        inc = _split_csv(include_keywords)
        exc = _split_csv(exclude_keywords)
        br = _split_csv(brands)
        ok = await self.bot.storage.update_filters(
            watch_id.strip(),
            ctx.guild.id,
            max_price=max_price,
            min_price=min_price,
            keywords_include=inc,
            keywords_exclude=exc,
            brands=br,
            clear_max_price=clear_max_price,
            clear_min_price=clear_min_price,
        )
        if not ok:
            await ctx.reply("Watch nicht gefunden.", ephemeral=True)
            return
        await ctx.reply("Filter aktualisiert.", ephemeral=ctx.interaction is not None)

    @vinted.command(name="start", description="Monitoring starten")
    @app_commands.describe(watch_id="Leer lassen = alle Watches")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def vinted_start(
        self, ctx: commands.Context, watch_id: Optional[str] = None
    ) -> None:
        assert ctx.guild is not None
        if watch_id:
            ok = await self.bot.storage.set_enabled(watch_id.strip(), ctx.guild.id, True)
            if not ok:
                await ctx.reply("Watch nicht gefunden.", ephemeral=True)
                return
            await ctx.reply(f"Watch `{watch_id}` gestartet.", ephemeral=ctx.interaction is not None)
        else:
            n = await self.bot.storage.set_enabled_all(ctx.guild.id, True)
            await ctx.reply(f"{n} Watch(es) gestartet.", ephemeral=ctx.interaction is not None)

    @vinted.command(name="stop", description="Monitoring pausieren")
    @app_commands.describe(watch_id="Leer lassen = alle Watches")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def vinted_stop(
        self, ctx: commands.Context, watch_id: Optional[str] = None
    ) -> None:
        assert ctx.guild is not None
        if watch_id:
            ok = await self.bot.storage.set_enabled(watch_id.strip(), ctx.guild.id, False)
            if not ok:
                await ctx.reply("Watch nicht gefunden.", ephemeral=True)
                return
            await ctx.reply(f"Watch `{watch_id}` pausiert.", ephemeral=ctx.interaction is not None)
        else:
            n = await self.bot.storage.set_enabled_all(ctx.guild.id, False)
            await ctx.reply(f"{n} Watch(es) pausiert.", ephemeral=ctx.interaction is not None)

    @vinted.command(name="status", description="Bot- und Monitor-Status")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def vinted_status(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        rows = await self.bot.storage.list_watches(ctx.guild.id)
        on = sum(1 for w in rows if w.enabled)
        lat = round(self.bot.latency * 1000, 1)
        await ctx.reply(
            f"Latency **{lat} ms** · Watches: **{len(rows)}** (**{on}** aktiv) · "
            f"Poll-Basis **{self.bot.settings.default_poll_interval_sec}s**",
            ephemeral=ctx.interaction is not None,
        )

    @vinted.command(name="channel", description="Zielkanal ändern")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def vinted_channel(
        self,
        ctx: commands.Context,
        watch_id: str,
        channel: discord.TextChannel,
    ) -> None:
        assert ctx.guild is not None
        ok = await self.bot.storage.set_channel(
            watch_id.strip(), ctx.guild.id, channel.id
        )
        if not ok:
            await ctx.reply("Watch nicht gefunden.", ephemeral=True)
            return
        await ctx.reply(f"Kanal → {channel.mention}", ephemeral=ctx.interaction is not None)

    @vinted.command(name="interval", description="Abfrage-Intervall für eine Watch")
    @app_commands.describe(seconds="8–120 oder leer = Standard")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def vinted_interval(
        self,
        ctx: commands.Context,
        watch_id: str,
        seconds: Optional[app_commands.Range[int, 8, 120]] = None,
    ) -> None:
        assert ctx.guild is not None
        ok = await self.bot.storage.set_poll_interval(
            watch_id.strip(), ctx.guild.id, seconds
        )
        if not ok:
            await ctx.reply("Watch nicht gefunden.", ephemeral=True)
            return
        await ctx.reply(
            f"Intervall → **{seconds or self.bot.settings.default_poll_interval_sec}s** (plus kleiner Jitter).",
            ephemeral=ctx.interaction is not None,
        )

