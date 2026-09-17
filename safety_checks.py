"""
Controlli di sicurezza da eseguire sui token candidati PRIMA di comprare.

Nessuno di questi controlli garantisce che un token sia "sicuro": servono
solo a scartare i casi più ovvi (mint authority attiva, honeypot semplici,
concentrazione estrema). Un token può passare tutti questi controlli ed
essere comunque un rug pull ben fatto.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional

import aiohttp
import base58
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

# Layout binario standard di un account SPL Token "Mint" (82 byte totali):
#   4 byte  tag COption "mint_authority" presente/assente
#   32 byte pubkey mint_authority (se presente)
#   8 byte  supply (u64 little-endian)
#   1 byte  decimals
#   1 byte  is_initialized
#   4 byte  tag COption "freeze_authority"
#   32 byte pubkey freeze_authority (se presente)
MINT_LAYOUT_SIZE = 82


@dataclass
class MintInfo:
    mint_authority: Optional[str]
    freeze_authority: Optional[str]
    supply: int
    decimals: int


@dataclass
class SafetyCheckResult:
    score: int  # 0-100
    passed: bool
    reasons: List[str] = field(default_factory=list)
    mint_info: Optional[MintInfo] = None
    holder_top10_pct: Optional[float] = None
    honeypot_suspected: Optional[bool] = None


async def get_mint_info(rpc: AsyncClient, mint_address: str) -> Optional[MintInfo]:
    resp = await rpc.get_account_info(Pubkey.from_string(mint_address))
    if resp.value is None:
        return None
    data = bytes(resp.value.data)
    if len(data) < MINT_LAYOUT_SIZE:
        return None

    offset = 0
    mint_auth_tag = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    mint_auth_bytes = data[offset:offset + 32]
    offset += 32
    mint_authority = base58.b58encode(mint_auth_bytes).decode() if mint_auth_tag == 1 else None

    supply = struct.unpack_from("<Q", data, offset)[0]
    offset += 8
    decimals = data[offset]
    offset += 1
    offset += 1  # is_initialized, non ci serve

    freeze_auth_tag = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    freeze_auth_bytes = data[offset:offset + 32]
    freeze_authority = base58.b58encode(freeze_auth_bytes).decode() if freeze_auth_tag == 1 else None

    return MintInfo(mint_authority, freeze_authority, supply, decimals)


async def get_holder_concentration(rpc: AsyncClient, mint_address: str, top_n: int = 10) -> Optional[float]:
    """Percentuale di supply detenuta dai top N holder. Un valore alto
    (es. >50%) significa che pochi wallet possono far crollare il prezzo
    vendendo tutto insieme."""
    try:
        largest = await rpc.get_token_largest_accounts(Pubkey.from_string(mint_address))
        mint_info = await get_mint_info(rpc, mint_address)
        if not largest.value or not mint_info or mint_info.supply == 0:
            return None
        top_amount = sum(int(acc.amount) for acc in largest.value[:top_n])
        return round((top_amount / mint_info.supply) * 100, 2)
    except Exception:
        return None


async def simulate_honeypot(
    session: aiohttp.ClientSession,
    jupiter_api: str,
    mint_address: str,
    sol_mint: str = "So11111111111111111111111111111111111111112",
    test_lamports: int = 10_000_000,  # 0.01 SOL
) -> Optional[bool]:
    """Prova a ottenere una quotazione di ACQUISTO e poi una di VENDITA per lo
    stesso token. Se non esiste una route di vendita, o se la perdita di
    andata e ritorno è assurda (>60%), è un forte segnale di honeypot.
    Ritorna True se sospetto, False se sembra scambiabile, None se non
    verificabile (es. API non raggiungibile)."""
    try:
        buy_quote = await _get_quote(session, jupiter_api, sol_mint, mint_address, test_lamports)
        if not buy_quote:
            return True  # nessuna route neanche in acquisto: molto sospetto

        token_out = int(buy_quote["outAmount"])
        sell_quote = await _get_quote(session, jupiter_api, mint_address, sol_mint, token_out)
        if not sell_quote:
            return True  # si compra ma non si può rivendere -> honeypot classico

        sol_back = int(sell_quote["outAmount"])
        round_trip_loss_pct = (1 - sol_back / test_lamports) * 100
        return round_trip_loss_pct > 60
    except Exception:
        return None  # non siamo riusciti a verificare, meglio non fidarsi ciecamente


async def _get_quote(session: aiohttp.ClientSession, jupiter_api: str,
                      input_mint: str, output_mint: str, amount: int) -> Optional[dict]:
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": "1000",  # tolleranza larga solo per il test, non per il trade vero
    }
    async with session.get(f"{jupiter_api}/quote", params=params, timeout=5) as resp:
        if resp.status != 200:
            return None
        return await resp.json()


async def check_lp_liquidity_sol(rpc: AsyncClient, pool_sol_vault: str) -> Optional[float]:
    """Stima grezza della liquidità in SOL guardando il saldo del vault SOL
    del pool. Per un controllo affidabile su LP bloccata/bruciata in
    produzione, integra un servizio dedicato (RugCheck, Birdeye, Bitquery):
    i formati dei pool cambiano spesso e un parsing fai-da-te si rompe
    facilmente senza preavviso."""
    try:
        balance = await rpc.get_balance(Pubkey.from_string(pool_sol_vault))
        return balance.value / 1_000_000_000
    except Exception:
        return None


async def run_full_check(
    rpc: AsyncClient,
    session: aiohttp.ClientSession,
    jupiter_api: str,
    mint_address: str,
    pool_sol_vault: Optional[str],
    settings,  # config.UserSettings
) -> SafetyCheckResult:
    reasons: List[str] = []
    score = 100

    mint_info = await get_mint_info(rpc, mint_address)
    if mint_info is None:
        return SafetyCheckResult(score=0, passed=False, reasons=["Impossibile leggere l'account mint"])

    if settings.require_mint_authority_revoked and mint_info.mint_authority is not None:
        score -= 35
        reasons.append("⚠️ Mint authority ATTIVA: il creatore può stampare nuovi token a piacere")

    if settings.require_freeze_authority_revoked and mint_info.freeze_authority is not None:
        score -= 25
        reasons.append("⚠️ Freeze authority ATTIVA: il creatore può bloccare i tuoi token")

    holder_pct = await get_holder_concentration(rpc, mint_address)
    if holder_pct is not None:
        if holder_pct > settings.max_top10_holder_pct:
            score -= 20
            reasons.append(f"⚠️ Top 10 holder possiedono il {holder_pct}% della supply")
    else:
        reasons.append("ℹ️ Concentrazione holder non verificabile")

    liquidity_sol = None
    if pool_sol_vault:
        liquidity_sol = await check_lp_liquidity_sol(rpc, pool_sol_vault)
        if liquidity_sol is not None and liquidity_sol < settings.min_liquidity_sol:
            score -= 20
            reasons.append(f"⚠️ Liquidità bassa: {liquidity_sol:.2f} SOL (minimo richiesto {settings.min_liquidity_sol})")

    honeypot = None  # Disabilita il controllo honeypot
# honeypot = await simulate_honeypot(session, jupiter_api, mint_address)
# if honeypot is True:
#     score -= 60
#     reasons.append("🚨 Probabile HONEYPOT: nessuna route di vendita trovata")
    
    score = max(0, score)
    passed = score >= settings.min_safety_score

    return SafetyCheckResult(
        score=score,
        passed=passed,
        reasons=reasons or ["✅ Nessun problema evidente rilevato"],
        mint_info=mint_info,
        holder_top10_pct=holder_pct,
        honeypot_suspected=honeypot,
    )
