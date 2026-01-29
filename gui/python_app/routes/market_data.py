"""Market data endpoints - series, markets, events, candlesticks"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from ..kalshi_client import KalshiClient
from ..settings import settings

router = APIRouter(prefix="/api", tags=["market-data"])

# Shared client instance
kalshi = KalshiClient(
    api_url=settings.kalshi_ws_url,
    key_id=settings.kalshi_api_key,
    private_key_path=settings.kalshi_private_key_path,
)


@router.get("/series")
def get_series(
    limit: int = Query(default=100, le=200),
    cursor: Optional[str] = None,
    tags: Optional[str] = None
):
    """Get all series from Kalshi"""
    try:
        tags_list = tags.split(",") if tags else None
        return kalshi.series.get_all(limit=limit, cursor=cursor, tags=tags_list)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/markets")
def get_markets(
    series_ticker: Optional[str] = None,
    limit: int = Query(default=100, le=200),
    cursor: Optional[str] = None,
    status: Optional[str] = None
):
    """Get markets, optionally filtered by series"""
    try:
        return kalshi.markets.get_all(
            series_ticker=series_ticker,
            limit=limit,
            cursor=cursor,
            status=status
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/markets/{ticker}")
def get_market(ticker: str):
    """Get a specific market by ticker"""
    try:
        return kalshi.markets.get(ticker)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/events")
def get_events(
    series_ticker: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, le=1000),
    cursor: Optional[str] = None,
    with_nested_markets: bool = False
):
    """Get events, optionally filtered by series"""
    try:
        return kalshi.events.get_all(
            series_ticker=series_ticker,
            status=status,
            limit=limit,
            cursor=cursor,
            with_nested_markets=with_nested_markets
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/events/{event_ticker}/markets")
def get_markets_by_event(
    event_ticker: str,
    status: Optional[str] = None,
    limit: int = Query(default=100, le=1000),
    cursor: Optional[str] = None
):
    """Get all markets for a specific event"""
    try:
        return kalshi.markets.get_by_event(
            event_ticker=event_ticker,
            status=status,
            limit=limit,
            cursor=cursor
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/series/{series_ticker}/events/{event_ticker}/candlesticks")
def get_event_candlesticks(
    series_ticker: str,
    event_ticker: str,
    start_ts: int = Query(..., description="Start timestamp"),
    end_ts: int = Query(..., description="End timestamp"),
    period_interval: int = Query(default=1, description="Period in minutes (1, 60, or 1440)")
):
    """Get candlesticks for an event"""
    try:
        return kalshi.events.get_candlesticks(
            series_ticker=series_ticker,
            event_ticker=event_ticker,
            start_ts=start_ts,
            end_ts=end_ts,
            period_interval=period_interval
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

