import os
import re
import io
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
import gspread

from PIL import Image, ImageDraw, ImageFont

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

        channel = discord.utils.get(
            guild.text_channels,
            name=name
        )

        if channel:
            return channel

    return None


def is_admin(member):

    return (
        isinstance(member, discord.Member)
        and member.guild_permissions.administrator
    )


def normalize_name(name):

    return " ".join(
        name.strip().lower().split()
    )


# =========================================================
# GOOGLE-SHEETS ÜBERSCHRIFTEN
# =========================================================

def ensure_headers():

    # -----------------------------------------------------
    # ERGEBNIS
    # -----------------------------------------------------

    ergebnis_values = ergebnis_sheet.get_all_values()

    if not ergebnis_values:

        ergebnis_sheet.update(
            values=[ERGEBNIS_HEADER],
            range_name="A1:H1"
        )

    else:

        current = ergebnis_values[0]

        if len(current) < 8:

            ergebnis_sheet.update(
                values=[ERGEBNIS_HEADER],
                range_name="A1:H1"
            )

    # -----------------------------------------------------
    # TABELLE
    # -----------------------------------------------------

    tabelle_sheet.update(
        values=[TABELLE_HEADER],
        range_name="A1:I1"
    )

    # -----------------------------------------------------
    # TABELLE FINAL
    # -----------------------------------------------------

    final_sheet.update(
        values=[TABELLE_HEADER],
        range_name="A1:I1"
    )


# =========================================================
# ERGEBNISSE LESEN
# =========================================================

def get_match_rows():

    rows = ergebnis_sheet.get_all_values()

    if len(rows) <= 1:
        return []

    aktueller_monat = jetzt().strftime("%Y-%m")

    result = []

    for row_number, row in enumerate(
        rows[1:],
        start=2
    ):

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

        # -------------------------------------------------
        # NUR AKTUELLER MONAT
        # -------------------------------------------------

        if datum:

            if not datum.startswith(
                aktueller_monat
            ):
                continue

        # -------------------------------------------------
        # LEGS
        # -------------------------------------------------

        try:

            legs_a_int = int(legs_a)
            legs_b_int = int(legs_b)

        except ValueError:

            continue

        result.append(
            {
                "row": row_number,
                "spieler_a": spieler_a,
                "spieler_b": spieler_b,
                "legs_a": legs_a_int,
                "legs_b": legs_b_int,
                "winner": winner,
                "datum": datum,
            }
        )

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

        # -------------------------------------------------
        # SPIELER ANLEGEN
        # -------------------------------------------------

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

        # -------------------------------------------------
        # SPIELE
        # -------------------------------------------------

        stats[p1_key]["spiele"] += 1
        stats[p2_key]["spiele"] += 1

        # -------------------------------------------------
        # LEGS
        # -------------------------------------------------

        stats[p1_key]["legs_plus"] += legs_a
        stats[p1_key]["legs_minus"] += legs_b

        stats[p2_key]["legs_plus"] += legs_b
        stats[p2_key]["legs_minus"] += legs_a

        # -------------------------------------------------
        # SIEGER
        # -------------------------------------------------

        winner_key = normalize_name(winner)

        if winner_key == p1_key:

            stats[p1_key]["siege"] += 1
            stats[p2_key]["niederlagen"] += 1

        elif winner_key == p2_key:

            stats[p2_key]["siege"] += 1
            stats[p1_key]["niederlagen"] += 1

    # =====================================================
    # TABELLE
    # =====================================================

    table = []

    for data in stats.values():

        leg_dif = (
            data["legs_plus"]
            - data["legs_minus"]
        )

        punkte = data["siege"] * 3

        table.append(
            {
                "name": data["name"],
                "spiele": data["spiele"],
                "siege": data["siege"],
                "niederlagen": data["niederlagen"],
                "legs_plus": data["legs_plus"],
                "legs_minus": data["legs_minus"],
                "leg_dif": leg_dif,
                "punkte": punkte,
            }
        )

    # -----------------------------------------------------
    # SORTIERUNG
    # -----------------------------------------------------

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

    for rang, player in enumerate(
        table,
        start=1
    ):

        rows.append(
            [
                rang,
                player["name"],
                player["spiele"],
                player["siege"],
                player["niederlagen"],
                player["legs_plus"],
                player["legs_minus"],
                player["leg_dif"],
                player["punkte"],
            ]
        )

    # -----------------------------------------------------
    # TABELLE
    # -----------------------------------------------------

    tabelle_sheet.clear()

    tabelle_sheet.update(
        values=rows,
        range_name=f"A1:I{len(rows)}"
    )

    # -----------------------------------------------------
    # TABELLE FINAL
    # -----------------------------------------------------

    final_sheet.clear()

    final_sheet.update(
        values=rows,
        range_name=f"A1:I{len(rows)}"
    )

    return table


