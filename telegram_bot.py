"""
Interfaccia Telegram: tutto a bottoni, zero comandi da ricordare a memoria.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import trader_pumpfun as trader
from config import AppConfig, SettingsStore
from risk_manager import Position, PositionManager, monitor_position
from safety_checks import run_full_check
from sniper import NewTokenEvent

logger = logging.getLogger("telegram_bot")


def main_menu_keyboard(auto_snipe_on: bool) -> InlineKeyboardMarkup:
    snipe_button = (
        InlineKeyboardButton("⏸ Ferma Sniping", callback_data="snipe_off")
        if auto_snipe_on else
        InlineKeyboardButton("🎯 Attiva Sniping", callback_data="snipe_on")
    )
    return InlineKeyboardMarkup([
        [snipe_button],
        [InlineKeyboardButton("⚙️ Impostazioni", callback_data="settings"),
         InlineKeyboardButton("💰 Saldo", callback_data="balance")],
        [InlineKeyboardButton("📊 Posizioni Aperte", callback_data="positions")],
        [InlineKeyboardButton("🆘 Vendi Tutto", callback_data="panic_sell")],
        [InlineKeyboardButton("ℹ️ Aiuto", callback_data="help")],
    ])


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Budget per trade", callback_data="set_budget")],
        [InlineKeyboardButton("📉 Stop loss", callback_data="set_sl"),
         InlineKeyboardButton("📈 Take profit", callback_data="set_tp")],
        [InlineKeyboardButton("🛡️ Soglia sicurezza minima", callback_data="set_score")],
        [InlineKeyboardButton("⬅️ Indietro", callback_data="back_main")],
    ])


def preset_keyboard(prefix: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """options: lista di (etichetta_visibile, valore_callback)"""
    rows = [[InlineKeyboardButton(label, callback_data=f"{prefix}:{value}")] for label, value in options]
    rows.append([InlineKeyboardButton("⬅️ Indietro", callback_data="settings")])
    return InlineKeyboardMarkup(rows)


class SniperTelegramBot:
    def __init__(self, config: AppConfig, keypair: Keypair, settings_store: SettingsStore):
        self.config = config
        self.keypair = keypair
        self.settings_store = settings_store
        self.positions = PositionManager()
        self.session: aiohttp.ClientSession | None = None
        self.rpc = AsyncClient(config.rpc_http)
        self.app = Application.builder().token(config.telegram_token).build()
        self._register_handlers()

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CallbackQueryHandler(self.on_button))

    # ---------- Comandi ----------

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        settings = self.settings_store.get(chat_id)
        text = (
            "👋 *Memecoin Sniper Bot*\n\n"
            "Monitoro pump.fun e Raydium per nuovi token appena lanciati.\n"
            "Usa i bottoni qui sotto, non serve scrivere comandi.\n\n"
            f"Wallet: `{self.keypair.pubkey()}`\n"
            f"Budget per trade: *{settings.max_buy_sol} SOL*\n"
            f"Sniping automatico: {'🟢 ATTIVO' if settings.auto_snipe_enabled else '🔴 spento'}"
        )
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=main_menu_keyboard(settings.auto_snipe_enabled)
        )

    # ---------- Bottoni ----------

    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        data = query.data
        settings = self.settings_store.get(chat_id)

        if data == "back_main" or data == "help_close":
            await query.edit_message_text(
                "Menu principale:", reply_markup=main_menu_keyboard(settings.auto_snipe_enabled)
            )

        elif data == "snipe_on":
            settings.auto_snipe_enabled = True
            self.settings_store.save(chat_id, settings)
            await query.edit_message_text(
                "🎯 Sniping automatico *ATTIVATO*.\nTi avviserò a ogni nuovo token rilevato.",
                parse_mode="Markdown", reply_markup=main_menu_keyboard(True),
            )

        elif data == "snipe_off":
            settings.auto_snipe_enabled = False
            self.settings_store.save(chat_id, settings)
            await query.edit_message_text(
                "⏸ Sniping automatico fermato.", reply_markup=main_menu_keyboard(False)
            )

        elif data == "settings":
            await query.edit_message_text(
                self._settings_summary(settings), parse_mode="Markdown", reply_markup=settings_keyboard()
            )

        elif data == "set_budget":
            await query.edit_message_text(
                "Quanto SOL vuoi investire per ogni token?",
                reply_markup=preset_keyboard("budget", [
                    ("0.01 SOL", "0.01"), ("0.02 SOL", "0.02"),
                    ("0.05 SOL", "0.05"), ("0.1 SOL", "0.1"),
                ]),
            )
        elif data.startswith("budget:"):
            settings.max_buy_sol = float(data.split(":")[1])
            self.settings_store.save(chat_id, settings)
            await query.edit_message_text(
                f"✅ Budget per trade impostato a {settings.max_buy_sol} SOL.",
                reply_markup=settings_keyboard(),
            )

        elif data == "set_sl":
            await query.edit_message_text(
                "A quale perdita vuoi uscire automaticamente?",
                reply_markup=preset_keyboard("sl", [
                    ("-15%", "15"), ("-25%", "25"), ("-35%", "35"), ("-50%", "50"),
                ]),
            )
        elif data.startswith("sl:"):
            settings.stop_loss_pct = float(data.split(":")[1])
            self.settings_store.save(chat_id, settings)
            await query.edit_message_text(
                f"✅ Stop loss impostato a -{settings.stop_loss_pct:.0f}%.",
                reply_markup=settings_keyboard(),
            )

        elif data == "set_tp":
            await query.edit_message_text(
                "Livelli di take profit (preimpostati, vendita parziale a ogni soglia):",
                reply_markup=preset_keyboard("tp", [
                    ("Conservativo (+30/+60/+100%)", "cons"),
                    ("Bilanciato (+50/+100/+200%)", "bal"),
                    ("Aggressivo (+100/+300/+500%)", "aggr"),
                ]),
            )
        elif data.startswith("tp:"):
            preset = data.split(":")[1]
            presets = {
                "cons": [(30, 0.3), (60, 0.3), (100, 0.4)],
                "bal": [(50, 0.3), (100, 0.3), (200, 0.4)],
                "aggr": [(100, 0.3), (300, 0.3), (500, 0.4)],
            }
            settings.take_profit_levels = presets[preset]
            self.settings_store.save(chat_id, settings)
            await query.edit_message_text("✅ Livelli di take profit aggiornati.", reply_markup=settings_keyboard())

        elif data == "set_score":
            await query.edit_message_text(
                "Punteggio minimo di sicurezza per l'acquisto automatico (0-100, più alto = più prudente):",
                reply_markup=preset_keyboard("score", [
                    ("40 (permissivo, più rischio)", "40"),
                    ("60 (bilanciato)", "60"),
                    ("80 (prudente)", "80"),
                ]),
            )
        elif data.startswith("score:"):
            settings.min_safety_score = int(data.split(":")[1])
            self.settings_store.save(chat_id, settings)
            await query.edit_message_text(
                f"✅ Soglia sicurezza minima impostata a {settings.min_safety_score}/100.",
                reply_markup=settings_keyboard(),
            )

        elif data == "balance":
            balance = await self.rpc.get_balance(self.keypair.pubkey())
            sol = balance.value / 1_000_000_000
            await query.edit_message_text(
                f"💰 Saldo wallet: *{sol:.4f} SOL*\n`{self.keypair.pubkey()}`",
                parse_mode="Markdown", reply_markup=main_menu_keyboard(settings.auto_snipe_enabled),
            )

        elif data == "positions":
            open_positions = self.positions.list_open(chat_id)
            if not open_positions:
                text = "📊 Nessuna posizione aperta al momento."
            else:
                lines = ["📊 *Posizioni aperte:*\n"]
                for p in open_positions:
                    lines.append(f"• `{p.mint[:8]}...` — entrata a {p.entry_price_sol:.8f} SOL")
                text = "\n".join(lines)
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard(settings.auto_snipe_enabled))

        elif data == "panic_sell":
            await query.edit_message_text("🆘 Vendita di tutte le posizioni in corso...")
            await self._panic_sell_all(chat_id, settings)

        elif data == "help":
            await query.edit_message_text(
                "ℹ️ *Come funziona*\n\n"
                "1️⃣ Attiva lo sniping automatico\n"
                "2️⃣ Il bot rileva nuovi token su pump.fun/Raydium\n"
                "3️⃣ Se superano la soglia di sicurezza, compra da solo\n"
                "4️⃣ Stop loss / take profit / trailing stop gestiti automaticamente\n"
                "5️⃣ Ricevi una notifica a ogni evento\n\n"
                "⚠️ Nessun controllo automatico garantisce l'assenza di rischio. "
                "Investi solo capitale che puoi permetterti di perdere.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Indietro", callback_data="back_main")]]),
            )

        elif data.startswith("buy_anyway:"):
            mint = data.split(":", 1)[1]
            await query.edit_message_text("⚠️ Acquisto manuale in corso nonostante il rischio segnalato...")
            await self._execute_buy(chat_id, mint, settings, forced=True)

        elif data == "ignore_detection":
            await query.edit_message_text("Ok, ignorato.")

    def _settings_summary(self, s) -> str:
        tp_text = ", ".join(f"+{pct:.0f}%→{frac*100:.0f}%" for pct, frac in s.take_profit_levels)
        return (
            "⚙️ *Impostazioni attuali*\n\n"
            f"💵 Budget per trade: {s.max_buy_sol} SOL\n"
            f"📉 Stop loss: -{s.stop_loss_pct:.0f}%\n"
            f"📉 Trailing stop: -{s.trailing_stop_pct:.0f}% dal massimo\n"
            f"📈 Take profit: {tp_text}\n"
            f"🛡️ Soglia sicurezza minima: {s.min_safety_score}/100\n"
            f"⏱️ Durata massima posizione: {s.max_hold_minutes} min\n"
        )

    # ---------- Logica di sniping ----------

    async def handle_new_token(self, event: NewTokenEvent):
        """Chiamato dal PoolListener a ogni nuovo token rilevato. Esegue i
        controlli di sicurezza per OGNI utente attivo e decide se comprare."""
        for chat_id_str in self._all_chat_ids():
            chat_id = int(chat_id_str)
            settings = self.settings_store.get(chat_id)
            if not settings.auto_snipe_enabled:
                continue
            if self.positions.count_open(chat_id) >= settings.max_concurrent_positions:
                continue

            result = await run_full_check(
                self.rpc, self.session, self.config.jupiter_api,
                event.mint, event.pool_sol_vault, settings,
            )

            reasons_text = "\n".join(f"  {r}" for r in result.reasons)
            base_msg = (
                f"🆕 Nuovo token rilevato ({event.source})\n"
                f"`{event.mint}`\n"
                f"Punteggio sicurezza: *{result.score}/100*\n{reasons_text}"
            )

            if result.passed:
                await self._notify(chat_id, base_msg + "\n\n✅ Supera la soglia, procedo con l'acquisto...")
                await self._execute_buy(chat_id, event.mint, settings, forced=False)
            elif settings.notify_on_low_score_detections:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚠️ Compra comunque", callback_data=f"buy_anyway:{event.mint}"),
                     InlineKeyboardButton("Ignora", callback_data="ignore_detection")],
                ])
                await self.app.bot.send_message(
                    chat_id, base_msg + "\n\n🚫 Sotto la soglia di sicurezza, NON comprato automaticamente.",
                    parse_mode="Markdown", reply_markup=keyboard,
                )

    async def _execute_buy(self, chat_id: int, mint: str, settings, forced: bool):
        result = await trader.execute_buy(
            self.session, self.config.jupiter_api, self.rpc, self.keypair,
            mint, settings.max_buy_sol, settings.slippage_bps, settings.priority_fee,
        )
        if not result.success:
            await self._notify(chat_id, f"❌ Acquisto fallito su `{mint[:8]}...`: {result.error}")
            return

        entry_price = settings.max_buy_sol / result.out_amount if result.out_amount else 0
        position = Position(
            chat_id=chat_id, mint=mint, entry_price_sol=entry_price, token_amount=result.out_amount,
        )
        self.positions.add(position)
        await self._notify(
            chat_id,
            f"{'⚠️ ' if forced else '✅ '}Comprato `{mint[:8]}...` per {settings.max_buy_sol} SOL\n"
            f"Tx: `{result.signature}`\nGestione stop loss/take profit ora automatica.",
        )
        asyncio.create_task(monitor_position(
            position, settings, self.session, self.config.jupiter_api, self.rpc,
            self.keypair, self.positions, self._notify,
        ))

    async def _panic_sell_all(self, chat_id: int, settings):
        open_positions = self.positions.list_open(chat_id)
        if not open_positions:
            await self._notify(chat_id, "Nessuna posizione da vendere.")
            return
        for pos in open_positions:
            balance = await trader.get_token_balance(self.rpc, str(self.keypair.pubkey()), pos.mint)
            if balance > 0:
                result = await trader.execute_sell(
                    self.session, self.config.jupiter_api, self.rpc, self.keypair,
                    pos.mint, balance, settings.slippage_bps, settings.priority_fee,
                )
                status = "✅ venduto" if result.success else f"❌ errore: {result.error}"
            else:
                status = "ℹ️ saldo già a zero"
            self.positions.close(chat_id, pos.mint)
            await self._notify(chat_id, f"`{pos.mint[:8]}...`: {status}")

    async def _notify(self, chat_id: int, text: str):
        try:
            await self.app.bot.send_message(chat_id, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning("Notifica fallita per %s: %s", chat_id, e)

    def _all_chat_ids(self):
        return [p.stem for p in self.settings_store.dir.glob("*.json")]

    async def start(self):
        self.session = aiohttp.ClientSession()
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

    async def stop(self):
        if self.session:
            await self.session.close()
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
