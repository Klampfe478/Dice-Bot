# Voraussetzungen:
# 1. Python 3.8+ installiert
# 2. Abhängigkeiten installieren mit:
#    pip install -U discord.py python-dotenv
# 3. DISCORD_BOT_TOKEN Umgebungsvariable setzen (z.B. via .env oder Discord_Bot_Token.env)
# 4. Im Discord Developer Portal: "Privileged Gateway Intents" → "Message Content Intent" aktivieren

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

# Bot-Token aus der Umgebungsvariable laden
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# JSON-Datei für Persistenz
data_file = 'dice_data.json'

# Intents konfigurieren
intents = discord.Intents.default()
intents.message_content = True

# Bot initialisieren
bot = commands.Bot(command_prefix='!', intents=intents)

# Speicher für Datum des letzten Wurfs pro User und alle Roll-Records
last_rolls: dict[int, datetime.date] = {}
roll_records: list[dict] = []  # Elemente: {'user_id': int, 'timestamp': datetime.datetime, 'result': int}

# Hilfsfunktionen für Persistenz
def load_data():
    global last_rolls, roll_records
    if not os.path.isfile(data_file) or os.path.getsize(data_file) == 0:
        return
    try:
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        last_rolls = {int(uid): datetime.date.fromisoformat(ds)
                      for uid, ds in data.get('last_rolls', {}).items()}
        roll_records = [
            {
                'user_id': rec['user_id'],
                'timestamp': datetime.datetime.fromisoformat(rec['timestamp']),
                'result': rec['result']
            }
            for rec in data.get('roll_records', [])
            if all(k in rec for k in ('user_id', 'timestamp', 'result'))
        ]
    except Exception as e:
        print(f'Persistenz: Fehler beim Laden ({e}), starte mit leerem Datensatz')


def save_data():
    data = {
        'last_rolls': {str(uid): d.isoformat() for uid, d in last_rolls.items()},
        'roll_records': [
            {'user_id': r['user_id'], 'timestamp': r['timestamp'].isoformat(), 'result': r['result']}
            for r in roll_records
        ]
    }
    try:
        with open(data_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'Fehler beim Speichern der Daten: {e}')

# Bot-Events und Commands
@bot.event
async def on_ready():
    load_data()
    print(f'Bot eingeloggt als {bot.user.name} (ID: {bot.user.id})')

@bot.command(name='roll')
async def roll(ctx):
    user_id = ctx.author.id
    now = datetime.datetime.utcnow()
    today = now.date()
    if last_rolls.get(user_id) == today:
        await ctx.send(f"{ctx.author.mention}, du hast heute bereits gewürfelt. Versuch's doch morgen nochmal!")
        return

    result = random.randint(0, 100)
    last_rolls[user_id] = today
    roll_records.append({'user_id': user_id, 'timestamp': now, 'result': result})
    save_data()
    await ctx.send(f"{ctx.author.mention} würfelt... 🎲 Ergebnis: **{result}**")

@bot.command(name='top')
async def top(ctx, period: str):
    """Zeigt Top-Werte: '!top today' für heute, '!top all' für aktuellen Monat.
       Nun: Pro User wird nur das Maximum berücksichtigt."""
    now = datetime.datetime.utcnow()
    # Filtere Records je nach Zeitraum
    if period.lower() == 'today':
        filtered = [r for r in roll_records if r['timestamp'].date() == now.date()]
        title = 'Top-Würfe des Tages'
    elif period.lower() == 'all':
        filtered = [r for r in roll_records if r['timestamp'].year == now.year and r['timestamp'].month == now.month]
        title = 'Top-Würfe des Monats'
    else:
        await ctx.send("Ungültiger Zeitraum. Nutze `!top today` oder `!top all`.")
        return

    if not filtered:
        await ctx.send('Noch keine Würfe für diesen Zeitraum.')
        return

    # Für jeden User nur das höchste Ergebnis im Zeitraum
    best_per_user: dict[int, int] = {}
    for rec in filtered:
        uid = rec['user_id']
        if uid not in best_per_user or rec['result'] > best_per_user[uid]:
            best_per_user[uid] = rec['result']

    # Liste der besten Ergebnisse je User
    reduced = [{'user_id': uid, 'result': res} for uid, res in best_per_user.items()]
    # Sortiere und wähle Top 10
    top_n = sorted(reduced, key=lambda x: x['result'], reverse=True)[:10]

    # Embed erstellen
    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now)
    embed.set_footer(text="Dice-Game Leaderboard")
    embed.set_thumbnail(url="attachment://thumbnail.png")
    # Thumbnail als File senden
    file = discord.File('thumbnail.png', filename='thumbnail.png')

    for idx, rec in enumerate(top_n, start=1):
        user = await bot.fetch_user(rec['user_id'])
        display_name = user.name
        embed.add_field(name=f"{idx}. {display_name}", value=f"{user.mention}: **{rec['result']}**", inline=False)

    await ctx.send(embed=embed, file=file)

@bot.command(name='command')
async def command_list(ctx):
    """Liste aller verfügbaren Befehle auf."""
    commands_text = (
        "**Verfügbare Befehle:**\n"
        "• `!roll`: Würfelt eine zufällige Zahl zwischen 0 und 100.\n"
        "• `!top today`: Zeigt die Top-Würfe des Tages.\n"
        "• `!top all`: Zeigt die Top-Würfe des Monats.\n"
    )
    await ctx.send(commands_text)

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

# Starte beides parallel
async def main():
    # Webserver parallel zum Bot starten
    await asyncio.gather(
        start_webserver(),
        bot.start(os.environ["DISCORD_BOT_TOKEN"])
    )

if __name__ == '__main__':
    asyncio.run(main())
    if not TOKEN:
        print('Fehler: DISCORD_BOT_TOKEN Umgebungsvariable nicht gesetzt')
    else:
        bot.run(TOKEN)
