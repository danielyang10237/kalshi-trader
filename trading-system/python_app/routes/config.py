"""Configuration endpoints"""

import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List

router = APIRouter(prefix="/api/config", tags=["config"])


class AddSeriesTickerRequest(BaseModel):
    ticker: str

CONFIG_DIR = Path(__file__).parent.parent.parent / "data_cache"
DATA_CACHE_DIR = Path(__file__).parent.parent.parent / "data_cache"

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


@router.get("/series-tickers")
def get_configured_series_tickers():
    """Get the list of configured series tickers"""
    try:
        config_file = CONFIG_DIR / "series_ticker.json"
        with open(config_file, 'r') as f:
            tickers = json.load(f)
        return {"series_tickers": tickers}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="series_ticker.json not found")
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


@router.post("/series-tickers")
def add_series_ticker(request: AddSeriesTickerRequest):
    """Add a series ticker to the configured list"""
    try:
        config_file = CONFIG_DIR / "series_ticker.json"
        
        # Read existing tickers
        if config_file.exists():
            with open(config_file, 'r') as f:
                tickers: List[str] = json.load(f)
        else:
            tickers = []
        
        # Check if already exists
        if request.ticker in tickers:
            return {"success": True, "message": "Ticker already in list", "tickers": tickers}
        
        # Add new ticker
        tickers.append(request.ticker)
        
        # Write back
        with open(config_file, 'w') as f:
            json.dump(tickers, f, indent=4)
        
        return {"success": True, "message": "Ticker added", "tickers": tickers}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
