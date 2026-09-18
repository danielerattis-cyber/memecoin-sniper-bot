"""
🚀 BOT RELIABLE MODE: WebSocket diretto a Solana RPC (affidabile).

- Usa sniper_reliable.py (WebSocket robusto)
- Acquisto DIRETTO via Pump.fun (Zero Jupiter API delay)
- Rilevamento real-time
"""
import asyncio
import getpass
import logging
from solana.rpc.async_api import AsyncClient
from config import AppConfig, SettingsStore
from sniper_reliable import ReliablePoolListener, TokenEvent
from telegram_bot import SniperTelegramBot
from trader_raydium_direct import RaydiumSwapTrader as DirectPumpTrader
from solders.keypair import Keypair
from mnemonic import Mnemonic
import nacl.signing
import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("telegram.vendor.ptb_urllib3.urllib3").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

logger = logging.getLogger("reliable")


async def handle_token_reliable(event: TokenEvent, bot: SniperTelegramBot, trader: DirectPumpTrader, session: aiohttp.ClientSession):
    """Handler: compra token rilevato direttamente su Pump.fun."""
    
    logger.info("🚀 RELIABLE BUY DIRETTO su %s", event.mint)
    
    chat_ids = list(bot._all_chat_ids())
    logger.info("🔍 Chat IDs: %s", chat_ids)
    
    if not chat_ids:
        logger.error("❌ Nessun chat_id! Fare /start nel bot Telegram")
        return
    
    for chat_id_str in chat_ids:
        chat_id = int(chat_id_str)
        settings = bot.settings_store.get(chat_id)
        
        logger.info("🔍 Chat %s - Auto snipe: %s", chat_id, settings.auto_snipe_enabled)
        
        if not settings.auto_snipe_enabled:
            logger.debug("Auto snipe OFF")
            continue
        
        open_count = bot.positions.count_open(chat_id)
        if open_count >= settings.max_concurrent_positions:
            logger.warning("Max posizioni raggiunto")
            continue
        
        # Calcolo Budget
        budget = getattr(settings, 'budget_sol_per_trade', None) or \
                 getattr(settings, 'amount_per_trade', None) or \
                 getattr(settings, 'trade_amount', None) or \
                 0.01  # Fallback
        
        # Conversione Slippage da BPS a Percentuale (es. 1500 bps = 15.0%)
        slippage_bps = getattr(settings, 'slippage_bps', 1500)
        slippage_pct = slippage_bps / 100.0 if slippage_bps >= 100 else float(slippage_bps)
        
        logger.info("💰 Budget: %s SOL, Slippage: %.1f%%", budget, slippage_pct)
        
        # Notifica Telegram
        logger.info("📱 Notifica Telegram...")
        asyncio.create_task(bot._notify(
            chat_id,
            f"⚡ Token: `{event.mint}`\n"
            f"Acquisto diretto Pump.fun in corso..."
        ))
        
        # Acquisto Diretto Pump.fun
        logger.info("💰 Tentativo acquisto diretto...")
        result = await trader.buy_direct_pumpfun(
            session=session,
            keypair=bot.keypair,
            token_mint=event.mint,
            amount_sol=budget,
            slippage_percent=slippage_pct,
            priority_fee=0.003  # Priority Fee in SOL per garantire la priorita nei blocchi
        )
        
        logger.info("🔍 Risultato: success=%s, error=%s", result.success, result.error)
        
        if result.success:
            logger.info("✅ Acquistato!")
            asyncio.create_task(bot._notify(
                chat_id,
                f"✅ Acquistato in {result.latency_ms}ms!\nTX: `{result.txid}`"
            ))
            await bot._open_position(chat_id, event.mint, settings)
        else:
            logger.error("❌ Errore: %s", result.error)
            asyncio.create_task(bot._notify(
                chat_id,
                f"❌ Errore: {result.error}"
            ))


async def run():
    config = AppConfig.from_env()
    
    # Carica wallet
    import os
    seed_words = os.getenv("SEED_PHRASE", "")
    if not seed_words:
        print("❌ SEED_PHRASE non trovato in .env")
        exit(1)
    mnemo = Mnemonic("english")
    seed = mnemo.to_seed(seed_words)
    key_bytes = seed[:32]
    signing_key = nacl.signing.SigningKey(key_bytes)
    full_key = signing_key.encode() + signing_key.verify_key.encode()
    keypair = Keypair.from_bytes(full_key)
    
    logger.info("✅ Wallet: %s", keypair.pubkey())
    
    # Setup
    settings_store = SettingsStore(config.data_dir)
    bot = SniperTelegramBot(config, keypair, settings_store)
    rpc_http = AsyncClient(config.rpc_http)
    
    # Inizializza il trader diretto di Pump.fun
    trader = DirectPumpTrader(rpc_http)
    
    # Session
    connector = aiohttp.TCPConnector(limit=50, limit_per_host=10)
    session = aiohttp.ClientSession(connector=connector)
    
    # Callback
    async def callback(event: TokenEvent):
        await handle_token_reliable(event, bot, trader, session)
    
    # Listener
    listener = ReliablePoolListener(config.rpc_wss, callback)
    
    # Avvia bot
    await bot.start()
    logger.info("🚀 BOT RELIABLE MODE AVVIATO (DIRETTO PUMP.FUN)")
    logger.info("📡 Ascoltando pump.fun via WebSocket")
    
    try:
        await listener.start()
    except KeyboardInterrupt:
        logger.info("⏹️ Shutdown...")
    finally:
        listener.stop()
        await bot.stop()
        await session.close()
        await rpc_http.close()


if __name__ == "__main__":
    asyncio.run(run())