# =========================================================
# SCHRIFTART
# =========================================================

def get_font(size, bold=False):

    if bold:

        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ]

    else:

        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        ]

    for path in font_paths:

        if os.path.exists(path):

            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


# =========================================================
# TABELLE ALS PNG ERSTELLEN
# =========================================================

def create_table_image(table):

    # =====================================================
    # EINSTELLUNGEN
    # =====================================================

    FONT_SIZE = 16
    TITLE_FONT_SIZE = 18

    PADDING = 16
    ROW_HEIGHT = 24

    BG_COLOR = (44, 47, 51)
    HEADER_COLOR = (255, 255, 255)
    ROW_COLOR = (220, 220, 220)
    ALT_ROW_COLOR = (180, 180, 180)
    SEP_COLOR = (100, 100, 100)
    TITLE_COLOR = (255, 255, 255)

    # =====================================================
    # SCHRIFT LADEN
    # =====================================================

    import urllib.request

    font_file = "/tmp/mono.ttf"

    if not os.path.exists(font_file):
        try:
            urllib.request.urlretrieve(
                "https://github.com/google/fonts/raw/main/apache/roboto/static/RobotoMono-Regular.ttf",
                font_file
            )
        except Exception:
            font_file = None

    try:
        if font_file:
            font = ImageFont.truetype(
                font_file,
                FONT_SIZE
            )

            title_font = ImageFont.truetype(
                font_file,
                TITLE_FONT_SIZE
            )
        else:
            font = ImageFont.load_default()
            title_font = font

    except Exception:
        font = ImageFont.load_default()
        title_font = font

    # =====================================================
    # SPALTEN
    # =====================================================

    columns = [
        "Rg",
        "Name",
        "Sp",
        "S",
        "N",
        "L+",
        "L-",
        "Dif",
        "Pkt"
    ]

    column_widths = [
        35,
        180,
        35,
        35,
        35,
        45,
        45,
        45,
        45
    ]

    width = (
        sum(column_widths)
        + PADDING * 2
    )

    # =====================================================
    # HÖHE
    # =====================================================

    number_of_rows = len(table)

    height = (
        PADDING
        + 30
        + ROW_HEIGHT
        + 4
        + (number_of_rows * ROW_HEIGHT)
        + PADDING
    )

    # =====================================================
    # BILD ERSTELLEN
    # =====================================================

    image = Image.new(
        "RGB",
        (width, height),
        BG_COLOR
    )

    draw = ImageDraw.Draw(image)

    y = PADDING

    # =====================================================
    # TITEL
    # =====================================================

    datum = jetzt().strftime(
        "%d.%m.%Y %H:%M"
    )

    title = (
        f"Aktuelle Tabelle  {datum} Uhr"
    )

    draw.text(
        (PADDING, y),
        title,
        font=title_font,
        fill=TITLE_COLOR
    )

    y += 30

    # =====================================================
    # HEADER
    # =====================================================

    x = PADDING

    for column, column_width in zip(
        columns,
        column_widths
    ):

        draw.text(
            (x, y),
            column,
            font=font,
            fill=HEADER_COLOR
        )

        x += column_width

    y += ROW_HEIGHT

    # =====================================================
    # TRENNLINIE
    # =====================================================

    draw.line(
        [
            (PADDING, y),
            (width - PADDING, y)
        ],
        fill=SEP_COLOR,
        width=1
    )

    y += 4

    # =====================================================
    # SPIELER
    # =====================================================

    for index, player in enumerate(table):

        # Abwechselnde Zeilenfarbe
        if index % 2 == 0:
            text_color = ALT_ROW_COLOR
        else:
            text_color = ROW_COLOR

        values = [
            str(index + 1),
            str(player["name"]),
            str(player["spiele"]),
            str(player["siege"]),
            str(player["niederlagen"]),
            str(player["legs_plus"]),
            str(player["legs_minus"]),
            str(player["leg_dif"]),
            str(player["punkte"]),
        ]

        x = PADDING

        for value, column_width in zip(
            values,
            column_widths
        ):

            draw.text(
                (x, y),
                value,
                font=font,
                fill=text_color
            )

            x += column_width

        y += ROW_HEIGHT

    # =====================================================
    # PNG
    # =====================================================

    output = io.BytesIO()

    image.save(
        output,
        format="PNG"
    )

    output.seek(0)

    return output


