"""
Traccia le posizioni aperte e applica automaticamente stop loss, take profit
a più livelli e trailing stop, senza bisogno che l'utente guardi il grafico.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

import aiohttp
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair

import trader
from config import UserSettings

logger = logging.getLogger("risk_manager")

Notify = Callable[[int, str], Awaitable[None]]  # (chat_id, testo) -> None


@dataclass
class Position:
    chat_id: int
    mint: str
    entry_price_sol: float          # prezzo per 1 token, in SOL
    token_amount: int               # unità grezze (senza decimali)
    entry_time: float = field(default_factory=time.time)
    highest_price_sol: float = 0.0
    tp_levels_hit: List[float] = field(default_factory=list)
    closed: bool = False

    def __post_init__(self):
        self.highest_price_sol = self.entry_price_sol


class PositionManager:
    def __init__(self):
        self._positions: Dict[str, Position] = {}  # key = f"{chat_id}:{mint}"

    def _key(self, chat_id: int, mint: str) -> str:
        return f"{chat_id}:{mint}"

    def add(self, position: Position) -> None:
        self._positions[self._key(position.chat_id, position.mint)] = position

    def get(self, chat_id: int, mint: str) -> Optional[Position]:
        return self._positions.get(self._key(chat_id, mint))

    def list_open(self, chat_id: int) -> List[Position]:
        return [p for p in self._positions.values() if p.chat_id == chat_id and not p.closed]

    def count_open(self, chat_id: int) -> int:
        return len(self.list_open(chat_id))

    def close(self, chat_id: int, mint: str) -> None:
        pos = self.get(chat_id, mint)
        if pos:
            pos.closed = True


async def get_current_price_sol(session: aiohttp.ClientSession, jupiter_api: str, mint: str, decimals: int = 6) -> Optional[float]:
    """Prezzo corrente di 1 token in SOL, usando una quotazione Jupiter per
    una piccola quantità di riferimento (evita di muovere il prezzo)."""
    quote = await trader.get_quote(session, jupiter_api, mint, trader.SOL_MINT, 10 ** decimals, slippage_bps=500)
    if not quote:
        return None
    sol_out = int(quote["outAmount"]) / 1_000_000_000
    return sol_out


async def monitor_position(
    position: Position,
    settings: UserSettings,
    session: aiohttp.ClientSession,
    jupiter_api: str,
    rpc: AsyncClient,
    keypair: Keypair,
    position_manager: PositionManager,
    notify: Notify,
    poll_seconds: float = 3.0,
):
    """Task in background: controlla il prezzo periodicamente e applica le
    regole di uscita configurate dall'utente. Va lanciato con
    asyncio.create_task() subito dopo un acquisto riuscito."""
    remaining_amount = position.token_amount

    while not position.closed:
        await asyncio.sleep(poll_seconds)

        elapsed_minutes = (time.time() - position.entry_time) / 60
        price = await get_current_price_sol(session, jupiter_api, position.mint)
        if price is None:
            continue

        position.highest_price_sol = max(position.highest_price_sol, price)
        pnl_pct = (price / position.entry_price_sol - 1) * 100
        drawdown_from_peak_pct = (1 - price / position.highest_price_sol) * 100

        # 1) Stop loss fisso
        if pnl_pct <= -settings.stop_loss_pct:
            await _sell_and_close(position, remaining_amount, "🔴 Stop loss", pnl_pct,
                                   session, jupiter_api, rpc, keypair, settings, position_manager, notify)
            return

        # 2) Trailing stop (solo se già in profitto)
        if pnl_pct > 0 and drawdown_from_peak_pct >= settings.trailing_stop_pct:
            await _sell_and_close(position, remaining_amount, "🟡 Trailing stop", pnl_pct,
                                   session, jupiter_api, rpc, keypair, settings, position_manager, notify)
            return

        # 3) Time-based exit
        if elapsed_minutes >= settings.max_hold_minutes:
            await _sell_and_close(position, remaining_amount, "⏱️ Tempo massimo raggiunto", pnl_pct,
                                   session, jupiter_api, rpc, keypair, settings, position_manager, notify)
            return

        # 4) Take profit a livelli (vendita parziale)
        for threshold_pct, fraction in settings.take_profit_levels:
            if pnl_pct >= threshold_pct and threshold_pct not in position.tp_levels_hit:
                sell_amount = int(position.token_amount * fraction)
                sell_amount = min(sell_amount, remaining_amount)
                if sell_amount <= 0:
                    continue
                result = await trader.execute_sell(
                    session, jupiter_api, rpc, keypair, position.mint, sell_amount,
                    settings.slippage_bps, settings.priority_fee,
                )
                position.tp_levels_hit.append(threshold_pct)
                if result.success:
                    remaining_amount -= sell_amount
                    await notify(
                        position.chat_id,
                        f"✅ Take profit +{threshold_pct:.0f}% raggiunto su `{position.mint[:6]}...`\n"
                        f"Venduto {fraction*100:.0f}% della posizione. PnL attuale: {pnl_pct:+.1f}%",
                    )
                if remaining_amount <= 0:
                    position_manager.close(position.chat_id, position.mint)
                    return


async def _sell_and_close(position, amount, reason, pnl_pct, session, jupiter_api, rpc, keypair,
                           settings, position_manager, notify):
    if amount > 0:
        result = await trader.execute_sell(
            session, jupiter_api, rpc, keypair, position.mint, amount,
            settings.slippage_bps, settings.priority_fee,
        )
        status = "✅ Venduto" if result.success else f"❌ Vendita fallita ({result.error})"
    else:
        status = "ℹ️ Nessun residuo da vendere"

    position_manager.close(position.chat_id, position.mint)
    await notify(
        position.chat_id,
        f"{reason} su `{position.mint[:6]}...`\nPnL finale: {pnl_pct:+.1f}%\n{status}",
    )
