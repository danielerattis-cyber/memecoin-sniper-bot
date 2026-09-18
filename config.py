"""
Configurazione dell'applicazione e impostazioni per-utente.
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
    data_dir: str

    @staticmethod
    def from_env() -> "AppConfig":
        return AppConfig(
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "8901225398:AAHnlNtoNMYfFyJEuqqrK3S5F6Zdd6HYix4"),
            telegram_owner_id=int(os.getenv("TELEGRAM_OWNER_ID", "8511493543")),
            rpc_http=os.getenv("RPC_HTTP", "https://api.mainnet-beta.solana.com"),
            rpc_wss=os.getenv("RPC_WSS", "wss://api.mainnet-beta.solana.com"),
            data_dir=os.getenv("DATA_DIR", "./data"),
        )


@dataclass
class UserSettings:
    """Impostazioni regolabili da Telegram."""
    
    max_buy_sol: float = 0.01
    slippage_bps: int = 300
    priority_fee: str = "auto"
    stop_loss_pct: float = 25.0
    trailing_stop_pct: float = 15.0
    take_profit_levels: List[Tuple[float, float]] = field(
        default_factory=lambda: [(50.0, 0.3), (100.0, 0.3), (200.0, 0.4)]
    )
    max_hold_minutes: int = 60
    min_liquidity_sol: float = 0.0
    max_top10_holder_pct: float = 40.0
    require_mint_authority_revoked: bool = True
    require_freeze_authority_revoked: bool = True
    min_safety_score: int = 60
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
    """Carica/salva UserSettings su file JSON."""

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
