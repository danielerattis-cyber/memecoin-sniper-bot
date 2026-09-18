"""
Trader Raydium V4 diretto - compra token via swap Raydium.
Non usa PumpPortal, chiama direttamente il programma Raydium SwapV4.
"""
import time
import logging
import asyncio
import aiohttp
from solders.keypair import Keypair
from solders.instruction import Instruction
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from solders.compute_budget import set_compute_unit_price
import base58

logger = logging.getLogger("trader_raydium")

# Raydium SwapV4 Program
import base58
RAYDIUM_SWAP_V4 = Pubkey(base58.b58decode("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"))
SOL_MINT = Pubkey(base58.b58decode("So11111111111111111111111111111111111111112"))
TOKEN_PROGRAM = Pubkey(base58.b58decode("TokenkegQfeZyiNwAJsyFbPVwwQQfq5x5EvFqUtNqt"))
class TradeResult:
    def __init__(self, success: bool, txid: str = None, latency_ms: float = 0, error: str = None):
        self.success = success
        self.txid = txid
        self.latency_ms = latency_ms
        self.error = error


class RaydiumSwapTrader:
    """Compra token via Raydium AMM V4."""
    
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
        """
        Compra token via Raydium usando Jupiter quote.
        Jupiter fornisce la route e il prezzo, poi firmiamo e mandiamo la tx.
        """
        start_time = time.time()
        
        try:
            logger.info("💰 Raydium Buy: %s SOL per %s (slippage: %s%%)", 
                       amount_sol, token_mint, slippage_percent)
            
            # 1. Ottieni quote da Jupiter (che usa Raydium)
            quote = await self._get_quote_from_jupiter(
                session,
                str(SOL_MINT),
                token_mint,
                int(amount_sol * 1_000_000_000),
                int(slippage_percent * 100)
            )
            
            if not quote:
                logger.error("❌ Quote fallito da Jupiter")
                return TradeResult(success=False, error="Quote fallito")
            
            logger.info("✅ Quote ricevuto: %s token", quote.get("outAmount", "?"))
            
            # 2. Ottieni la transazione da Jupiter
            swap_tx = await self._get_swap_transaction(
                session,
                keypair.pubkey(),
                quote,
                int(priority_fee * 1_000_000)  # Converti a lamports
            )
            
            if not swap_tx:
                logger.error("❌ Swap transaction fallito")
                return TradeResult(success=False, error="Swap transaction fallito")
            
            # 3. Firma e invia
            logger.info("🔏 Firma transazione...")
            tx_bytes = self._decode_and_sign_transaction(swap_tx, keypair)
            
            if not tx_bytes:
                return TradeResult(success=False, error="Firma fallita")
            
            logger.info("📤 Invio transazione...")
            result = await self.rpc_client.send_raw_transaction(tx_bytes)
            txid = str(result.value)
            latency = (time.time() - start_time) * 1000
            
            logger.info("✅ TX Inviata in %.2f ms | TXID: %s", latency, txid[:20])
            return TradeResult(success=True, txid=txid, latency_ms=latency)
        
        except Exception as e:
            logger.error("❌ Errore: %s", str(e))
            return TradeResult(success=False, error=str(e))
    
    async def _get_quote_from_jupiter(
        self,
        session: aiohttp.ClientSession,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int,
    ) -> dict:
        """Ottiene quote da Jupiter API."""
        try:
            url = "https://quote-api.jup.ag/v6/quote"
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": slippage_bps,
            }
            
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    logger.warning("Quote status %d", resp.status)
                    return None
                
                return await resp.json()
        
        except asyncio.TimeoutError:
            logger.error("Quote timeout")
            return None
        except Exception as e:
            logger.error("Quote error: %s", str(e))
            return None
    
    async def _get_swap_transaction(
        self,
        session: aiohttp.ClientSession,
        user_pubkey: Pubkey,
        quote: dict,
        priority_fee_lamports: int,
    ) -> str:
        """Ottiene la transazione swap da Jupiter."""
        try:
            url = "https://api.jup.ag/swap/v1/swap"
            body = {
                "quoteResponse": quote,
                "userPublicKey": str(user_pubkey),
                "wrapAndUnwrapSol": True,
                "prioritizationFeeLamports": priority_fee_lamports,
            }
            
            async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning("Swap status %d: %s", resp.status, text[:100])
                    return None
                
                data = await resp.json()
                return data.get("swapTransaction")
        
        except asyncio.TimeoutError:
            logger.error("Swap timeout")
            return None
        except Exception as e:
            logger.error("Swap error: %s", str(e))
            return None
    
    def _decode_and_sign_transaction(self, tx_base64: str, keypair: Keypair) -> bytes:
        """Decodifica transazione base64, firma e ritorna bytes."""
        try:
            from base64 import b64decode
            tx_bytes = b64decode(tx_base64)
            tx = VersionedTransaction.from_bytes(tx_bytes)
            tx.sign([keypair])
            return bytes(tx)
        except Exception as e:
            logger.error("Firma error: %s", str(e))
            return None
