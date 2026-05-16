from __future__ import annotations

import discord
from discord.ext import commands

from gepvintage.bot import GepVintageBot


class UebersichtCog(commands.Cog, name="Übersicht"):
    """Bot-interne Statistik (kein Vinted-Konto-Zugriff)."""

    def __init__(self, bot: GepVintageBot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="uebersicht",
        description="Übersicht: private Monitore & Bot-Statistik (kein Vinted-Konto)",
    )
    @commands.guild_only()
    async def uebersicht(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        ephem = ctx.interaction is not None
        emb = discord.Embed(
            title="Übersicht (Bot-Daten)",
            description=(
                "**Hinweis:** Käufe, Verkäufe und Euro-Beträge auf **Vinted** kann dieser Bot **nicht** "
                "auslesen — dafür bräuchte man Zugriff auf dein Vinted-Konto. "
                "Hier siehst du nur Daten, die **dieser Bot** auf diesem Server speichert und sendet."
            ),
            color=discord.Color.dark_teal(),
        )
        pw = await self.bot.storage.get_private_watch(ctx.guild.id, ctx.author.id)
        if pw:
            seen_n = await self.bot.storage.count_seen_for_watch(pw.id)
            st = "an" if pw.enabled else "aus"
            emb.add_field(
                name="Dein privater Monitor",
                value=(
                    f"Watch `{pw.id}` · **{st}**\n"
                    f"Gesendete Alerts: **{pw.alerts_sent}**\n"
                    f"Bekannte Listings (Snapshot): **{seen_n}**"
                ),
                inline=False,
            )
        else:
            emb.add_field(
                name="Privater Monitor",
                value="Keiner eingerichtet — `/privat erstellen`.",
                inline=False,
            )

        member = ctx.author if isinstance(ctx.author, discord.Member) else None
        if member and member.guild_permissions.manage_guild:
            gs = await self.bot.storage.guild_stats(ctx.guild.id)
            emb.add_field(
                name="Server (nur für Server-Verwaltung)",
                value=(
                    f"Watch-Einträge: **{gs['total']}** · aktiv: **{gs['enabled']}**\n"
                    f"Server-Watches: **{gs['server_watches']}** · privat: **{gs['private_watches']}**\n"
                    f"Alerts gesendet (Summe): **{gs['alerts_sent']}**"
                ),
                inline=False,
            )

        await ctx.reply(embed=emb, ephemeral=ephem)
