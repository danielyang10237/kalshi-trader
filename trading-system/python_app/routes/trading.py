"""Trading endpoints - order placement and fills"""

from typing import Dict, Literal, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..kalshi_client import KalshiClient
from ..settings import settings
from ..fills_cache import get_fills as get_cached_fills, clear_fills as clear_cached_fills

router = APIRouter(prefix="/api/trading", tags=["trading"])

# Shared client instance
kalshi = KalshiClient(
    api_url=settings.kalshi_ws_url,
    key_id=settings.kalshi_api_key,
    private_key_path=settings.kalshi_private_key_path,
)

# In-memory mapping of market ticker -> order group ID
# This allows us to cancel all orders for a specific market
market_order_groups: Dict[str, str] = {}


def get_or_create_order_group(ticker: str) -> str:
    """Get existing order group for a market, or create a new one"""
    if ticker in market_order_groups:
        return market_order_groups[ticker]
    
    # Create a new order group with a high contracts limit
    result = kalshi.trades.create_order_group(contracts_limit=100000)
    # Response can be {"order_group_id": "..."} or {"order_group": {"order_group_id": "..."}}
    order_group_id = result.get("order_group_id") or result.get("order_group", {}).get("order_group_id")
    if not order_group_id:
        raise Exception(f"Failed to create order group: {result}")
    
    market_order_groups[ticker] = order_group_id
    print(f"[trading] Created order group {order_group_id} for market {ticker}")
    return order_group_id


# =============================================================================
# Request Models
# =============================================================================

class LimitOrderRequest(BaseModel):
    ticker: str
    count: int = Field(..., ge=1)
    price: int = Field(..., ge=1, le=99)
    client_order_id: Optional[str] = None
    order_group_id: Optional[str] = None
    time_in_force: Literal["fill_or_kill", "good_till_canceled", "immediate_or_cancel"] = "good_till_canceled"
    post_only: Optional[bool] = None
    reduce_only: Optional[bool] = None


class MarketOrderRequest(BaseModel):
    ticker: str
    count: int = Field(..., ge=1)
    client_order_id: Optional[str] = None
    order_group_id: Optional[str] = None
    reduce_only: Optional[bool] = None


# =============================================================================
# Balance & Portfolio
# =============================================================================

@router.get("/balance")
def get_balance():
    """Get account balance"""
    try:
        return kalshi.portfolio.get_balance()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
def get_positions(
    limit: int = Query(default=100, le=1000),
    cursor: Optional[str] = None,
    settlement_status: Optional[str] = None,
):
    """Get user's positions"""
    try:
        return kalshi.portfolio.get_positions(
            limit=limit,
            cursor=cursor,
            settlement_status=settlement_status
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Orders
# =============================================================================

@router.get("/orders")
def get_orders(
    ticker: Optional[str] = None,
    limit: int = Query(default=100, le=1000),
    cursor: Optional[str] = None,
):
    """Get user's resting orders, optionally filtered by market ticker"""
    try:
        if ticker:
            return kalshi.trades.get_orders_by_market_ticker(
                ticker=ticker,
                limit=limit,
                cursor=cursor
            )
        else:
            return kalshi.trades.get_orders(limit=limit, cursor=cursor)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/orders/all")
def cancel_all_orders():
    """Cancel all resting orders"""
    try:
        result = kalshi.trades.cancel_all_orders()
        return {"success": True, "cancelled": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/orders/init-group/{ticker}")
def init_order_group(ticker: str):
    """Pre-create an order group for a market to reduce latency on first order"""
    try:
        order_group_id = get_or_create_order_group(ticker)
        return {"success": True, "order_group_id": order_group_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/orders/market/{ticker}")
def cancel_market_orders(ticker: str):
    """Cancel all resting orders for a specific market by deleting its order group"""
    try:
        if ticker not in market_order_groups:
            return {"success": True, "message": "No order group for this market"}
        
        order_group_id = market_order_groups[ticker]
        result = kalshi.trades.delete_order_group(order_group_id)
        
        # Remove from our mapping since the group is now deleted
        del market_order_groups[ticker]
        
        return {"success": True, "deleted_group": order_group_id, "result": result}
    except Exception as e:
        # If the group was already deleted or doesn't exist, clean up our mapping
        if ticker in market_order_groups:
            del market_order_groups[ticker]
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Fills
# =============================================================================

@router.get("/fills")
def get_fills(
    ticker: Optional[str] = None,
    limit: int = Query(default=100, le=1000),
    cursor: Optional[str] = None,
):
    """Get user's trade fills"""
    try:
        return kalshi.trades.get_fills(ticker=ticker, limit=limit, cursor=cursor)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fills/cached")
def get_fills_cached(
    ticker: Optional[str] = None,
    limit: int = Query(default=100, le=1000),
):
    """Get locally cached trade fills (persists across restarts)"""
    return {"fills": get_cached_fills(ticker=ticker, limit=limit)}


@router.delete("/fills/cached")
def delete_fills_cached():
    """Clear all locally cached fills"""
    clear_cached_fills()
    return {"success": True}


# =============================================================================
# Limit Orders
# =============================================================================

@router.post("/orders/buy/limit")
def buy_limit_order(req: LimitOrderRequest):
    """Place a buy limit order (YES side)"""
    try:
        # Auto-assign to market's order group for easy cancellation
        order_group_id = req.order_group_id or get_or_create_order_group(req.ticker)
        
        return kalshi.trades.buy_limit_order(
            ticker=req.ticker,
            count=req.count,
            price=req.price,
            client_order_id=req.client_order_id,
            order_group_id=order_group_id,
            time_in_force=req.time_in_force,
            post_only=req.post_only,
            reduce_only=req.reduce_only,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/orders/sell/limit")
def sell_limit_order(req: LimitOrderRequest):
    """Place a sell limit order (YES side)"""
    try:
        # Auto-assign to market's order group for easy cancellation
        order_group_id = req.order_group_id or get_or_create_order_group(req.ticker)
        
        return kalshi.trades.sell_limit_order(
            ticker=req.ticker,
            count=req.count,
            price=req.price,
            client_order_id=req.client_order_id,
            order_group_id=order_group_id,
            time_in_force=req.time_in_force,
            post_only=req.post_only,
            reduce_only=req.reduce_only,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Market Orders
# =============================================================================

@router.post("/orders/buy/market")
def buy_market_order(req: MarketOrderRequest):
    """Place a buy market order (YES side)"""
    try:
        # Auto-assign to market's order group for consistency
        order_group_id = req.order_group_id or get_or_create_order_group(req.ticker)
        
        return kalshi.trades.buy_market_order(
            ticker=req.ticker,
            count=req.count,
            client_order_id=req.client_order_id,
            order_group_id=order_group_id,
            reduce_only=req.reduce_only,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/orders/sell/market")
def sell_market_order(req: MarketOrderRequest):
    """Place a sell market order (YES side)"""
    try:
        # Auto-assign to market's order group for consistency
        order_group_id = req.order_group_id or get_or_create_order_group(req.ticker)
        
        return kalshi.trades.sell_market_order(
            ticker=req.ticker,
            count=req.count,
            client_order_id=req.client_order_id,
            order_group_id=order_group_id,
            reduce_only=req.reduce_only,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

