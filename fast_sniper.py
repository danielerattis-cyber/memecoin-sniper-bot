"""
Modalità FAST BUY: salta TUTTI i controlli di sicurezza (mint authority,
freeze authority, liquidità, concentrazione holder, honeypot) e compra
immediatamente ogni nuovo token rilevato.

Riusa bot._execute_buy() e tutta la logica di stop loss/take profit/notifiche
già presente in telegram_bot.py — quella non viene toccata.
"""
import logging
from sniper import NewTokenEvent

logger = logging.getLogger("fast_sniper")


async def handle_new_token_fast(event: NewTokenEvent, bot):
    """Callback da passare al PoolListener al posto di bot.handle_new_token.
    ZERO controlli di sicurezza: compra e basta, il più velocemente possibile."""
    for chat_id_str in bot._all_chat_ids():
        chat_id = int(chat_id_str)
        settings = bot.settings_store.get(chat_id)

        if not settings.auto_snipe_enabled:
            continue
        if bot.positions.count_open(chat_id) >= settings.max_concurrent_positions:
            continue

        logger.info("FAST BUY su %s (%s)", event.mint, event.source)
        await bot._notify(
            chat_id,
            f"⚡ Nuovo token ({event.source}) `{event.mint}`\n"
            f"ZERO controlli di sicurezza — acquisto immediato in corso...",
        )
        await bot._execute_buy(chat_id, event.mint, settings, forced=True)