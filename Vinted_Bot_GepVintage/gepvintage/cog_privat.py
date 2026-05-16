from __future__ import annotations

import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from gepvintage.bot import GepVintageBot
from gepvintage.vinted_util import parse_vinted_catalog_url


def _safe_channel_name(display_name: str, user_id: int) -> str:
    s = re.sub(r"[^\w\s\-]", "", display_name, flags=re.UNICODE)[:28].strip()
    s = re.sub(r"\s+", "-", s) or str(user_id)
    return f"vinted-{s}"[:90]


def _private_monitor_overwrites(
    guild: discord.Guild,
    owner: discord.Member,
    bot_member: discord.Member,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    """Nur Ersteller + Bot sehen den Kanal; @everyone und (wo möglich) andere Rollen aus."""
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        bot_member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            read_message_history=True,
            attach_files=True,
            manage_messages=True,
        ),
        owner: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
        ),
    }
    top = bot_member.top_role
    for role in guild.roles:
        if role.is_default():
            continue
        if role in bot_member.roles:
            continue
        if top is not None and role >= top:
            continue
        overwrites[role] = discord.PermissionOverwrite(view_channel=False)
    return overwrites


class PrivatCog(commands.Cog, name="Privat"):
    """Persönlicher Monitor-Kanal (nur du + Bot)."""

    def __init__(self, bot: GepVintageBot) -> None:
        self.bot = bot

    @commands.hybrid_group(name="privat", invoke_without_command=True)
    @commands.guild_only()
    async def privat(self, ctx: commands.Context) -> None:
        p = ctx.prefix or self.bot.settings.command_prefix
        await ctx.reply(
            f"Privater Monitor: `erstellen`, `link`, `start`, `stop`, `status`, `löschen`. "
            f"Slash `/privat` oder `{p}privat`.",
            ephemeral=True,
        )

    @privat.command(name="erstellen", description="Privaten Kanal + genau einen Vinted-Link")
    @app_commands.describe(url="Vinted-Katalog-URL")
    @commands.guild_only()
    async def privat_erstellen(self, ctx: commands.Context, url: str) -> None:
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        if not self.bot.settings.private_monitor_enabled:
            await ctx.reply("Private Monitore sind auf diesem Bot deaktiviert.", ephemeral=True)
            return
        me = ctx.guild.me
        if not me or not me.guild_permissions.manage_channels:
            await ctx.reply(
                "Mir fehlt die Berechtigung **Kanäle verwalten**.", ephemeral=True
            )
            return
        try:
            parse_vinted_catalog_url(url)
        except ValueError as e:
            await ctx.reply(str(e), ephemeral=True)
            return

        ex = await self.bot.storage.get_private_watch(ctx.guild.id, ctx.author.id)
        if ex:
            ch = ctx.guild.get_channel(ex.channel_id)
            if ch is not None:
                await ctx.reply(
                    f"Du hast schon einen privaten Monitor: {ch.mention}\n"
                    f"URL ändern: `/privat link` · Entfernen: `/privat löschen`",
                    ephemeral=True,
                )
                return

        overwrites = _private_monitor_overwrites(ctx.guild, ctx.author, me)
        parent: Optional[discord.CategoryChannel] = None
        cid = self.bot.settings.private_monitor_category_id
        if cid is not None:
            c = ctx.guild.get_channel(cid)
            if isinstance(c, discord.CategoryChannel):
                parent = c

        name = _safe_channel_name(ctx.author.display_name, ctx.author.id)
        try:
            new_ch = await ctx.guild.create_text_channel(
                name=name,
                overwrites=overwrites,
                category=parent,
                sync_permissions=False,
                reason=f"Privater Vinted-Monitor für {ctx.author}",
            )
        except discord.HTTPException:
            await ctx.reply("Kanal konnte nicht erstellt werden.", ephemeral=True)
            return

        w = await self.bot.storage.upsert_private_watch(
            ctx.guild.id,
            ctx.author.id,
            new_ch.id,
            url.strip(),
            label="Privat",
        )
        await new_ch.send(
            f"{ctx.author.mention} **Dein privater Monitor**\n"
            f"Nur du und dieser Bot haben Zugriff (nicht sichtbar für andere Mitglieder).\n"
            f"Watch `{w.id}` · URL gespeichert · Erster Lauf ohne Spam-Alerts.\n"
            f"**Monitor ist aus** — Benachrichtigungen starten mit `/privat start`.\n"
            f"Discord-Push: Kanal rechtsklick → **Benachrichtigungen** → z. B. *nur Erwähnungen*.\n"
            f"_(Hinweis: Nutzer mit der Berechtigung **Administrator** können den Kanal trotzdem sehen.)_",
        )
        await ctx.reply(
            f"Fertig: {new_ch.mention}\n"
            f"Monitor ist **aus** — `/privat start` aktiviert Alerts. "
            f"Fast-Buy-Ton (falls genutzt): `/fastbuy sound`.",
            ephemeral=True,
        )

    @privat.command(name="link", description="Vinted-URL deines privaten Monitors ändern")
    @app_commands.describe(url="Neue Katalog-URL")
    @commands.guild_only()
    async def privat_link(self, ctx: commands.Context, url: str) -> None:
        assert ctx.guild is not None
        try:
            parse_vinted_catalog_url(url)
        except ValueError as e:
            await ctx.reply(str(e), ephemeral=True)
            return
        w = await self.bot.storage.get_private_watch(ctx.guild.id, ctx.author.id)
        if not w:
            await ctx.reply("Kein privater Monitor. Zuerst `/privat erstellen`.", ephemeral=True)
            return
        ch = ctx.guild.get_channel(w.channel_id)
        if ch is None:
            await ctx.reply(
                "Kanal fehlt. Bitte `/privat erstellen` erneut ausführen.",
                ephemeral=True,
            )
            return
        await self.bot.storage.upsert_private_watch(
            ctx.guild.id,
            ctx.author.id,
            ch.id,
            url.strip(),
            label=w.label,
        )
        await ctx.reply("URL aktualisiert (neuer Snapshot, keine alten Treffer).", ephemeral=True)

    @privat.command(name="start", description="Privaten Monitor an")
    @commands.guild_only()
    async def privat_start(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        w = await self.bot.storage.get_private_watch(ctx.guild.id, ctx.author.id)
        if not w:
            await ctx.reply("Kein privater Monitor.", ephemeral=True)
            return
        await self.bot.storage.set_enabled(w.id, ctx.guild.id, True)
        await ctx.reply("Monitor **an**.", ephemeral=True)

    @privat.command(name="stop", description="Privaten Monitor aus")
    @commands.guild_only()
    async def privat_stop(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        w = await self.bot.storage.get_private_watch(ctx.guild.id, ctx.author.id)
        if not w:
            await ctx.reply("Kein privater Monitor.", ephemeral=True)
            return
        await self.bot.storage.set_enabled(w.id, ctx.guild.id, False)
        await ctx.reply("Monitor **aus**.", ephemeral=True)

    @privat.command(name="status", description="Privaten Monitor anzeigen")
    @commands.guild_only()
    async def privat_status(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        w = await self.bot.storage.get_private_watch(ctx.guild.id, ctx.author.id)
        if not w:
            await ctx.reply("Kein privater Monitor.", ephemeral=True)
            return
        ch = ctx.guild.get_channel(w.channel_id)
        st = "an" if w.enabled else "aus"
        chs = ch.mention if ch else "*Kanal fehlt*"
        await ctx.reply(
            f"Watch `{w.id}` · **{st}** · Kanal {chs}\n{w.vinted_url[:200]}{'…' if len(w.vinted_url)>200 else ''}",
            ephemeral=True,
        )

    @privat.command(name="löschen", description="Privaten Kanal + Monitor entfernen")
    @commands.guild_only()
    async def privat_loeschen(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        w = await self.bot.storage.get_private_watch(ctx.guild.id, ctx.author.id)
        if not w:
            await ctx.reply("Nichts zu löschen.", ephemeral=True)
            return
        ch = ctx.guild.get_channel(w.channel_id)
        wid = await self.bot.storage.delete_private_watch(ctx.guild.id, ctx.author.id)
        if ch is not None:
            try:
                await ch.delete(reason=f"Privater Monitor gelöscht von {ctx.author}")
            except discord.HTTPException:
                pass
        await ctx.reply(
            f"Monitor `{wid}` entfernt."
            + (f" Kanal gelöscht." if ch else ""),
            ephemeral=True,
        )
