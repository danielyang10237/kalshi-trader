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

class Settings(BaseModel):
    kalshi_api_key: str = os.getenv("KALSHI_API_KEY", "")
    # Default to kalshi_client folder, fallback to project root for backwards compatibility
    kalshi_private_key_path: str = os.getenv(
        "KALSHI_PRIVATE_KEY_PATH", 
        str(KALSHI_CLIENT_DIR / "kalshi_key.pem")
    )
    kalshi_ws_url: str = os.getenv("KALSHI_WS_URL", "wss://api.elections.kalshi.com/trade-api/ws/v2")
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")

settings = Settings()

if not settings.kalshi_api_key:
    raise RuntimeError("Missing KALSHI_API_KEY in .env")