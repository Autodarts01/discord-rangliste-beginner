import os
import re
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
import gspread

from discord.ext import commands, tasks
from google.oauth2.service_account import Credentials


# =========================================================
# KONFIGURATION
# =========================================================

TOKEN = os.environ["DISCORD_TOKEN"]

GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]

TZ = ZoneInfo("Europe/Vienna")

MATCH_CHANNEL_NAME = "match-ergebnisse"
TABLE_CHANNEL_NAME = "ranglisten-tabelle"
INFO_CHANNEL_NAME = "ranglisten-info"
ADMIN_CHANNEL_NAME = "admin-chat"


# =========================================================
# GOOGLE SHEETS
# =========================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

credentials = Credentials.from_service_account_info(
    {
        "type": "service_account",
        "project_id": os.environ["GOOGLE_PROJECT_ID"],
        "private_key_id": os.environ["GOOGLE_PRIVATE_KEY_ID"],
        "private_key": os.environ["GOOGLE_PRIVATE_KEY"].replace("\\n", "\n"),
        "client_email": os.environ["GOOGLE_CLIENT_EMAIL"],
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "token_uri": "https://oauth2.googleapis.com/token",
    },
    scopes=SCOPES,
)

gc = gspread.authorize(credentials)

spreadsheet = gc.open_by_key(GOOGLE_SHEET_ID)

ergebnis_sheet = spreadsheet.worksheet("Ergebnis")
tabelle_sheet = spreadsheet.worksheet("Tabelle")
final_sheet = spreadsheet.worksheet("Tabelle final")


# =========================================================
# DISCORD
# =========================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================================================
# GOOGLE-SHEET ÜBERSCHRIFTEN
# =========================================================

ERGEBNIS_HEADER = [
    "Spieler A",
    "Spieler B",
    "Legs A",
    "Legs B",
    "Bot",
    "Bot",
    "Gewinner",
    "Datum",
]

TABELLE_HEADER = [
    "Rang",
    "Name",
    "Spiele",
    "Siege",
    "Niederlage",
    "Legs +",
    "Legs -",
    "Leg Dif",
    "Punkte",
]


# =========================================================
# HILFSFUNKTIONEN
# =========================================================

def jetzt():
    return datetime.now(TZ)


def get_channel(name):
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name=name)
        if channel:
            return channel
    return None


def is_admin(member):
    return (
        isinstance(member, discord.Member)
        and member.guild_permissions.administrator
    )


def normalize_name(name):
    return " ".join(name.strip().lower().split())


def ensure_headers():
    """
    Stellt sicher, dass die Google-Sheets die benötigten
    Überschriften haben.
    """

    # Ergebnis
    ergebnis_values = ergebnis_sheet.get_all_values()

    if not ergebnis_values:
        ergebnis_sheet.update("A1:H1", [ERGEBNIS_HEADER])
    else:
        current = ergebnis_values[0]

        # Fehlende Datum-Spalte ergänzen
        if len(current) < 8:
            ergebnis_sheet.update("H1", [["Datum"]])

    # Tabelle
    tabelle_sheet.update("A1:I1", [TABELLE_HEADER])

    # Tabelle final
    final_sheet.update("A1:I1", [TABELLE_HEADER])


# =========================================================
# ERGEBNISSE LESEN
# =========================================================

def get_match_rows():
    """
    Liest nur die Ergebnisse des aktuellen Monats.

    Archivierte Monate liegen in Archiv_YYYY_MM und werden
    nicht in die aktuelle Rangliste eingerechnet.
    """

    rows = ergebnis_sheet.get_all_values()

    if len(rows) <= 1:
        return []

    aktueller_monat = jetzt().strftime("%Y-%m")

    result = []

    for row_number, row in enumerate(rows[1:], start=2):

        if len(row) < 7:
            continue

        spieler_a = row[0].strip()
        spieler_b = row[1].strip()
        legs_a = row[2].strip()
        legs_b = row[3].strip()
        winner = row[6].strip()

        datum = ""

        if len(row) >= 8:
            datum = row[7].strip()

        if not spieler_a or not spieler_b:
            continue

        # Alte Zeilen ohne Datum werden ebenfalls berücksichtigt,
        # solange sie in der aktuellen Ergebnis-Tabelle stehen.
        if datum:
            if not datum.startswith(aktueller_monat):
                continue

        try:
            legs_a_int = int(legs_a)
            legs_b_int = int(legs_b)
        except ValueError:
            continue

        result.append({
            "row": row_number,
            "spieler_a": spieler_a,
            "spieler_b": spieler_b,
            "legs_a": legs_a_int,
            "legs_b": legs_b_int,
            "winner": winner,
            "datum": datum,
        })

    return result


