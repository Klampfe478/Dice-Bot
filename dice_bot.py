import os
import json
import datetime
import asyncio
import random
from aiohttp import web
from dotenv import load_dotenv
load_dotenv(dotenv_path='Discord_Bot_Token.env')
import discord
from discord.ext import commands, tasks
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build  # type: ignore
from zoneinfo import ZoneInfo

# Lock für parallele Sheet- und Command-Aufrufe
top_command_lock = asyncio.Lock()

# Umgebungsvariablen laden
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not TOKEN:
    raise RuntimeError("Fehler: DISCORD_BOT_TOKEN Umgebungsvariable nicht gesetzt")

# Google Sheets Client initialisieren
def init_sheets_client():
    creds_raw = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not creds_raw:
        raise RuntimeError("GOOGLE_SHEETS_CREDENTIALS fehlt")
    creds_info = json.loads(creds_raw)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    gc = gspread.authorize(creds)
    sheet_id = os.environ.get('SHEET_ID')
    if not sheet_id:
        raise RuntimeError("SHEET_ID fehlt")
    sh = gc.open_by_key(sheet_id)
    sheet = sh.sheet1
    headers = ['user_id','username','datum','zeitstempel','wert']
    values = sheet.get_all_values()
    if not values or values[0] != headers:
        sheet.insert_row(headers, index=1)
    return sheet

sheet = init_sheets_client()

# Bot setup
tintents = discord.Intents.default()
tintents.message_content = True
bot = commands.Bot(command_prefix='!', intents=tintents)

@bot.event
async def on_ready():
    print(f'Bot eingeloggt als {bot.user.name} (ID: {bot.user.id})')
    auto_backup.start()

@bot.command(name='roll')
async def roll(ctx):
    async with top_command_lock:
        user_id = str(ctx.author.id)
        now = datetime.datetime.now(ZoneInfo("Europe/Berlin"))
        today = now.date().isoformat()
        try:
            rows = sheet.get_all_values()[1:]
        except Exception:
            return await ctx.send("Fehler beim Zugriff auf Google Sheets.")
        if any(r[0]==user_id and r[2]==today for r in rows):
            return await ctx.send(f"{ctx.author.mention}, du hast heute schon gewürfelt!")
        result = random.randint(0,100)
        try:
            sheet.append_row([user_id, str(ctx.author), today, now.isoformat(), result])
        except Exception as e:
            print("Fehler beim Schreiben:", e)
        await ctx.send(f"{ctx.author.mention} würfelt… 🎲 **{result}**")

@bot.command(name='top')
async def top(ctx, period: str):
    async with top_command_lock:
        now = datetime.datetime.now(ZoneInfo("Europe/Berlin"))
        try:
            records = sheet.get_all_records()
        except Exception:
            return await ctx.send("Fehler beim Zugriff auf Google Sheets.")
        if period.lower()=='today':
            filtered = [r for r in records if r.get('datum')==str(now.date())]
            title='Top-Würfe des Tages'
        elif period.lower()=='all':
            filtered = [r for r in records if datetime.datetime.fromisoformat(r.get('zeitstempel','')).month==now.month and datetime.datetime.fromisoformat(r.get('zeitstempel','')).year==now.year]
            title='Top-Würfe des Monats'
        else:
            return await ctx.send("Nutz `!top today` oder `!top all`.")
        if not filtered:
            return await ctx.send('Keine Würfe im Zeitraum.')
        best={}
        for r in filtered:
            uid, val = r['user_id'], int(r['wert'])
            if uid not in best or val>best[uid]: best[uid]=val
        top_list=sorted([{'user_id':u,'result':v} for u,v in best.items()], key=lambda x:x['result'], reverse=True)[:10]
        embed=discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now)
        embed.set_footer(text='Dice-Game Leaderboard')
        embed.set_thumbnail(url='attachment://thumbnail.png')
        file=discord.File('thumbnail.png', filename='thumbnail.png')
        for i,rec in enumerate(top_list,1):
            user=await bot.fetch_user(int(rec['user_id']))
            embed.add_field(name=f"{i}. {user.name}", value=f"{user.mention}: **{rec['result']}**", inline=False)
        await ctx.send(embed=embed, file=file)

