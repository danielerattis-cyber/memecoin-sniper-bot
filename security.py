"""
Gestione della chiave privata del wallet.

REGOLA D'ORO: la chiave privata non deve MAI passare per Telegram, per un
server remoto di terzi, o per un log. Viene cifrata con una password e
salvata in un file locale (`wallet.enc`). La password si inserisce SOLO nel
terminale all'avvio del bot (vedi main.py), mai in una chat.

Se in futuro modifichi questo bot per farlo girare "in cloud" condiviso da
più persone, questo schema NON basta: serve un vero key-management service
(es. AWS KMS, HashiCorp Vault) con un wallet per utente.
"""
from __future__ import annotations

import base64
import os

import base58
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from solders.keypair import Keypair

PBKDF2_ITERATIONS = 390_000


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def create_keystore(private_key_base58: str, password: str, path: str) -> None:
    """Cifra una chiave privata (formato base58, es. export di Phantom) e la
    salva su disco. Da eseguire SOLO in locale, mai su una macchina condivisa
    senza disco cifrato."""
    # Valida che sia base58 valido
    try:
        raw = base58.b58decode(private_key_base58)
    except Exception as e:
        raise ValueError(f"Chiave privata base58 non valida: {e}")
    
    # Deve essere 32 byte (chiave privata Solana)
    if len(raw) != 32:
        raise ValueError(f"Chiave privata deve essere 32 byte, ma è {len(raw)}")
    
    salt = os.urandom(16)
    key = _derive_key(password, salt)
    token = Fernet(key).encrypt(raw)
    
    with open(path, "wb") as f:
        f.write(salt + b"::" + token)
    
    os.chmod(path, 0o600)


def load_wallet(path: str, password: str) -> Keypair:
    """Decifra il keystore e ritorna il Keypair pronto per firmare."""
    with open(path, "rb") as f:
        content = f.read()
    
    salt, token = content.split(b"::", 1)
    key = _derive_key(password, salt)
    raw = Fernet(key).decrypt(token)
    
    # raw ha 32 byte, li ritorniamo direttamente
    return raw