# =========================================================
# TABELLE BERECHNEN
# =========================================================

def calculate_table():
    stats = {}

    matches = get_match_rows()

    for match in matches:

        p1 = match["spieler_a"]
        p2 = match["spieler_b"]

        legs_a = match["legs_a"]
        legs_b = match["legs_b"]

        winner = match["winner"]

        for player in [p1, p2]:

            key = normalize_name(player)

            if key not in stats:
                stats[key] = {
                    "name": player,
                    "spiele": 0,
                    "siege": 0,
                    "niederlagen": 0,
                    "legs_plus": 0,
                    "legs_minus": 0,
                }

        p1_key = normalize_name(p1)
        p2_key = normalize_name(p2)

        stats[p1_key]["spiele"] += 1
        stats[p2_key]["spiele"] += 1

        stats[p1_key]["legs_plus"] += legs_a
        stats[p1_key]["legs_minus"] += legs_b

        stats[p2_key]["legs_plus"] += legs_b
        stats[p2_key]["legs_minus"] += legs_a

        winner_key = normalize_name(winner)

        if winner_key == p1_key:
            stats[p1_key]["siege"] += 1
            stats[p2_key]["niederlagen"] += 1

        elif winner_key == p2_key:
            stats[p2_key]["siege"] += 1
            stats[p1_key]["niederlagen"] += 1

    table = []

    for data in stats.values():

        leg_dif = (
            data["legs_plus"] -
            data["legs_minus"]
        )

        punkte = data["siege"] * 3

        table.append({
            "name": data["name"],
            "spiele": data["spiele"],
            "siege": data["siege"],
            "niederlagen": data["niederlagen"],
            "legs_plus": data["legs_plus"],
            "legs_minus": data["legs_minus"],
            "leg_dif": leg_dif,
            "punkte": punkte,
        })

    # Manfred-Prinzip:
    # Punkte -> Leg-Differenz -> Siege
    table.sort(
        key=lambda x: (
            x["punkte"],
            x["leg_dif"],
            x["siege"],
        ),
        reverse=True,
    )

    return table


# =========================================================
# TABELLE IN GOOGLE SHEETS SCHREIBEN
# =========================================================

def write_table():

    table = calculate_table()

    rows = [TABELLE_HEADER]

    for rang, player in enumerate(table, start=1):

        rows.append([
            rang,
            player["name"],
            player["spiele"],
            player["siege"],
            player["niederlagen"],
            player["legs_plus"],
            player["legs_minus"],
            player["leg_dif"],
            player["punkte"],
        ])

    # Tabelle
    tabelle_sheet.clear()
    tabelle_sheet.update(
        f"A1:I{len(rows)}",
        rows
    )

    # Tabelle final
    final_sheet.clear()
    final_sheet.update(
        f"A1:I{len(rows)}",
        rows
    )

    return table


# =========================================================
# DISCORD-TABELLE
# =========================================================

def table_to_discord(table):

    now = jetzt()

    text = (
        f"**Aktuelle Tabelle {now.strftime('%d.%m.%Y %H:%M')} Uhr**\n\n"
        "```text\n"
        f"{'Rg':<3}{'Name':<10}{'Sp':>3}{'S':>3}{'N':>3}"
        f"{'L+':>4}{'L-':>4}{'Dif':>4}{'Pkt':>4}\n"
        f"{'-'*3}{'-'*10}{'-'*3}{'-'*3}{'-'*3}"
        f"{'-'*4}{'-'*4}{'-'*4}{'-'*4}\n"
        
    )

    if not table:
        text += "Noch keine Spiele in diesem Monat.\n"
    else:
        for rang, player in enumerate(table, start=1):
            text += (
                f"{rang:<4}"
                f"{player['name']:<15}"
                f"{player['spiele']:>3}"
                f"{player['siege']:>4}"
                f"{player['niederlagen']:>4}"
                f"{player['legs_plus']:>5}"
                f"{player['legs_minus']:>5}"
                f"{player['leg_dif']:>6}"
                f"{player['punkte']:>6}\n"
            )

    text += "```"

    return text


