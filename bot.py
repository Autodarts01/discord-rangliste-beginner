import os
import discord
import gspread

from discord.ext import commands
from google.oauth2.service_account import Credentials


# Google Sheets
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

sheet = gc.open_by_key(
    os.environ["GOOGLE_SHEET_ID"]
).sheet1


# Discord
TOKEN = os.environ["DISCORD_TOKEN"]

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


@bot.event
async def on_ready():
    print(f"Bot online: {bot.user}")


@bot.command()
async def punkte(ctx, spieler: str, punkte: int):

    records = sheet.get_all_records()

    for row_number, row in enumerate(records, start=2):

        if str(row["Spieler"]).lower() == spieler.lower():

            alte_punkte = int(row["Punkte"])
            neue_punkte = alte_punkte + punkte

            sheet.update_cell(
                row_number,
                2,
                neue_punkte
            )

            await ctx.send(
                f"✅ {spieler} hat jetzt {neue_punkte} Punkte."
            )
            return

    sheet.append_row([spieler, punkte])

    await ctx.send(
        f"✅ {spieler} wurde mit {punkte} Punkten eingetragen."
    )


@bot.command()
async def rangliste(ctx):

    records = sheet.get_all_records()

    if not records:
        await ctx.send("Die Rangliste ist noch leer.")
        return

    records.sort(
        key=lambda x: int(x["Punkte"]),
        reverse=True
    )

    text = "🏆 **RANGLISTE**\n\n"

    for position, row in enumerate(records, start=1):
        text += (
            f"**{position}.** "
            f"{row['Spieler']} — "
            f"{row['Punkte']} Punkte\n"
        )

    await ctx.send(text)


bot.run(TOKEN)