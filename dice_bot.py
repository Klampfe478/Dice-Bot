import os
import json
import datetime
import asyncio
from aiohttp import web
import random
from dotenv import load_dotenv
load_dotenv(dotenv_path='Discord_Bot_Token.env')
import discord
from discord.ext import commands
import gspread
from google.oauth2.service_account import Credentials

def init_sheets_client():
    creds_raw = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not creds_raw:
        raise RuntimeError("GOOGLE_SHEETS_CREDENTIALS fehlt")

    creds_json = json.loads(creds_raw)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)

    gc = gspread.authorize(creds)
    sheet_id = os.environ.get("SHEET_ID")
    if not sheet_id:
        raise RuntimeError("SHEET_ID fehlt")

    sh = gc.open_by_key(sheet_id)
    sheet = sh.sheet1

    # 🧼 Header prüfen und ggf. setzen
    headers = ["user_id", "username", "datum", "zeitstempel", "wert"]
    current_values = sheet.get_all_values()
    if not current_values or current_values[0] != headers:
        sheet.insert_row(headers, index=1)

    return sheet

# Bot-Token aus der Umgebungsvariable laden
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not TOKEN:
    raise RuntimeError("Fehler: DISCORD_BOT_TOKEN Umgebungsvariable nicht gesetzt")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)
sheet = init_sheets_client()

@bot.event
async def on_ready():
    print(f'Bot eingeloggt als {bot.user.name} (ID: {bot.user.id})')

@bot.command(name='roll')
async def roll(ctx):
    user_id = str(ctx.author.id)
    now = datetime.datetime.utcnow()
    today = str(now.date())

    try:
        data = sheet.get_all_values()
        header = data[0]
        rows = data[1:]
    except Exception as e:
        await ctx.send("Fehler beim Zugriff auf das Google Sheet.")
        print(e)
        return

    for row in rows:
        if len(row) >= 3 and row[0] == user_id and row[2] == today:
            await ctx.send(f"{ctx.author.mention}, du hast heute bereits gewürfelt. Versuch's doch morgen nochmal!")
            return

    result = random.randint(0, 100)
    try:
        sheet.append_row([user_id, str(ctx.author), today, now.isoformat(), result])
    except Exception as e:
        print(f"Fehler beim Schreiben ins Google Sheet: {e}")

    await ctx.send(f"{ctx.author.mention} würfelt... 🎲 Ergebnis: **{result}**")

@bot.command(name='top')
async def top(ctx, period: str):
    now = datetime.datetime.utcnow()
    try:
        records = sheet.get_all_records()
    except Exception as e:
        await ctx.send("⚠️ Fehler beim Zugriff auf das Google Sheet.")
        print("Fehler in !top:", e)
        return

    if period.lower() == 'today':
        filtered = [r for r in records if r.get('datum') == str(now.date())]
        title = 'Top-Würfe des Tages'
    elif period.lower() == 'all':
        filtered = [
            r for r in records
            if 'zeitstempel' in r and datetime.datetime.fromisoformat(r['zeitstempel']).year == now.year
            and datetime.datetime.fromisoformat(r['zeitstempel']).month == now.month
        ]
        title = 'Top-Würfe des Monats'
    else:
        await ctx.send("Ungültiger Zeitraum. Nutze `!top today` oder `!top all`.")
        return

    if not filtered:
        await ctx.send('Noch keine Würfe für diesen Zeitraum.')
        return

    best_per_user = {}
    for rec in filtered:
        uid = rec['user_id']
        wert = rec['wert']
        if uid not in best_per_user or wert > best_per_user[uid]:
            best_per_user[uid] = wert

    top_n = sorted(
        [{'user_id': uid, 'result': val} for uid, val in best_per_user.items()],
        key=lambda x: x['result'],
        reverse=True
    )[:10]

    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now)
    embed.set_footer(text="Dice-Game Leaderboard")
    embed.set_thumbnail(url="attachment://thumbnail.png")
    file = discord.File('thumbnail.png', filename='thumbnail.png')

    for idx, rec in enumerate(top_n, start=1):
        user = await bot.fetch_user(int(rec['user_id']))
        embed.add_field(name=f"{idx}. {user.name}", value=f"{user.mention}: **{rec['result']}**", inline=False)

    await ctx.send(embed=embed, file=file)

@bot.command(name='command')
async def command_list(ctx):
    help_text = (
        "**Verfügbare Commands:**\n"
        "• `!roll` – Würfelt eine zufällige Zahl zwischen 0 und 100.\n"
        "• `!top today` – Zeigt die Top-Würfe des Tages.\n"
        "• `!top all` – Zeigt die Top-Würfe des Monats."
    )
    await ctx.send(help_text)

@bot.command(name='backup')
async def backup_sheet(ctx):
    try:
        gc = sheet.spreadsheet.client
        original = sheet.spreadsheet
        timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
        new_title = f"Backup_{original.title}_{timestamp}"
        backup_copy = gc.copy(original.id, title=new_title)
        await ctx.send(f"✅ Backup erstellt: `{new_title}`")
    except Exception as e:
        print("Fehler beim Backup:", e)
        await ctx.send("⚠️ Backup fehlgeschlagen.")

async def start_webserver():
    async def handle(request):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", handle)

    port = int(os.environ.get("PORT", 8000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Webserver läuft auf Port {port}")

async def main():
    await asyncio.gather(
        start_webserver(),
        bot.start(os.environ["DISCORD_BOT_TOKEN"])
    )

if __name__ == '__main__':
    asyncio.run(main())
