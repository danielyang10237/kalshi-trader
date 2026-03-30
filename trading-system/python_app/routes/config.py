"""Configuration endpoints"""

import json
import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

router = APIRouter(prefix="/api/config", tags=["config"])


class AddSeriesTickerRequest(BaseModel):
    category: str
    ticker: str

CONFIG_DIR = Path(__file__).parent.parent.parent / "data_cache"
DATA_CACHE_DIR = Path(__file__).parent.parent.parent / "data_cache"

# Model directories
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent  # kalshi-bot/
_NBA_RESEARCH_DIR = _PROJECT_ROOT / "nba"
_NBA_APP_DIR = Path(__file__).parent.parent / "nba"

CATEGORIES_BLACKLIST = [
            "Climate and Weather",
            "Companies",
            "Crypto",
            "Economics",
            "Elections",
            "Entertainment",
            "Financials",
            "Mentions",
            "Politics",
            "Science and Technology",
            "Social",
            "Transportation",
        ]


@router.get("/trading-events")
def get_trading_events():
    """Get trading events grouped by category (e.g. nba -> [KXNBAGAME, ...])"""
    try:
        config_file = CONFIG_DIR / "trading_events.json"
        with open(config_file, 'r') as f:
            events = json.load(f)
        return {"trading_events": events}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="trading_events.json not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Keep old endpoint for backward compatibility
@router.get("/series-tickers")
def get_configured_series_tickers():
    """Get all series tickers (flattened from trading_events.json)"""
    try:
        config_file = CONFIG_DIR / "trading_events.json"
        with open(config_file, 'r') as f:
            events = json.load(f)
        # Flatten all tickers across categories
        all_tickers = []
        for category_tickers in events.values():
            all_tickers.extend(category_tickers)
        return {"series_tickers": all_tickers}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="trading_events.json not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/series-tags")
def get_series_tags():
    """Get the list of series tags"""
    fetched_tags = json.load(open(DATA_CACHE_DIR / "series_tags.json"))
    tags_by_categories = fetched_tags["tags_by_categories"]
    all_tags = []
    for category in tags_by_categories:
        if category not in CATEGORIES_BLACKLIST:
            all_tags.extend(tags_by_categories[category])
    return all_tags


@router.post("/trading-events")
def add_series_ticker(request: AddSeriesTickerRequest):
    """Add a series ticker to a category in trading_events.json"""
    try:
        config_file = CONFIG_DIR / "trading_events.json"

        if config_file.exists():
            with open(config_file, 'r') as f:
                events: Dict[str, List[str]] = json.load(f)
        else:
            events = {}

        if request.category not in events:
            events[request.category] = []

        if request.ticker in events[request.category]:
            return {"success": True, "message": "Ticker already in category", "trading_events": events}

        events[request.category].append(request.ticker)

        with open(config_file, 'w') as f:
            json.dump(events, f, indent=4)

        return {"success": True, "message": "Ticker added", "trading_events": events}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nba/deploy-models")
def deploy_nba_models():
    """Copy the latest prior and posterior models from nba/ research dir into the trading app."""
    copied = []
    errors = []

    # --- Prior models ---
    src_prior_dir = _NBA_RESEARCH_DIR / "prior_models"
    dst_prior_dir = _NBA_APP_DIR / "prior_models"
    prior_files = ["xgboost_prior_deploy.pkl", "config_deploy.json"]

    for fname in prior_files:
        src = src_prior_dir / fname
        dst = dst_prior_dir / fname
        if not src.exists():
            errors.append(f"Prior source missing: {fname}")
            continue
        try:
            dst_prior_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(f"prior_models/{fname}")
        except Exception as e:
            errors.append(f"Failed to copy {fname}: {e}")

    # --- Posterior models ---
    src_posterior_dir = _NBA_RESEARCH_DIR / "posterior_models" / "deployment"
    dst_posterior_dir = _NBA_APP_DIR / "posterior_modeling"

    if not src_posterior_dir.exists():
        errors.append(f"Posterior source dir missing: {src_posterior_dir}")
    else:
        dst_posterior_dir.mkdir(parents=True, exist_ok=True)
        for src_file in src_posterior_dir.glob("*.pkl"):
            dst = dst_posterior_dir / src_file.name
            try:
                shutil.copy2(src_file, dst)
                copied.append(f"posterior_modeling/{src_file.name}")
            except Exception as e:
                errors.append(f"Failed to copy {src_file.name}: {e}")

    if errors and not copied:
        raise HTTPException(status_code=500, detail="; ".join(errors))

    return {
        "success": True,
        "copied": copied,
        "errors": errors,
    }
