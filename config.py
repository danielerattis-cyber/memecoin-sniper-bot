"""
Configurazione dell'applicazione e impostazioni per-utente.

Le impostazioni utente sono salvate come JSON su disco (niente database,
per restare semplice). In produzione con più utenti, sostituisci
SettingsStore con una vera tabella DB.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Tuple

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    telegram_token: str
    telegram_owner_id: int
    rpc_http: str
    rpc_wss: str
    jupiter_api: str
    keystore_path: str
    data_dir: str

    @staticmethod
    def from_env() -> "AppConfig":
        return AppConfig(
            telegram_token=_require_env("TELEGRAM_BOT_TOKEN"),
            telegram_owner_id=int(_require_env("TELEGRAM_OWNER_ID")),
            rpc_http=_require_env("SOLANA_RPC_HTTP"),
            rpc_wss=_require_env("SOLANA_RPC_WSS"),
            jupiter_api=os.getenv("JUPITER_QUOTE_API", "https://api.jup.ag/swap/v1"),
            keystore_path=os.getenv("WALLET_KEYSTORE_PATH", "./wallet.enc"),
            data_dir=os.getenv("DATA_DIR", "./data"),
        )


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Variabile d'ambiente mancante: {name}. Controlla il tuo .env")
    return value


@dataclass
class UserSettings:
    """Tutte le impostazioni regolabili da Telegram tramite bottoni."""

    # Dimensione posizione
    max_buy_sol: float = 0.02

    # Esecuzione
    slippage_bps: int = 300          # 3%
    priority_fee: str = "auto"       # "auto" oppure valore fisso in micro-lamports

    # Uscita
    stop_loss_pct: float = 25.0
    trailing_stop_pct: float = 15.0
    take_profit_levels: List[Tuple[float, float]] = field(
        default_factory=lambda: [(50.0, 0.3), (100.0, 0.3), (200.0, 0.4)]
    )  # (soglia % guadagno, frazione posizione da vendere)
    max_hold_minutes: int = 60

    # Filtri di sicurezza (soglie minime per comprare in automatico)
    min_liquidity_sol: float = 0.0
    max_top10_holder_pct: float = 40.0
    require_mint_authority_revoked: bool = True
    require_freeze_authority_revoked: bool = True
    min_safety_score: int = 60       # 0-100, sotto questa soglia niente autobuy

    # Operatività
    auto_snipe_enabled: bool = False
    max_concurrent_positions: int = 3
    notify_on_low_score_detections: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @staticmethod
    def from_dict(d: dict) -> "UserSettings":
        defaults = UserSettings()
        for key, value in d.items():
            if hasattr(defaults, key):
                setattr(defaults, key, value)
        return defaults


class SettingsStore:
    """Carica/salva UserSettings su file JSON, uno per chat_id."""

    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "settings"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_id: int) -> Path:
        return self.dir / f"{chat_id}.json"

    def get(self, chat_id: int) -> UserSettings:
        path = self._path(chat_id)
        if not path.exists():
            settings = UserSettings()
            self.save(chat_id, settings)
            return settings
        return UserSettings.from_dict(json.loads(path.read_text()))

    def save(self, chat_id: int, settings: UserSettings) -> None:
        self._path(chat_id).write_text(settings.to_json())