# =========================================================
# TABELLENBILD SENDEN
# =========================================================

async def send_table_image(channel):

    table = write_table()

    image_data = create_table_image(
        table
    )

    await channel.send(
        file=discord.File(
            image_data,
            filename="rangliste.png"
        )
    )


# =========================================================
# AUTOMATISCHE TABELLE
# =========================================================

async def update_discord_table():

    channel = get_channel(
        TABLE_CHANNEL_NAME
    )

    if not channel:

        print(
            f"⚠️ Kanal #{TABLE_CHANNEL_NAME} "
            f"nicht gefunden.",
            flush=True
        )

        return

    await send_table_image(
        channel
    )


# =========================================================
# MONATSARCHIV
# =========================================================

def get_previous_year_month():

    now = jetzt()

    year = now.year
    month = now.month - 1

    if month == 0:
        month = 12
        year -= 1

    return year, month


def archive_current_month():

    year, month = (
        get_previous_year_month()
    )

    archive_name = (
        f"Archiv_{year}_{month:02d}"
    )

    existing_names = [
        ws.title
        for ws in spreadsheet.worksheets()
    ]

    if archive_name in existing_names:

        return archive_name

    old_values = (
        ergebnis_sheet.get_all_values()
    )

    archive_sheet = (
        spreadsheet.add_worksheet(
            title=archive_name,
            rows=max(
                len(old_values) + 5,
                10
            ),
            cols=8
        )
    )

    if old_values:

        archive_sheet.update(
            values=old_values,
            range_name=f"A1:H{len(old_values)}"
        )

    return archive_name


# =========================================================
# MONATSNAME
# =========================================================

def get_previous_month_name():

    year, month = (
        get_previous_year_month()
    )

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

    return (
        f"{months[month - 1]} {year}"
    )


# =========================================================
# VORMONATS-ARCHIV
# =========================================================

def get_previous_month_archive():

    year, month = (
        get_previous_year_month()
    )

    archive_name = (
        f"Archiv_{year}_{month:02d}"
    )

    try:

        return spreadsheet.worksheet(
            archive_name
        )

    except gspread.WorksheetNotFound:

        return None


# =========================================================
# ARCHIV-TABELLE BERECHNEN
# =========================================================

