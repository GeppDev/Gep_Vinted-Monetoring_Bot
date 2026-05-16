from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from gepvintage.bot import GepVintageBot


def _csv(s: Optional[str]) -> Optional[list[str]]:
    if s is None or not str(s).strip():
        return None
    return [p.strip() for p in str(s).split(",") if p.strip()]


class FastbuyCog(commands.Cog, name="FastBuy"):
    def __init__(self, bot: GepVintageBot) -> None:
        self.bot = bot

    def _clamp_pm(self, n: int) -> int:
        return max(1, min(int(n), self.bot.settings.fast_assistant_max_per_minute))

    def _clamp_par(self, n: int) -> int:
        return max(1, min(int(n), self.bot.settings.fast_assistant_max_parallel_tabs))

    @commands.hybrid_group(name="fastbuy", invoke_without_command=True)
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def fastbuy(self, ctx: commands.Context) -> None:
        p = ctx.prefix or self.bot.settings.command_prefix
        await ctx.reply(
            f"Fast Buy Assistant: `open`, `sound`, `limits`, `priority`, `bestdeal`, `status`. "
            f"Slash `/fastbuy` oder `{p}fastbuy`.",
            ephemeral=ctx.interaction is not None,
        )

    @fastbuy.command(name="open", description="Browser-Fast-Open an/aus (dieser Server)")
    @app_commands.describe(enabled="True = bei Treffern Tabs öffnen")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def fastbuy_open(self, ctx: commands.Context, enabled: bool) -> None:
        assert ctx.guild is not None
        if not self.bot.settings.fast_assistant_enabled:
            await ctx.reply(
                "Global deaktiviert: setze `FAST_ASSISTANT_ENABLED=true` in `.env`.",
                ephemeral=True,
            )
            return
        c = await self.bot.storage.upsert_fast_assistant(
            ctx.guild.id, fast_open_enabled=enabled
        )
        await ctx.reply(
            f"Fast Open → **{'an' if c.fast_open_enabled else 'aus'}**.",
            ephemeral=ctx.interaction is not None,
        )

    @fastbuy.command(name="sound", description="Sound bei Fast-Open an/aus")
    @app_commands.describe(enabled="True = Ton abspielen")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def fastbuy_sound(self, ctx: commands.Context, enabled: bool) -> None:
        assert ctx.guild is not None
        c = await self.bot.storage.upsert_fast_assistant(
            ctx.guild.id, sound_enabled=enabled
        )
        await ctx.reply(
            f"Sound → **{'an' if c.sound_enabled else 'aus'}**.",
            ephemeral=ctx.interaction is not None,
        )

    @fastbuy.command(name="limits", description="Rate-Limits & parallele Tabs")
    @app_commands.describe(
        per_minute="Max. öffnende Tabs pro Minute (Server-DB)",
        parallel="Gleichzeitig erlaubte Opens (≤ globales Cap in .env)",
        min_interval="Mindestabstand zwischen Opens (Sekunden)",
    )
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def fastbuy_limits(
        self,
        ctx: commands.Context,
        per_minute: Optional[app_commands.Range[int, 1, 30]] = None,
        parallel: Optional[app_commands.Range[int, 1, 6]] = None,
        min_interval: Optional[app_commands.Range[float, 0.5, 60.0]] = None,
    ) -> None:
        assert ctx.guild is not None
        pm = self._clamp_pm(per_minute) if per_minute is not None else None
        par = self._clamp_par(parallel) if parallel is not None else None
        c = await self.bot.storage.upsert_fast_assistant(
            ctx.guild.id,
            max_opens_per_minute=pm,
            max_parallel_tabs=par,
            min_interval_sec=float(min_interval) if min_interval is not None else None,
        )
        await ctx.reply(
            f"Limits → **{c.max_opens_per_minute}/min**, parallel **{c.max_parallel_tabs}**, "
            f"Abstand **{c.min_interval_sec:g}s**.",
            ephemeral=ctx.interaction is not None,
        )

    @fastbuy.command(name="priority", description="Nur bei diesen Kriterien Fast-Open")
    @app_commands.describe(
        max_price="Höchstpreis (Pflicht-Teil, wenn gesetzt)",
        brands="Komma-getrennt, eine muss passen",
        keywords="Komma-getrennt",
        match_all_keywords="True = alle Keywords im Titel",
        clear_max_price="Obergrenze entfernen",
    )
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def fastbuy_priority(
        self,
        ctx: commands.Context,
        max_price: Optional[float] = None,
        brands: Optional[str] = None,
        keywords: Optional[str] = None,
        match_all_keywords: Optional[bool] = None,
        clear_max_price: bool = False,
    ) -> None:
        assert ctx.guild is not None
        br = _csv(brands)
        kw = _csv(keywords)
        c = await self.bot.storage.upsert_fast_assistant(
            ctx.guild.id,
            priority_max_price=max_price,
            priority_brands=br,
            priority_keywords=kw,
            priority_keywords_all=match_all_keywords,
            clear_priority_max_price=clear_max_price,
        )
        mode = "ALLE" if c.priority_keywords_all else "EINES"
        await ctx.reply(
            f"Priority → max **{c.priority_max_price or '—'}**, Marken **{c.priority_brands}**, "
            f"Keywords **{c.priority_keywords}** ({mode}).",
            ephemeral=ctx.interaction is not None,
        )

    @fastbuy.command(name="bestdeal", description="Aggressivere Alerts unter diesem Preis")
    @app_commands.describe(max_price="Preis ≤ = BEST DEAL", clear="Zurücksetzen")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def fastbuy_bestdeal(
        self,
        ctx: commands.Context,
        max_price: Optional[float] = None,
        clear: bool = False,
    ) -> None:
        assert ctx.guild is not None
        c = await self.bot.storage.upsert_fast_assistant(
            ctx.guild.id,
            best_deal_max_price=max_price,
            clear_best_deal_max_price=clear,
        )
        await ctx.reply(
            f"BEST DEAL ≤ **{c.best_deal_max_price or '—'}**.",
            ephemeral=ctx.interaction is not None,
        )

    @fastbuy.command(name="status", description="Fast Buy Assistant Status")
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def fastbuy_status(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        c = await self.bot.storage.get_fast_assistant(ctx.guild.id)
        g = self.bot.settings.fast_assistant_enabled
        snd_g = self.bot.settings.fast_assistant_sound_enabled
        await ctx.reply(
            f"Global FA **{'an' if g else 'aus'}** · global Sound **{'an' if snd_g else 'aus'}**\n"
            f"Server Fast Open **{'an' if c.fast_open_enabled else 'aus'}** · Sound **{'an' if c.sound_enabled else 'aus'}**\n"
            f"Limits **{c.max_opens_per_minute}/min** · parallel **{c.max_parallel_tabs}** · "
            f"Δ **{c.min_interval_sec:g}s**\n"
            f"Priority: max **{c.priority_max_price}** · {c.priority_brands} · {c.priority_keywords} "
            f"({'ALLE' if c.priority_keywords_all else 'EINES'})\n"
            f"BEST DEAL ≤ **{c.best_deal_max_price}**",
            ephemeral=ctx.interaction is not None,
        )
