import asyncio
import json
import os
from config import AppConfig, SettingsStore

async def test():
    config = AppConfig.from_env()
    store = SettingsStore(config.data_dir)
    
    # Leggi il file JSON direttamente
    settings_file = os.path.join(config.data_dir, "settings.json")
    
    if not os.path.exists(settings_file):
        print(f"❌ File non trovato: {settings_file}")
        print(f"📁 Cartella dati: {config.data_dir}")
        return
    
    with open(settings_file, "r") as f:
        data = json.load(f)
    
    if not data:
        print("❌ Nessun chat_id salvato. Hai fatto /start nel bot?")
        return
    
    print(f"✅ Chat ID trovati: {len(data)}\n")
    
    for chat_id_str, settings_dict in data.items():
        print(f"Chat ID: {chat_id_str}")
        print(f"  Auto Snipe: {settings_dict.get('auto_snipe_enabled', False)}")
        print(f"  Budget per trade: {settings_dict.get('budget_sol_per_trade', 0)} SOL")
        print(f"  Slippage: {settings_dict.get('slippage_bps', 500)} bps")
        print(f"  Max posizioni aperte: {settings_dict.get('max_concurrent_positions', 3)}")
        print("---\n")

asyncio.run(test())