async def update_discord_table():

    channel = get_channel(TABLE_CHANNEL_NAME)

    if not channel:
        print(
            f"⚠️ Kanal #{TABLE_CHANNEL_NAME} nicht gefunden.",
            flush=True
        )
        return

    table = write_table()

    text = table_to_discord(table)

    await channel.send(text)


# =========================================================
# MONATSSIEGER
# =========================================================

def archive_current_month():

    now = jetzt()

    year = now.year
    month = now.month

    archive_name = f"Archiv_{year}_{month:02d}"

    existing_names = [
        ws.title for ws in spreadsheet.worksheets()
    ]

    if archive_name in existing_names:
        return archive_name

    old_values = ergebnis_sheet.get_all_values()

    archive_sheet = spreadsheet.add_worksheet(
        title=archive_name,
        rows=max(len(old_values) + 5, 10),
        cols=8
    )

    if old_values:
        archive_sheet.update(
            f"A1:H{len(old_values)}",
            old_values
        )

    return archive_name


def get_previous_month_name():

    now = jetzt()

    year = now.year
    month = now.month - 1

    if month == 0:
        month = 12
        year -= 1

    months = [
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ]

    return f"{months[month - 1]} {year}"


def get_previous_month_archive():

    now = jetzt()

    year = now.year
    month = now.month - 1

    if month == 0:
        month = 12
        year -= 1

    archive_name = f"Archiv_{year}_{month:02d}"

    try:
        return spreadsheet.worksheet(archive_name)
    except gspread.WorksheetNotFound:
        return None


def calculate_archive_table(archive_sheet):

    if not archive_sheet:
        return []

    rows = archive_sheet.get_all_values()

    stats = {}

    for row in rows[1:]:

        if len(row) < 7:
            continue

        p1 = row[0].strip()
        p2 = row[1].strip()

        try:
            legs_a = int(row[2])
            legs_b = int(row[3])
        except ValueError:
            continue

        winner = row[6].strip()

        if not p1 or not p2:
            continue

        for player in [p1, p2]:

            key = normalize_name(player)

            if key not in stats:
                stats[key] = {
                    "name": player,
                    "spiele": 0,
                    "siege": 0,
                    "niederlagen": 0,
                    "legs_plus": 0,
                    "legs_minus": 0,
                }

        p1_key = normalize_name(p1)
        p2_key = normalize_name(p2)

        stats[p1_key]["spiele"] += 1
        stats[p2_key]["spiele"] += 1

        stats[p1_key]["legs_plus"] += legs_a
        stats[p1_key]["legs_minus"] += legs_b

        stats[p2_key]["legs_plus"] += legs_b
        stats[p2_key]["legs_minus"] += legs_a

        winner_key = normalize_name(winner)

        if winner_key == p1_key:
            stats[p1_key]["siege"] += 1
            stats[p2_key]["niederlagen"] += 1

        elif winner_key == p2_key:
            stats[p2_key]["siege"] += 1
            stats[p1_key]["niederlagen"] += 1

    table = []

    for data in stats.values():

        leg_dif = (
            data["legs_plus"] -
            data["legs_minus"]
        )

        punkte = data["siege"] * 3

        table.append({
            "name": data["name"],
            "spiele": data["spiele"],
            "siege": data["siege"],
            "niederlagen": data["niederlagen"],
            "legs_plus": data["legs_plus"],
            "legs_minus": data["legs_minus"],
            "leg_dif": leg_dif,
            "punkte": punkte,
        })

    table.sort(
        key=lambda x: (
            x["punkte"],
            x["leg_dif"],
            x["siege"],
        ),
        reverse=True,
    )

    return table


