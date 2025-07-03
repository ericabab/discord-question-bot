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
    # 取得最近10筆問題，依照插入時間排序
    cursor = collection.find().sort("created_at", -1).limit(10)
    questions = [doc['question'] for doc in cursor]
    return list(reversed(questions))  # 反轉成舊到新

def save_question(question):
    today = datetime.now(pytz.timezone("Asia/Taipei"))
    doc = {
        "question": question,
        "created_at": today.now() #datetime.utcnow()
    }
    collection.insert_one(doc)
    count = collection.count_documents({})
    if count > 100:
        # 找出多餘的舊資料數量
        to_delete = count - 100
        # 依照created_at排序，取得最舊的 to_delete 筆
        old_docs = collection.find().sort("created_at", 1).limit(to_delete)
        old_ids = [doc["_id"] for doc in old_docs]
        collection.delete_many({"_id": {"$in": old_ids}})

# 用暱稱找目標使用者
TARGET_DISPLAY_NAMES = ["咪葛格", "珊"]
TARGET_USER_IDS = []
user_answers = {}
current_question = ""



# Discord Client 與 Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # 必須開啟
client = discord.Client(intents=intents)

# OpenAI 客戶端
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Scheduler 設定（台灣時區）
scheduler = AsyncIOScheduler(timezone="Asia/Taipei")



class AnswerButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💬 回答", style=discord.ButtonStyle.primary)
    async def answer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in TARGET_USER_IDS:
            await interaction.response.send_message("你不是這題的目標對象喔 🙅", ephemeral=True)
            return

        await interaction.response.send_message("請用私訊回覆你的答案喔！", ephemeral=True)
        await interaction.user.send(f"請回答問題：\n{current_question}")

async def ask_question():
    global current_question, user_answers
    user_answers.clear()

    history_questions = load_history()
    history_text = "\n".join(f"- {q}" for q in history_questions) if history_questions else "無"

    # 取得今天日期
    today = datetime.now(pytz.timezone("Asia/Taipei"))
    day = today.day
    type_index = day % 7

    

    # 建立 prompt（會請 ChatGPT 自行根據 type_index 選擇提問類型）
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

    請產生一個與常見提問（如「你小時候有沒有什麼特別的夢想？」）**不同的、有變化的**新問題。
    請確保問題是開放性的，並且適合情侶之間的互動與了解。
    請確保問題的長度不超過 50 個字。
    請只輸出產生出來的**單一問題**本身，不要列出類型名稱、說明文字、解釋、代號或多個選項。
    """

    print(prompt)
    print(history_text)
    
    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=100,
        temperature=1.2
    )
    
    current_question = response.choices[0].message.content.strip()
    # 將新問題存入歷史檔案
    save_question(current_question)

    channel = client.get_channel(CHANNEL_ID)
    await channel.send(
        f"🧠 ChatGPT 提問時間到了！\n**{current_question}**\n👇 請點下方按鈕回答：",
        view=AnswerButton()
    )

@client.event
async def on_message(message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
        if message.author.id in TARGET_USER_IDS:
            user_answers[message.author.id] = message.content
            await message.channel.send("✅ 回答已記錄！")

            if all(uid in user_answers for uid in TARGET_USER_IDS):
                channel = client.get_channel(CHANNEL_ID)
                await channel.send(
                    f"🎉 兩位目標用戶都回覆了！\n"
                    f"🔔 問題是：**{current_question}**"
                )
                # 若要公布答案，取消下面註解
                for uid in TARGET_USER_IDS:
                    user = await client.fetch_user(uid)
                    await channel.send(f"📝 {user.display_name} 的回答：{user_answers[uid]}")
                user_answers.clear()

@client.event
async def on_ready():
    print(f"✅ 已登入為 {client.user}")

    # 找到目標成員的 ID
    guild = discord.utils.get(client.guilds)
    async for member in guild.fetch_members(limit=None):
        if member.display_name in TARGET_DISPLAY_NAMES:
            print(f"✅ 找到 {member.display_name} 的 ID：{member.id}")
            TARGET_USER_IDS.append(member.id)

    if len(TARGET_USER_IDS) < len(TARGET_DISPLAY_NAMES):
        print("⚠️ 有些目標使用者沒有成功找到，請檢查暱稱是否正確。")

    scheduler.add_job(ask_question, trigger='cron', hour=20, minute=0)  # 台灣時間 晚上8點
    scheduler.start()

    # 登入時立刻發問一次
    # await ask_question()

client.run(TOKEN)
