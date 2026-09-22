import asyncio
import os
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()

async def cleanup():
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    try:
        # Elimina webhook
        await bot.delete_webhook(drop_pending_updates=True)
        print("✅ Webhook eliminato")
    except Exception as e:
        print(f"⚠️ Webhook error: {e}")
    
    await bot.session.close()

asyncio.run(cleanup())