async def send_month_winner():

    archive_sheet = get_previous_month_archive()

    if not archive_sheet:
        print(
            "⚠️ Kein Monatsarchiv gefunden.",
            flush=True
        )
        return

    table = calculate_archive_table(archive_sheet)

    if not table:
        return

    top3 = table[:3]

    month_name = get_previous_month_name()

    text = (
        f"🏆 **MONATSSIEGER – {month_name.upper()}**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    emojis = ["🥇", "🥈", "🥉"]

    for index, player in enumerate(top3):

        text += (
            f"{emojis[index]} **{player['name']}**\n"
            f"🎮 {player['spiele']} Spiele | "
            f"🏆 {player['siege']} Siege | "
            f"💀 {player['niederlagen']} Niederlagen\n"
            f"🎯 Legs +{player['legs_plus']} | "
            f"📉 Legs -{player['legs_minus']} | "
            f"📊 Diff {player['leg_dif']}\n"
            f"⭐ **{player['punkte']} Punkte**\n\n"
        )

    winner = top3[0]

    text += (
        f"👑 **Monatssieger: {winner['name']}!**\n"
        f"⭐ **{winner['punkte']} Punkte**\n"
        "🎯 Herzlichen Glückwunsch! 🔥"
    )

    channel = get_channel(INFO_CHANNEL_NAME)

    if channel:
        await channel.send(text)


# =========================================================
# MONATSRESET
# =========================================================

async def monthly_reset():

    print("🔄 Monatsreset wird ausgeführt...", flush=True)

    # Alten Monat archivieren
    archive_current_month()

    # Top 3 des abgeschlossenen Monats posten
    await send_month_winner()

    # Aktuelle Ergebnis-Tabelle leeren
    ergebnis_sheet.clear()

    ergebnis_sheet.update(
        "A1:H1",
        [ERGEBNIS_HEADER]
    )

    # Tabellen zurücksetzen
    tabelle_sheet.clear()
    tabelle_sheet.update(
        "A1:I1",
        [TABELLE_HEADER]
    )

    final_sheet.clear()
    final_sheet.update(
        "A1:I1",
        [TABELLE_HEADER]
    )

    print("✅ Monatsreset abgeschlossen.", flush=True)


# =========================================================
# ZEITPLAN
# =========================================================

last_reset_month = None
last_table_update = None


@tasks.loop(seconds=30)
async def scheduler():

    global last_reset_month
    global last_table_update

    now = jetzt()

    current_month = now.strftime("%Y-%m")

    # -----------------------------------------------------
    # MONATSRESET
    # -----------------------------------------------------

    if now.day == 1 and now.hour == 0 and now.minute == 0:

        if last_reset_month != current_month:

            await monthly_reset()

            last_reset_month = current_month

            await asyncio.sleep(2)

            return

    # -----------------------------------------------------
    # TABELLE 08 / 12 / 18 / 22 UHR
    # -----------------------------------------------------

    update_hours = {8, 12, 18, 22}

    if now.hour in update_hours and now.minute == 0:

        update_key = now.strftime("%Y-%m-%d-%H")

        if last_table_update != update_key:

            await update_discord_table()

            last_table_update = update_key


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():

    print(f"🤖 Mad Dog online: {bot.user}", flush=True)

    ensure_headers()

    if not scheduler.is_running():
        scheduler.start()

    print("✅ Ranglisten-System bereit.", flush=True)


# =========================================================
# MATCH ERGEBNIS
# =========================================================

@bot.event
async def on_message(message):

    if message.author.bot:
        return

    # -----------------------------------------------------
    # MATCH-KANAL
    # -----------------------------------------------------

    if message.channel.name == MATCH_CHANNEL_NAME:

        mentions = message.mentions

        # -------------------------------------------------
        # KEINE / FALSCHE MENTIONS
        # -------------------------------------------------

        if len(mentions) != 2:

            await message.channel.send(
                "Ohne @ bin ich blind. Ich bin ein Bot, kein Hellseher 🔮\n"
                "⚠️ **Bitte Spieler mit @ markieren!**\n"
                "Beispiel: `@spieler vs @spieler 3:2`"
            )

            return

        mention_matches = re.findall(r"<@!?(\d+)>", message.content)

        player_a = bot.get_user(int(mention_matches[0]))
        player_b = bot.get_user(int(mention_matches[1]))

        if player_a.id == player_b.id:

            await message.channel.send(
                "⚠️ **Ein Spieler kann nicht gegen sich selbst spielen.**"
            )

            return

        # -------------------------------------------------
        # SCORE AUS NACHRICHT LESEN
        # -------------------------------------------------

        score_match = re.search(
            r"(\d+)\s*[:\-]\s*(\d+)",
            message.content
        )

        if not score_match:

            await message.channel.send(
                "⚠️ **Ergebnis nicht erkannt!**\n"
                "Beispiel: `@spieler vs @spieler 3:2`"
            )

            return

        legs_a = int(score_match.group(1))
        legs_b = int(score_match.group(2))

        if legs_a == legs_b:

            await message.channel.send(
                "⚠️ **Ein Unentschieden ist bei der Rangliste nicht möglich.**"
            )

            return

        if legs_a > 20 or legs_b > 20:

            await message.channel.send(
                "⚠️ **Das Ergebnis ist ungültig.**"
            )

            return

        # -------------------------------------------------
        # SIEGER
        # -------------------------------------------------

        if legs_a > legs_b:
            winner = player_a
        else:
            winner = player_b

        # -------------------------------------------------
        # IN GOOGLE SHEETS SPEICHERN
        # -------------------------------------------------

        datum = jetzt().strftime("%Y-%m-%d %H:%M:%S")

        ergebnis_sheet.append_row(
            [
                player_a.display_name,
                player_b.display_name,
                legs_a,
                legs_b,
                player_b.display_name,
                player_a.display_name,
                winner.display_name,
                datum,
            ],
            value_input_option="USER_ENTERED"
        )

        # -------------------------------------------------
        # TABELLE AKTUALISIEREN
        # -------------------------------------------------

        write_table()

        # -------------------------------------------------
        # DISCORD MELDUNG
        # -------------------------------------------------

        await message.channel.send(
            f"📊 **Match Update:**\n"
            f"{player_a.mention} vs {player_b.mention}\n\n"
            f"🏆 **Sieger: {winner.mention} ({legs_a}:{legs_b})**"
            if winner == player_a
            else
            f"📊 **Match Update:**\n"
            f"{player_a.mention} vs {player_b.mention}\n\n"
            f"🏆 **Sieger: {winner.mention} ({legs_a}:{legs_b})**"
        )

        return

    # =====================================================
    # ADMIN COMMANDS
    # =====================================================

    if message.content.lower().startswith(("!hilfe", "!tabelle", "!undo")):

        # Nur Admin-Chat
        if message.channel.name != ADMIN_CHANNEL_NAME:
            return

        # Nur Admins
        if not is_admin(message.author):
            return

    # -----------------------------------------------------
    # HILFE
    # -----------------------------------------------------

    if message.content.lower() == "!hilfe":

        await message.channel.send(
            "🛠️ **Mad Dog – Admin Hilfe**\n\n"
            "`!tabelle` – aktuelle Rangliste anzeigen\n"
            "`!undo` – letztes Ergebnis löschen"
        )

        return

    # -----------------------------------------------------
    # TABELLE
    # -----------------------------------------------------

    if message.content.lower() == "!tabelle":

        table = write_table()

        await message.channel.send(
            table_to_discord(table)
        )

        return

    # -----------------------------------------------------
    # UNDO
    # -----------------------------------------------------

    if message.content.lower() == "!undo":

        rows = ergebnis_sheet.get_all_values()

        if len(rows) <= 1:

            await message.channel.send(
                "⚠️ Es gibt kein Ergebnis zum Löschen."
            )

            return

        last_row = len(rows)

        deleted = rows[last_row - 1]

        if len(deleted) < 7:

            await message.channel.send(
                "⚠️ Das letzte Ergebnis konnte nicht gelesen werden."
            )

            return

        player_a = deleted[0]
        player_b = deleted[1]
        legs_a = deleted[2]
        legs_b = deleted[3]

        ergebnis_sheet.delete_rows(last_row)

        write_table()

        await message.channel.send(
            f"🗑️ **Letztes Ergebnis gelöscht!**\n\n"
            f"**{player_a} {legs_a}:{legs_b} {player_b}**\n\n"
            f"✅ Die Rangliste wurde neu berechnet."
        )

        return


# =========================================================
# START
# =========================================================

print("🚀 MAD DOG RANGLISTE STARTET...", flush=True)
print(
    f"TOKEN VORHANDEN: {bool(TOKEN)}",
    flush=True
)

bot.run(TOKEN)