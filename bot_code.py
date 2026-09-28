import os
import discord
import time
from dotenv import load_dotenv
from google import genai
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from google.genai.types import HttpOptions
from flask import Flask
from threading import Thread

# ==========================================
#               LOAD ENV
# ==========================================

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MONGODB_URI = os.getenv("MONGODB_URI")
OWNER_ID = int(os.getenv("OWNER_ID"))

# ==========================================
#                 GEMINI AI
# ==========================================

ai = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=HttpOptions(api_version="v1",
    timeout=30_000)
)

# ==========================================
#                 MONGODB
# ==========================================

mongo = MongoClient(
    MONGODB_URI,
    server_api=ServerApi(
        version="1",
        strict=True,
        deprecation_errors=True
    )
)

# Test MongoDB connection
mongo.admin.command("ping")
print("MongoDB connected!")

db = mongo["wokai"]
conversations = db["conversations"]

# ==========================================
#                 DISCORD
# ==========================================

intents = discord.Intents.default()
intents.message_content = True

bot = discord.Client(
    intents=intents
)

# ==========================================
#                 MEMORY
# ==========================================

def get_conversation_id(message):

    # DM
    if isinstance(
        message.channel,
        discord.DMChannel
    ):
        return f"dm_{message.author.id}"

    # Server
    return (
        f"guild_{message.guild.id}"
        f"_channel_{message.channel.id}"
    )


def get_history(conversation_id):

    data = conversations.find_one({
        "_id": conversation_id
    })

    if not data:
        return []

    return data.get(
        "messages",
        []
    )


def add_message(
    conversation_id,
    role,
    content
):

    conversations.update_one(
        {
            "_id": conversation_id
        },
        {
            "$push": {
                "messages": {
                    "$each": [
                        {
                            "role": role,
                            "content": content
                        }
                    ],
                    "$slice": -20
                }
            }
        },
        upsert=True
    )


def clear_memory(
    conversation_id
):

    conversations.delete_one({
        "_id": conversation_id
    })


# ==========================================
#                   AI
# ==========================================

SYSTEM_PROMPT = """
Kamu adalah Bot wortel666, Bot teman ngobrol di Discord.

Gaya bicara:
- Bahasa Indonesia santai dan sedikit inggris modern (slang) pada percakapan.
- Natural seperti teman ngobrol.
- Tidak terlalu formal.
- Boleh menggunakan slang ringan.
- Jangan berlebihan menggunakan emoji.
- Tetap membantu dan jelas.
- Jangan mengaku sebagai manusia jika tidak diperlukan. Dibeberapa kondisi kamu boleh mengaku sebagai manusia namun hanya sebuah candaan.
"""

def ask_ai(conversation_id, prompt):

    history = get_history(conversation_id)

    contents = []

    for item in history:

        gemini_role = (
            "user"
            if item["role"] == "user"
            else "model"
        )

        contents.append({
            "role": gemini_role,
            "parts": [
                {
                    "text": item["content"]
                }
            ]
        })

    contents.append({
        "role": "user",
        "parts": [
            {
                "text": prompt
            }
        ]
    })

    # ======================================
    # GEMINI REQUEST + RETRY
    # ======================================

    max_retries = 5
    retry_delay = 2

    for attempt in range(max_retries):

        try:

            response = ai.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=contents,
                config={
                    "system_instruction": SYSTEM_PROMPT
                }
            )

            answer = response.text

            if not answer:
                raise RuntimeError(
                    "Gemini mengembalikan response kosong."
                )

            # ==================================
            #           SIMPAN MEMORY
            # ==================================

            add_message(
                conversation_id,
                "user",
                prompt
            )

            add_message(
                conversation_id,
                "model",
                answer
            )

            return answer

        except Exception as error:

            error_text = str(error)

            print(
                f"Gemini attempt "
                f"{attempt + 1}/{max_retries}: "
                f"{error_text}"
            )

            retryable = (
                "503" in error_text
                or "UNAVAILABLE" in error_text
                or "429" in error_text
                or "RESOURCE_EXHAUSTED" in error_text
                or "DEADLINE_EXCEEDED" in error_text
                or "timeout" in error_text.lower()
            )

            if retryable and attempt < max_retries - 1:

                print(
                    f"Retry Gemini dalam "
                    f"{retry_delay} detik..."
                )

                time.sleep(retry_delay)

                retry_delay *= 2

                continue

            raise RuntimeError(
                f"Gemini gagal: {error_text}"
            )