def calculate_archive_table(
    archive_sheet
):

    if not archive_sheet:
        return []

    rows = (
        archive_sheet.get_all_values()
    )

    stats = {}

    for row in rows[1:]:

        if len(row) < 7:
            continue

        p1 = row[0].strip()
        p2 = row[1].strip()

        if not p1 or not p2:
            continue

        try:

            legs_a = int(row[2])
            legs_b = int(row[3])

        except ValueError:

            continue

        winner = row[6].strip()

        for player in [p1, p2]:

            key = normalize_name(
                player
            )

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

        winner_key = normalize_name(
            winner
        )

        if winner_key == p1_key:

            stats[p1_key]["siege"] += 1
            stats[p2_key]["niederlagen"] += 1

        elif winner_key == p2_key:

            stats[p2_key]["siege"] += 1
            stats[p1_key]["niederlagen"] += 1

    table = []

    for data in stats.values():

        leg_dif = (
            data["legs_plus"]
            - data["legs_minus"]
        )

        punkte = data["siege"] * 3

        table.append(
            {
                "name": data["name"],
                "spiele": data["spiele"],
                "siege": data["siege"],
                "niederlagen": data["niederlagen"],
                "legs_plus": data["legs_plus"],
                "legs_minus": data["legs_minus"],
                "leg_dif": leg_dif,
                "punkte": punkte,
            }
        )

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
# MONATSSIEGER POSTEN
# =========================================================

async def send_month_winner():

    archive_sheet = (
        get_previous_month_archive()
    )

    if not archive_sheet:

        print(
            "⚠️ Kein Monatsarchiv gefunden.",
            flush=True
        )

        return

    table = calculate_archive_table(
        archive_sheet
    )

    if not table:
        return

    top3 = table[:3]

    month_name = (
        get_previous_month_name()
    )

    text = (
        f"🏆 **MONATSSIEGER – "
        f"{month_name.upper()}**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    emojis = [
        "🥇",
        "🥈",
        "🥉"
    ]

    for index, player in enumerate(top3):

        text += (
            f"{emojis[index]} "
            f"**{player['name']}**\n"
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
        f"👑 **Monatssieger: "
        f"{winner['name']}!**\n"
        f"⭐ **{winner['punkte']} Punkte**\n"
        "🎯 Herzlichen Glückwunsch! 🔥"
    )

    channel = get_channel(
        INFO_CHANNEL_NAME
    )

    if channel:

        await channel.send(text)


# =========================================================
# MONATSRESET
# =========================================================

async def monthly_reset():

    print(
        "🔄 Monatsreset wird ausgeführt...",
        flush=True
    )

    # -----------------------------------------------------
    # VORMONAT ARCHIVIEREN
    # -----------------------------------------------------

    archive_current_month()

    # -----------------------------------------------------
    # TOP 3 POSTEN
    # -----------------------------------------------------

    await send_month_winner()

    # -----------------------------------------------------
    # ERGEBNIS LEEREN
    # -----------------------------------------------------

    ergebnis_sheet.clear()

    ergebnis_sheet.update(
        values=[ERGEBNIS_HEADER],
        range_name="A1:H1"
    )

    # -----------------------------------------------------
    # TABELLE LEEREN
    # -----------------------------------------------------

    tabelle_sheet.clear()

    tabelle_sheet.update(
        values=[TABELLE_HEADER],
        range_name="A1:I1"
    )

    # -----------------------------------------------------
    # TABELLE FINAL LEEREN
    # -----------------------------------------------------

    final_sheet.clear()

    final_sheet.update(
        values=[TABELLE_HEADER],
        range_name="A1:I1"
    )

    print(
        "✅ Monatsreset abgeschlossen.",
        flush=True
    )


# =========================================================
# TEST-MONATSSIEGER
# =========================================================

async def send_test_month_winner(table):

    if not table:
        return

    top3 = table[:3]

    now = jetzt()

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

    month_name = f"{months[now.month - 1]} {now.year}"

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

    # =====================================================
    # MONATSRESET 00:00
    # =====================================================

    if (
        now.day == 1
        and now.hour == 0
        and now.minute == 0
    ):

        if last_reset_month != current_month:

            await monthly_reset()

            last_reset_month = current_month

            await asyncio.sleep(2)

            return

    # =====================================================
    # TABELLE 08 / 12 / 18 / 22
    # =====================================================

    update_hours = {
        8,
        12,
        18,
        22
    }

    if (
        now.hour in update_hours
        and now.minute == 0
    ):

        update_key = now.strftime(
            "%Y-%m-%d-%H"
        )

        if last_table_update != update_key:

            await update_discord_table()

            last_table_update = update_key


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():

    print(
        f"🤖 Mad Dog online: {bot.user}",
        flush=True
    )

    ensure_headers()

    if not scheduler.is_running():

        scheduler.start()

    print(
        "✅ Ranglisten-System bereit.",
        flush=True
    )