@bot.command(name='daily')
async def daily(ctx, days: int = 7):
    async with top_command_lock:
        now = datetime.datetime.now(ZoneInfo("Europe/Berlin"))
        try:
            records = sheet.get_all_records()
        except Exception:
            return await ctx.send("Fehler beim Zugriff auf Google Sheets.")
        dates=[(now.date()-datetime.timedelta(days=i)).isoformat() for i in range(days)]
        counts={}
        for d in dates:
            daily=[r for r in records if r.get('datum')==d]
            if not daily: continue
            maxv=max(int(r['wert']) for r in daily)
            winners=[r['user_id'] for r in daily if int(r['wert'])==maxv]
            for uid in winners: counts[uid]=counts.get(uid,0)+1
        if not counts:
            return await ctx.send(f"Keine Daten für die letzten {days} Tage.")
        sorted_counts=sorted(counts.items(), key=lambda x:x[1], reverse=True)
        embed=discord.Embed(title=f"🏆 Daily-Champs der letzten {days} Tage", color=discord.Color.green(), timestamp=now)
        embed.set_footer(text='Daily Stats')
        for i,(uid,cnt) in enumerate(sorted_counts[:10],1):
            user=await bot.fetch_user(int(uid))
            embed.add_field(name=f"{i}. {user.name}", value=f"{user.mention}: {cnt} Tage", inline=False)
        await ctx.send(embed=embed)

@bot.command(name='command')
async def command_list(ctx):
    text=("**Befehle:**\n""!roll – würfeln""\n!top today – Tages-Bestenliste""\n!top all – Monats-Bestenliste""\n!daily [Tage] – Daily-Champions")
    await ctx.send(text)

@bot.command(name='backup')
async def backup_sheet(ctx):
    status=await ctx.send('Backup...')
    now=datetime.datetime.now(ZoneInfo("Europe/Berlin"))
    try:
        creds=Credentials.from_service_account_info(json.loads(os.environ.get('GOOGLE_SHEETS_CREDENTIALS')), scopes=["https://www.googleapis.com/auth/drive"])
        service=build('drive','v3', credentials=creds)
        orig=os.environ['SHEET_ID']
        ts=now.strftime('%Y-%m-%d_%H-%M')
        new=f'Backup_{ts}'
        service.files().copy(fileId=orig, body={'name':new}).execute()
        await status.edit(content=f'✅ {new} erstellt')
    except Exception as e:
        print('Backup-Fehler', e)
        await status.edit(content='⚠️ Backup fehlgeschlagen')

@tasks.loop(hours=24)
async def auto_backup():
    now=datetime.datetime.now(ZoneInfo("Europe/Berlin"))
    if now.day==1:
        creds=Credentials.from_service_account_info(json.loads(os.environ.get('GOOGLE_SHEETS_CREDENTIALS')), scopes=["https://www.googleapis.com/auth/drive"])
        service=build('drive','v3',credentials=creds)
        orig=os.environ['SHEET_ID']
        new=f"AutoBackup_{now.strftime('%Y-%m-%d')}"
        try:
            service.files().copy(fileId=orig, body={'name':new}).execute()
            print(f'🔁 {new} erstellt')
        except Exception as e:
            print('AutoBackup-Fehler', e)

async def start_webserver():
    async def handle(req):
        return web.Response(text='OK')

    app = web.Application()
    app.router.add_get('/', handle)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8000)))
    await site.start()

    print("🌐 Webserver gestartet")  # ← Diese Zeile einfügen

async def main():
    await asyncio.gather(start_webserver(), bot.start(TOKEN))

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(start_webserver())  # Webserver im Hintergrund starten
    bot.run(TOKEN)  # startet den Bot synchron, inkl. integriertem reconnect/retry
