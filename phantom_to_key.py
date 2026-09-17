from mnemonic import Mnemonic
from solders.keypair import Keypair
import base58

seed_words = input("Incolla le 24 parole di Phantom separate da spazi: ")

mnemo = Mnemonic("english")
seed = mnemo.to_seed(seed_words)

# Derivazione standard Solana
from solders.pubkey import Pubkey
keypair = Keypair()

# Metodo alternativo: usa la libreria BIP39
try:
    key_bytes = seed[:32]
    keypair = Keypair.from_bytes(key_bytes)
except:
    print("Errore nella conversione")
    exit()

private_key_b58 = base58.b58encode(keypair.secret_key).decode()
print("CHIAVE PRIVATA BASE58:")
print(private_key_b58)