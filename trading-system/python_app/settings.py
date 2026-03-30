from pydantic import BaseModel
from dotenv import load_dotenv
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
KALSHI_CLIENT_DIR = Path(__file__).parent / "kalshi_client"

# Load .env from project root
load_dotenv(PROJECT_ROOT / ".env")

# wss://api.elections.kalshi.com/trade-api/ws/v2
# wss://demo-api.kalshi.co/trade-api/ws/v2

SIM_EXCHANGE_URL = "ws://localhost:9000/trade-api/ws/v2"
LIVE_EXCHANGE_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"


class Settings(BaseModel):
    kalshi_api_key: str = os.getenv("KALSHI_API_KEY", "")
    # Default to kalshi_client folder, fallback to project root for backwards compatibility
    kalshi_private_key_path: str = os.getenv(
        "KALSHI_PRIVATE_KEY_PATH",
        str(KALSHI_CLIENT_DIR / "kalshi_key.pem")
    )
    # SIM_MODE=1 overrides KALSHI_WS_URL to point at the local sim exchange
    sim_mode: bool = os.getenv("SIM_MODE", "").lower() in ("1", "true", "yes")
    kalshi_ws_url: str = os.getenv(
        "KALSHI_WS_URL",
        SIM_EXCHANGE_URL if os.getenv("SIM_MODE", "").lower() in ("1", "true", "yes") else LIVE_EXCHANGE_URL,
    )
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
    paper_trade: bool = os.getenv("PAPER_TRADE", "").lower() in ("1", "true", "yes")

    @property
    def cors_origins(self) -> list[str]:
        """Allowed CORS origins (frontend + veridianteach.info)."""
        origins = [
            self.frontend_origin,
            "https://veridianteach.info",
            "http://veridianteach.info",
        ]
        # Deduplicate while preserving order
        return list(dict.fromkeys(origins))

settings = Settings()

if not settings.kalshi_api_key:
    raise RuntimeError("Missing KALSHI_API_KEY in .env")

if settings.sim_mode:
    print(f"[settings] *** SIM MODE *** connecting to {settings.kalshi_ws_url}")
else:
    print(f"[settings] Live mode: {settings.kalshi_ws_url}")