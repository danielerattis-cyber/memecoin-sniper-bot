import asyncio
from telegram import Bot
import os
from dotenv import load_dotenv

load_dotenv()

async def delete_webhook():
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    await bot.delete_webhook()
    print("✅ Webhook cancellato")
    await bot.session.close()

asyncio.run(delete_webhook())
