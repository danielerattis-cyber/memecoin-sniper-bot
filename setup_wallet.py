import getpass
import os
from dotenv import load_dotenv
from mnemonic import Mnemonic
from solders.keypair import Keypair

load_dotenv()

def main():
    print("=== Setup wallet cifrato ===")
    print("La chiave privata NON verrà mai inviata in rete: viene cifrata e")
    print("salvata solo su questo disco.\n")
    
    seed_words = getpass.getpass("Incolla le 24 parole di Phantom separate da spazi: ").strip()
    
    # Converti le parole in chiave privata
    try:
        mnemo = Mnemonic("english")
        seed = mnemo.to_seed(seed_words)
        private_key_bytes = seed[:32]
        
        # Converti in base58 per il formato richiesto
        import base58
        private_key = base58.b58encode(private_key_bytes).decode()
    except Exception as e:
        print(f"❌ Errore nella conversione delle parole: {e}")
        return
    
    password = getpass.getpass("Scegli una password per cifrarla: ")
    password_confirm = getpass.getpass("Conferma password: ")
    
    if password != password_confirm:
        print("❌ Le password non coincidono. Riprova.")
        return
    
    path = os.getenv("WALLET_KEYSTORE_PATH", "./wallet.enc")
    
    from security import create_keystore
    create_keystore(private_key, password, path)
    
    print(f"\n✅ Wallet cifrato salvato in: {path}")
    print("Ricordati questa password: ti servirà per avviare il bot con `python main.py`.")

if __name__ == "__main__":
    main()

