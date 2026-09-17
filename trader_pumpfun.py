import time
import logging
import asyncio
import aiohttp
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient

logger = logging.getLogger("trader_pumpfun")

class TradeResult:
    def __init__(self, success: bool, txid: str = None, latency_ms: float = 0, error: str = None):
        self.success = success
        self.txid = txid
        self.latency_ms = latency_ms
        self.error = error

class DirectPumpTrader:
    def __init__(self, rpc_client: AsyncClient):
        self.rpc_client = rpc_client

    async def buy_direct_pumpfun(
        self,
        session: aiohttp.ClientSession,
        keypair: Keypair,
        token_mint: str,
        amount_sol: float,
        slippage_percent: float = 15.0,
        priority_fee: float = 0.003
    ) -> TradeResult:
        start_time = time.time()
        url = "https://api.pumpportal.fun/trade"

        payload = {
            "publicKey": str(keypair.pubkey()),
            "action": "buy",
            "mint": token_mint,
            "denominatedInSol": True,
            "amount": amount_sol,
            "slippage": slippage_percent,
            "priorityFee": priority_fee,
            "pool": "pump"
        }

        try:
            logger.info("📤 Payload: %s", payload)
            
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                response_text = await resp.text()
                
                logger.info("📥 Status: %d | Body: %s", resp.status, response_text[:500])
                
                if resp.status == 200:
                    tx_bytes = await resp.read()
                    logger.info("✅ TX ricevuta (%d bytes)", len(tx_bytes))
                    
                    try:
                        versioned_tx = VersionedTransaction.from_bytes(tx_bytes)
                        versioned_tx.sign([keypair])
                        send_resp = await self.rpc_client.send_raw_transaction(bytes(versioned_tx))
                        txid = str(send_resp.value)
                        latency = (time.time() - start_time) * 1000
                        
                        logger.info("✅ TX Inviata in %.2f ms", latency)
                        return TradeResult(success=True, txid=txid, latency_ms=latency)
                    except Exception as tx_err:
                        logger.error("❌ TX Error: %s", str(tx_err))
                        return TradeResult(success=False, error=str(tx_err))
                else:
                    logger.error("❌ PumpPortal HTTP %d: %s", resp.status, response_text)
                    return TradeResult(success=False, error=response_text[:100])

        except asyncio.TimeoutError:
            logger.error("❌ Timeout")
            return TradeResult(success=False, error="Timeout")
        except Exception as e:
            logger.error("❌ Error: %s", str(e))
            return TradeResult(success=False, error=str(e))