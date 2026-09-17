"""
Rilevamento affidabile via RPC WebSocket di Solana.
Ascolta transazioni del programma pump.fun e estrae i nuovi token.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Set
import websockets

logger = logging.getLogger("sniper_reliable")

PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

@dataclass
class TokenEvent:
    """Evento di nuovo token."""
    mint: str
    source: str
    detected_at: float
    
    @property
    def latency_ms(self) -> int:
        return int((time.time() - self.detected_at) * 1000)


class ReliablePoolListener:
    """Ascolta nuovi token pump.fun via RPC WebSocket (affidabile)."""
    
    def __init__(self, wss_endpoint: str, callback: Callable):
        self.wss_endpoint = wss_endpoint
        self.callback = callback
        self.running = False
        self.seen_mints: Set[str] = set()
        self.ws = None
        self.subscription_id = None
        
    async def start(self):
        """Avvia listener WebSocket con gestione errori robusta."""
        self.running = True
        logger.info("🚀 ReliablePoolListener avviato")
        
        retry_count = 0
        max_retries = 10
        
        while self.running and retry_count < max_retries:
            try:
                async with websockets.connect(self.wss_endpoint, ping_interval=20) as ws:
                    self.ws = ws
                    retry_count = 0
                    logger.info("✅ WebSocket connesso")
                    
                    # Subscribe a pump.fun
                    subscribe_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {
                                "mentions": [PUMP_FUN_PROGRAM]
                            },
                            {"commitment": "confirmed"}
                        ]
                    }
                    
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info("📡 Subscription inviata a pump.fun")
                    
                    # Ascolta
                    async for message in ws:
                        if not self.running:
                            break
                        try:
                            data = json.loads(message)
                            await self._handle_message(data)
                        except json.JSONDecodeError:
                            pass
                        except Exception as e:
                            logger.debug("Handler error: %s", str(e))
                    
            except websockets.exceptions.WebSocketException as e:
                retry_count += 1
                wait = min(2 ** retry_count, 30)
                logger.warning("WebSocket disconnesso, retry in %ds (%d/%d)", wait, retry_count, max_retries)
                await asyncio.sleep(wait)
            except Exception as e:
                retry_count += 1
                logger.error("Errore: %s", str(e))
                await asyncio.sleep(min(2 ** retry_count, 30))
        
        logger.error("❌ ReliablePoolListener fermato")
    
    async def _handle_message(self, data: dict):
        """Processa messaggio e estrae token."""
        try:
            # Controlla se è una notifica di log
            if "params" not in data or "result" not in data["params"]:
                return
            
            result = data["params"]["result"]
            if "value" not in result:
                return
            
            value = result["value"]
            logs = value.get("logs", [])
            
            if not logs or not isinstance(logs, list):
                return
            
            # Cerca initialize di token (nuovo token pump.fun)
            # Parsing semplice: se vede "InitializeMint" o "initialize2" è probabile un nuovo token
            log_text = " ".join(str(log) for log in logs if log)
            
            # Cerca pattern di nuovo token
            if "initialize" not in log_text.lower():
                return
            
            # Estrai il mint
            mint = self._extract_mint(value)
            if not mint or mint in self.seen_mints:
                return
            
            self.seen_mints.add(mint)
            
            # Callback
            event = TokenEvent(
                mint=mint,
                source="pumpfun",
                detected_at=time.time()
            )
            
            logger.info("⚡ NUOVO TOKEN: %s", event.mint)
            asyncio.create_task(self.callback(event))
        
        except Exception as e:
            logger.debug("Message handler error: %s", str(e))
    
    def _extract_mint(self, value: dict) -> str or None:
        """Estrae mint dalla transazione."""
        try:
            # Il mint è solamente nel signature (transazione hash)
            # O nei log come account
            tx_sig = value.get("signature", "")
            
            # Fallback: prova a trovare base58 nei log
            logs = value.get("logs", [])
            for log in logs:
                if not log:
                    continue
                log_str = str(log)
                # Cerca indirizzi base58 (44 caratteri che iniziano con A-Z)
                for word in log_str.split():
                    if len(word) == 44 and word[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                        return word
            
            return None
        except:
            return None
    
    def stop(self):
        """Ferma il listener."""
        self.running = False
        logger.info("ReliablePoolListener fermato")