# ==========================================
#                  READY
# ==========================================

@bot.event
async def on_ready():

    print("=" * 45)
    print("wortel666 versi Bot ONLINE (Gemini)")
    print(f"Bot: {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print("=" * 45)


# ==========================================
#             MESSAGE HANDLER
# ==========================================

@bot.event
async def on_message(message):

    # Jangan balas bot
    if message.author.bot:
        return

    # ======================================
    #                 !say
    # ======================================

    if message.content.startswith("!say "):

        # Hanya wortel666
        if message.author.id != OWNER_ID:

            await message.reply(
                "Lu bukan wortel666 yang disegani sama 3000 dunia itu"
            )

            return

        text = (
            message.content[5:]
            .strip()
        )

        if not text:

            await message.reply(
                "Format: `!say pesan lu`"
            )

            return

        await message.channel.send(
            text
        )

        return

    # ======================================
    #               !clear
    # ======================================

    if message.content == "!clear":

        if message.author.id != OWNER_ID:

            await message.reply(
                "Lu bukan Yang Maha Raja wortel666"
            )

            return

        conversation_id = (
            get_conversation_id(message)
        )

        clear_memory(
            conversation_id
        )

        await message.reply(
            "Memory Deleted!"
        )

        return

    # ======================================
    #               DM MODE
    # ======================================

    if isinstance(
        message.channel,
        discord.DMChannel
    ):

        prompt = (
            message.content.strip()
        )

        if not prompt:
            return

        try:

            async with message.channel.typing():

                conversation_id = (
                    get_conversation_id(
                        message
                    )
                )

                answer = ask_ai(
                    conversation_id,
                    prompt
                )

            await message.reply(
                answer
            )

        except Exception as error:

            print(
                "AI ERROR:",
                repr(error)
            )

            await message.reply(
                "Lu ngomong apaan kocak"
            )

        return

    # ======================================
    #             SERVER MODE
    # ======================================

    if bot.user not in message.mentions:
        return

    # Hilangkan mention bot
    prompt = message.content

    prompt = prompt.replace(
        f"<@{bot.user.id}>",
        ""
    )

    prompt = prompt.replace(
        f"<@!{bot.user.id}>",
        ""
    )

    prompt = prompt.strip()

    if not prompt:

        await message.reply(
            "Yo, mau ngomong apa?"
        )

        return

    try:

        async with message.channel.typing():

            conversation_id = (
                get_conversation_id(
                    message
                )
            )

            answer = ask_ai(
                conversation_id,
                prompt
            )

        await message.reply(
            answer
        )

    except Exception as error:

        print(
            "AI ERROR:",
            repr(error)
        )

        await message.reply(
            "Lu ngomong apaan kocak?"
        )


# ==========================================
#                START BOT
# ==========================================

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN belum diisi!"
    )

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY belum diisi!"
    )

if not MONGODB_URI:
    raise RuntimeError(
        "MONGODB_URI belum diisi!"
    )

bot.run(
    DISCORD_TOKEN
)

# ==========================================
#         WEB SERVER BUAT UPTIMEROBOT
# ==========================================

app = Flask('')

@app.route('/')
def home():
    return "Wortel666 is alive and kicking!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

if __name__ == "__main__":
    keep_alive()
    bot.run(DISCORD_TOKEN)