# =========================================================
# MATCH-ERGEBNIS
# =========================================================

@bot.event
async def on_message(message):

    # -----------------------------------------------------
    # BOT-NACHRICHTEN IGNORIEREN
    # -----------------------------------------------------

    if message.author.bot:
        return

    # =====================================================
    # MATCH-KANAL
    # =====================================================

    if message.channel.name == MATCH_CHANNEL_NAME:

        content = message.content.strip()

        # -------------------------------------------------
        # MENTIONS AUS ORIGINALNACHRICHT
        # -------------------------------------------------

        mention_matches = re.findall(
            r"<@!?(\d+)>",
            content
        )

        # -------------------------------------------------
        # GENAU 2 SPIELER
        # -------------------------------------------------

        if len(mention_matches) != 2:

            await message.channel.send(
                "Ohne @ bin ich blind. "
                "Ich bin ein Bot, kein Hellseher 🔮\n"
                "⚠️ **Bitte Spieler mit @ markieren!**\n"
                "Beispiel: "
                "`@spieler vs @spieler 3:2`"
            )

            return

        # -------------------------------------------------
        # SPIELER HOLEN
        # -------------------------------------------------

        player_a = bot.get_user(
            int(mention_matches[0])
        )

        player_b = bot.get_user(
            int(mention_matches[1])
        )

        # Falls nicht im Cache
        if player_a is None:

            player_a = message.guild.get_member(
                int(mention_matches[0])
            )

        if player_b is None:

            player_b = message.guild.get_member(
                int(mention_matches[1])
            )

        if (
            player_a is None
            or player_b is None
        ):

            await message.channel.send(
                "⚠️ **Spieler konnte nicht gefunden werden.**"
            )

            return

        # -------------------------------------------------
        # GLEICHER SPIELER
        # -------------------------------------------------

        if player_a.id == player_b.id:

            await message.channel.send(
                "⚠️ **Ein Spieler kann nicht "
                "gegen sich selbst spielen.**"
            )

            return

        # =================================================
        # FORMAT PRÜFEN
        # =================================================

        format_match = re.search(
            r"<@!?\d+>\s+vs\s+<@!?\d+>\s+"
            r"(\d+)\s*[:\-]\s*(\d+)",
            content,
            re.IGNORECASE
        )

        if not format_match:

            await message.channel.send(
                "⚠️ **Bitte genau dieses Format verwenden:**\n"
                "`@spieler vs @spieler 3:2`"
            )

            return

        # -------------------------------------------------
        # SCORE
        # -------------------------------------------------

        legs_a = int(
            format_match.group(1)
        )

        legs_b = int(
            format_match.group(2)
        )

        # -------------------------------------------------
        # UNENTSCHIEDEN
        # -------------------------------------------------

        if legs_a == legs_b:

            await message.channel.send(
                "⚠️ **Ein Unentschieden ist "
                "bei der Rangliste nicht möglich.**"
            )

            return

        # -------------------------------------------------
        # MAXIMUM
        # -------------------------------------------------

        if (
            legs_a > 20
            or legs_b > 20
        ):

            await message.channel.send(
                "⚠️ **Das Ergebnis ist ungültig.**"
            )

            return

        # =================================================
        # SIEGER
        # =================================================

        if legs_a > legs_b:

            winner = player_a

        else:

            winner = player_b

        # =================================================
        # DATUM
        # =================================================

        datum = jetzt().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # =================================================
        # GOOGLE SHEETS
        # =================================================

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

        # =================================================
        # TABELLE NEU BERECHNEN
        # =================================================

        write_table()

        # =================================================
        # MATCH UPDATE
        # =================================================

        await message.channel.send(
            f"📊 **Match Update:**\n"
            f"{player_a.mention} vs "
            f"{player_b.mention}\n\n"
            f"🏆 **Sieger: "
            f"{winner.mention} "
            f"({legs_a}:{legs_b})**"
        )

        return

    # =====================================================
    # ADMIN COMMANDS
    # =====================================================

    command = message.content.strip().lower()

    if command in (
        "!hilfe",
        "!tabelle",
        "!undo",
        "!resettest"
    ):

        # -------------------------------------------------
        # NUR ADMIN-CHAT
        # -------------------------------------------------

        if message.channel.name != ADMIN_CHANNEL_NAME:
            return

        # -------------------------------------------------
        # NUR ADMINS
        # -------------------------------------------------

        if not is_admin(message.author):
            return

        # =================================================
        # RESETTEST
        # =================================================

        if command == "!resettest":

            print(
                "🧪 TEST-MONATSRESET wird ausgeführt...",
                flush=True
            )

            # Aktuellen Monat nur auswerten
            # NICHT löschen und NICHT zurücksetzen
            table = calculate_table()

            if table:

                top3 = table[:3]

                text = (
                    "🧪 **TEST – MONATSSIEGER**\n"
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

                info_channel = get_channel(
                    INFO_CHANNEL_NAME
                )

                if info_channel:
                    await info_channel.send(text)

            else:

                await message.channel.send(
                    "⚠️ Keine Ergebnisse im aktuellen Monat vorhanden."
                )

            await message.channel.send(
                "🧪 **Test-Monatsreset abgeschlossen.**\n"
                "✅ Die aktuelle Rangliste und Ergebnisse wurden "
                "**nicht verändert**."
            )

            return

        # =================================================
        # HILFE
        # =================================================

        if command == "!hilfe":

            await message.channel.send(
                "🛠️ **Mad Dog – Admin Hilfe**\n\n"
                "`!tabelle` – aktuelle Rangliste anzeigen\n"
                "`!undo` – letztes Ergebnis löschen\n"
                "`!resettest` – Monatsreset testen"
            )

            return

        # =================================================
        # TABELLE
        # =================================================

        if command == "!tabelle":

            await send_table_image(
                message.channel
            )

            return

        # =================================================
        # UNDO
        # =================================================

        if command == "!undo":

            rows = ergebnis_sheet.get_all_values()

            if len(rows) <= 1:

                await message.channel.send(
                    "⚠️ **Es gibt kein Ergebnis "
                    "zum Löschen.**"
                )

                return

            last_row = len(rows)

            deleted = rows[
                last_row - 1
            ]

            if len(deleted) < 7:

                await message.channel.send(
                    "⚠️ **Das letzte Ergebnis "
                    "konnte nicht gelesen werden.**"
                )

                return

            player_a = deleted[0]
            player_b = deleted[1]
            legs_a = deleted[2]
            legs_b = deleted[3]

            # -------------------------------------------------
            # LETZTE ZEILE LÖSCHEN
            # -------------------------------------------------

            ergebnis_sheet.delete_rows(
                last_row
            )

            # -------------------------------------------------
            # TABELLE NEU BERECHNEN
            # -------------------------------------------------

            write_table()

            # -------------------------------------------------
            # BESTÄTIGUNG
            # -------------------------------------------------

            await message.channel.send(
                f"🗑️ **Letztes Ergebnis gelöscht!**\n\n"
                f"**{player_a} "
                f"{legs_a}:{legs_b} "
                f"{player_b}**\n\n"
                f"✅ Die Rangliste wurde "
                f"neu berechnet."
            )

            return


# =========================================================
# START
# =========================================================

print(
    "🚀 MAD DOG RANGLISTE STARTET...",
    flush=True
)

print(
    f"TOKEN VORHANDEN: {bool(TOKEN)}",
    flush=True
)

bot.run(TOKEN)