import discord
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from openai import OpenAI
from datetime import datetime
import pytz
from pymongo import MongoClient

# 環境變數設定
TOKEN = os.environ['DISCORD_BOT_TOKEN']
CHANNEL_ID = int(os.environ['DISCORD_CHANNEL_ID'])
OPENAI_API_KEY = os.environ['OPENAI_API_KEY']
MONGODB_URI = os.environ['MONGODB_URI']

DATABASE_NAME = "discord_bot"
COLLECTION_NAME = "questions_history"

mongo_client = MongoClient(MONGODB_URI)
db = mongo_client[DATABASE_NAME]
collection = db[COLLECTION_NAME]

def load_history():
    cursor = collection.find().sort("created_at", -1).limit(100)
    questions = [doc['question'] for doc in cursor]
    return list(reversed(questions))

def save_question(question):
    today = datetime.now(pytz.timezone("Asia/Taipei"))
    doc = {
        "question": question,
        "created_at": today
    }
    collection.insert_one(doc)
    count = collection.count_documents({})
    if count > 100:
        to_delete = count - 100
        old_docs = collection.find().sort("created_at", 1).limit(to_delete)
        old_ids = [doc["_id"] for doc in old_docs]
        collection.delete_many({"_id": {"$in": old_ids}})

TARGET_DISPLAY_NAMES = ["咪葛格", "珊"]
TARGET_USER_IDS = []
user_answers = {}
waiting_users = set()
current_question = ""

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)
openai_client = OpenAI(api_key=OPENAI_API_KEY)
scheduler = AsyncIOScheduler(timezone="Asia/Taipei")

class AnswerButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="\U0001F4AC 回答", style=discord.ButtonStyle.primary)
    async def answer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in TARGET_USER_IDS:
            await interaction.response.send_message("你不是這題的目標對象喔 \U0001F645", ephemeral=True)
            return

        if interaction.user.id in waiting_users:
            await interaction.response.send_message("你已經在等待回答中，請到私訊完成回答 ✅", ephemeral=True)
            return

        waiting_users.add(interaction.user.id)
        await interaction.response.send_message("請到你的私訊中回答這個問題 \U0001F447", ephemeral=True)

        try:
            dm = await interaction.user.create_dm()
            await dm.send(f"請回答問題：\n**{current_question}**\n請直接回覆這則訊息。")

            def check(msg):
                return (
                    msg.author == interaction.user and
                    isinstance(msg.channel, discord.DMChannel) and
                    not msg.author.bot and
                    msg.author.id in waiting_users
                )

            msg = await client.wait_for("message", check=check, timeout=3600)
            waiting_users.remove(interaction.user.id)
            user_answers[interaction.user.id] = msg.content
            await msg.channel.send("✅ 回答已記錄！")

            if all(uid in user_answers for uid in TARGET_USER_IDS):
                channel = client.get_channel(CHANNEL_ID)
                await channel.send(f"\U0001F389 兩位目標用戶都回覆了！\n\U0001F514 問題是：**{current_question}**")
                for uid in TARGET_USER_IDS:
                    user = await client.fetch_user(uid)
                    await channel.send(f"📝 {user.display_name} 的回答：{user_answers[uid]}")
                user_answers.clear()

        except asyncio.TimeoutError:
            waiting_users.remove(interaction.user.id)
            await dm.send("⌛ 回覆超時，請下次準時回答")

async def ask_question():
    global current_question, user_answers
    user_answers.clear()

    history_questions = load_history()
    history_text = "\n".join(f"- {q}" for q in history_questions) if history_questions else "無"
    today = datetime.now(pytz.timezone("Asia/Taipei"))
    day = today.day
    type_index = day % 7

    prompt = f"""
    今天是 {today.month} 月 {today.day} 日，今天的日 = {day}，因此 type_index = {type_index}。

    以下是我們之前問過的問題，請避免產生重複或過於類似的問題：
    {history_text}

    請根據以下類型的對應關係，選擇類型 {type_index}，並隨機生成一題適合情侶每日互相了解的提問問題，使用繁體中文。

    類型如下：
    0. 回憶與關係互動（例如：我們一起做過最難忘的一件事是什麼？）
    1. 喜好與價值觀（例如：你最喜歡的放鬆方式是什麼？）
    2. 假設性情境（例如：如果我們中了一億，你會怎麼用？）
    3. 社會或世界觀（例如：你覺得什麼樣的生活才算是成功？）
    4. 夢想與未來展望（例如：未來五年內，你最想嘗試的新事物是什麼？）
    5. 愛與關係的看法（例如：你覺得我們之間最重要的是什麼？）
    6. 輕鬆趣味題（例如：如果我們是卡通角色，你覺得是哪一對？）

    請產生一個與常見提問（如「你小時候有沒有什麼特別的夢想？」）不同的、有變化的問題。
    確保問題是開放性的，適合情侶互動與了解，長度不超過 50 個字。
    僅輸出問題本身，不要列出解釋、代號、標題或多個選項。
    """
    
    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=100,
        temperature=1.2
    )

    current_question = response.choices[0].message.content.strip()
    save_question(current_question)

    channel = client.get_channel(CHANNEL_ID)
    await channel.send(
        f"🧠 ChatGPT 提問時間到了！\n**{current_question}**\n👇 請點下方按鈕回答：",
        view=AnswerButton()
    )

@client.event
async def on_ready():
    print(f"✅ 已登入為 {client.user}")
    guild = discord.utils.get(client.guilds)
    async for member in guild.fetch_members(limit=None):
        if member.display_name in TARGET_DISPLAY_NAMES:
            print(f"✅ 找到 {member.display_name} 的 ID：{member.id}")
            TARGET_USER_IDS.append(member.id)

    if len(TARGET_USER_IDS) < len(TARGET_DISPLAY_NAMES):
        print("⚠️ 有些目標使用者沒有成功找到，請檢查暱稱是否正確。")

    scheduler.add_job(ask_question, trigger='cron', hour=20, minute=0)
    scheduler.start()
    await ask_question()

client.run(TOKEN)
