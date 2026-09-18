"""
Trader Raydium V4 - compra token via Jupiter API.
"""
import time
import logging
import asyncio
import aiohttp
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient
from base64 import b64decode

logger = logging.getLogger("trader_raydium")

SOL_MINT = "So11111111111111111111111111111111111111112"


class TradeResult:
    def __init__(self, success: bool, txid: str = None, latency_ms: float = 0, error: str = None):
        self.success = success
        self.txid = txid
        self.latency_ms = latency_ms
        self.error = error


class RaydiumSwapTrader:
    """Compra token via Jupiter (Raydium)."""
    
    def __init__(self, rpc_client: AsyncClient):
        self.rpc_client = rpc_client

    async def buy_on_raydium(
        self,
        session: aiohttp.ClientSession,
        keypair: Keypair,
        token_mint: str,
        amount_sol: float,
        slippage_percent: float = 15.0,
        priority_fee: float = 0.003,
    ) -> TradeResult:
        start_time = time.time()
        
        try:
            logger.info("💰 Jupiter Buy: %s SOL per %s", amount_sol, token_mint)
            
            # 1. Quote
            quote = await self._get_quote(
                session,
                SOL_MINT,
                token_mint,
                int(amount_sol * 1_000_000_000),
                int(slippage_percent * 100)
            )
            
            if not quote:
                return TradeResult(success=False, error="Quote fallito")
            
            # 2. Swap TX
            swap_tx_b64 = await self._get_swap_tx(
                session,
                str(keypair.pubkey()),
                quote,
                int(priority_fee * 1_000_000)
            )
            
            if not swap_tx_b64:
                return TradeResult(success=False, error="Swap fallito")
            
            # 3. Firma e invia
            tx_bytes = b64decode(swap_tx_b64)
            tx = VersionedTransaction.from_bytes(tx_bytes)
            tx.sign([keypair])
            
            result = await self.rpc_client.send_raw_transaction(bytes(tx))
            txid = str(result.value)
            latency = (time.time() - start_time) * 1000
            
            logger.info("✅ TX inviato in %.0fms", latency)
            return TradeResult(success=True, txid=txid, latency_ms=latency)
        
        except Exception as e:
            logger.error("❌ Errore: %s", str(e))
            return TradeResult(success=False, error=str(e))
    
    async def _get_quote(self, session, input_mint, output_mint, amount, slippage_bps):
        try:
            async with session.get(
                "https://quote-api.jup.ag/v6/quote",
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": amount,
                    "slippageBps": slippage_bps,
                },
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.error("Quote error: %s", str(e))
        return None
    
    async def _get_swap_tx(self, session, user_pubkey, quote, priority_fee):
        try:
            async with session.post(
                "https://api.jup.ag/swap/v1/swap",
                json={
                    "quoteResponse": quote,
                    "userPublicKey": user_pubkey,
                    "wrapAndUnwrapSol": True,
                    "prioritizationFeeLamports": priority_fee,
                },
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("swapTransaction")
        except Exception as e:
            logger.error("Swap error: %s", str(e))
        return None
