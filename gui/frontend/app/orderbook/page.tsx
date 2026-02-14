'use client';

import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'next/navigation';
import OrderbookLadder from '@/components/OrderbookLadder';
import CandlestickChart from '@/components/CandlestickChart';
import TradesFeed from '@/components/TradesFeed';
import { 
  fetchBalance, 
  fetchPositions, 
  Balance, 
  Position,
  placeBuyLimitOrder,
  placeSellLimitOrder,
  placeBuyMarketOrder,
  placeSellMarketOrder,
  fetchOrders,
  RestingOrder,
  cancelMarketOrders,
  initOrderGroup,
  fetchFills,
  Fill as ApiFill
} from '@/lib/api';
import Link from 'next/link';

// Type for user orders to pass to ladder
interface UserOrder {
  price: number;
  size: number;
  action: 'buy' | 'sell';
}

// Type for fill notifications from WebSocket
interface Fill {
  trade_id: string;
  order_id: string;
  market_ticker: string;
  side: string;
  action: string;
  yes_price: number;
  count: number;
  ts: number;
  is_taker: boolean;
}

// Type for individual limit order panels
interface LimitOrderPanel {
  id: string;
  action: 'buy' | 'sell';
  price: number | null;
  quantity: number;
  reduceOnly: boolean;
}

export default function OrderbookPage() {
  const searchParams = useSearchParams();
  const tickerParam = searchParams.get('ticker');
  const seriesParam = searchParams.get('series');
  const eventParam = searchParams.get('event');
  
  const [marketTicker, setMarketTicker] = useState<string>(tickerParam || '');
  const [seriesTicker, setSeriesTicker] = useState<string>(seriesParam || '');
  const [eventTicker, setEventTicker] = useState<string>(eventParam || '');
  const [activeMarket, setActiveMarket] = useState<string>(tickerParam || '');
  
  // Balance and positions state
  const [balance, setBalance] = useState<Balance | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [balanceError, setBalanceError] = useState<string | null>(null);
  
  // Order form state
  const [marketQuantity, setMarketQuantity] = useState<number>(1);
  const [marketReduceOnly, setMarketReduceOnly] = useState<boolean>(false);
  const [orderLoading, setOrderLoading] = useState<string | null>(null);
  const [orderMessage, setOrderMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  
  // Multiple limit order panels state
  const [limitPanels, setLimitPanels] = useState<LimitOrderPanel[]>([]);
  const [draggedPanelId, setDraggedPanelId] = useState<string | null>(null);

  // User's resting orders and recent fills
  const [userOrders, setUserOrders] = useState<UserOrder[]>([]);
  const [recentFills, setRecentFills] = useState<Fill[]>([]);
  
  // Trade history for position panel
  const [marketFills, setMarketFills] = useState<ApiFill[]>([]);
  const [fillsLoading, setFillsLoading] = useState(false);

  useEffect(() => {
    if (tickerParam) {
      setMarketTicker(tickerParam);
      setActiveMarket(tickerParam);
      
      // Try to extract series and event from market ticker if not provided
      // Market ticker format: SERIES-EVENT-DETAILS
      if (!seriesParam || !eventParam) {
        const parts = tickerParam.split('-');
        if (parts.length >= 2) {
          const extractedSeries = parts[0];
          const extractedEvent = parts.slice(0, 2).join('-');
          
          console.log('Extracted from ticker:', { extractedSeries, extractedEvent });
          
          if (!seriesParam) {
            setSeriesTicker(extractedSeries);
          }
          if (!eventParam) {
            setEventTicker(extractedEvent);
          }
        }
      }
    }
    if (seriesParam) setSeriesTicker(seriesParam);
    if (eventParam) setEventTicker(eventParam);
    
    // Debug log
    console.log('Orderbook params:', { 
      ticker: tickerParam, 
      series: seriesParam || 'extracted', 
      event: eventParam || 'extracted'
    });
  }, [tickerParam, seriesParam, eventParam]);

  // Pre-create order group when entering trading dashboard to reduce latency on first order
  useEffect(() => {
    if (!activeMarket) return;
    
    initOrderGroup(activeMarket)
      .then((res) => console.log(`[OrderGroup] Initialized for ${activeMarket}:`, res.order_group_id))
      .catch((err) => console.warn(`[OrderGroup] Failed to init:`, err));
  }, [activeMarket]);

  // Reusable function to refresh balance and positions
  const refreshBalanceAndPositions = async () => {
    try {
      setBalanceLoading(true);
      setBalanceError(null);
      
      const [balanceData, positionsData] = await Promise.all([
        fetchBalance(),
        fetchPositions(100, undefined, 'unsettled')
      ]);
      
      setBalance(balanceData);
      setPositions(positionsData.market_positions || []);
    } catch (err) {
      console.error('Failed to load balance/positions:', err);
      setBalanceError(err instanceof Error ? err.message : 'Failed to load');
    } finally {
      setBalanceLoading(false);
    }
  };

  // Fetch balance and positions on mount and every 30 seconds
  useEffect(() => {
    refreshBalanceAndPositions();
    const interval = setInterval(refreshBalanceAndPositions, 30000);
    return () => clearInterval(interval);
  }, []);

  // Fetch user's resting orders for the active market
  const refreshUserOrders = async () => {
    if (!activeMarket) {
      setUserOrders([]);
      return;
    }
    try {
      const ordersData = await fetchOrders(activeMarket);
      const orders: UserOrder[] = (ordersData.orders || []).map((o: RestingOrder) => ({
        // Kalshi may return yes_price=0 for sell orders, with the actual price in no_price
        price: o.yes_price > 0 ? o.yes_price : (100 - o.no_price),
        size: o.remaining_count,
        action: o.action as 'buy' | 'sell',
      }));
      setUserOrders(orders);
    } catch (err) {
      console.error('Failed to fetch user orders:', err);
    }
  };

  // Fetch fills for the active market (trade history)
  const refreshMarketFills = async () => {
    if (!activeMarket) {
      setMarketFills([]);
      return;
    }
    try {
      setFillsLoading(true);
      const fillsData = await fetchFills(activeMarket, 500);
      setMarketFills(fillsData.fills || []);
    } catch (err) {
      console.error('Failed to fetch market fills:', err);
    } finally {
      setFillsLoading(false);
    }
  };

  useEffect(() => {
    refreshUserOrders();
    refreshMarketFills();
    // Refresh orders every 10 seconds
    const interval = setInterval(refreshUserOrders, 10000);
    return () => clearInterval(interval);
  }, [activeMarket]);

  // Connect to fills WebSocket for real-time fill notifications
  useEffect(() => {
    const wsUrl = 'ws://localhost:8000/ws/fills';
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('[Fills WS] Connected');
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // Parse the inner message if it's a string
        const parsed = typeof data === 'string' ? JSON.parse(data) : data;
        
        if (parsed.type === 'fill' && parsed.msg) {
          const fill = parsed.msg as Fill;
          console.log('[Fills WS] Fill received:', fill);
          
          // Add to recent fills (keep max 10)
          setRecentFills(prev => [fill, ...prev].slice(0, 10));
          
          // Refresh orders, positions, and market fills after a fill
          refreshUserOrders();
          refreshBalanceAndPositions();
          refreshMarketFills();
        }
      } catch (err) {
        console.error('[Fills WS] Error parsing message:', err);
      }
    };

    ws.onerror = (err) => {
      console.error('[Fills WS] Error:', err);
    };

    ws.onclose = () => {
      console.log('[Fills WS] Disconnected');
    };

    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }, []);

  const handleConnect = () => {
    if (marketTicker.trim()) {
      setActiveMarket(marketTicker.trim().toUpperCase());
    }
  };

  // Order handlers
  const clearMessage = () => setOrderMessage(null);

  // Generate unique ID for panels
  const generatePanelId = () => `panel-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

  // Add a new limit order panel
  const addLimitPanel = useCallback((action: 'buy' | 'sell', price: number | null = null) => {
    const newPanel: LimitOrderPanel = {
      id: generatePanelId(),
      action,
      price,
      quantity: 1,
      reduceOnly: false,
    };
    setLimitPanels(prev => [...prev, newPanel]);
  }, []);

  // Remove a limit order panel
  const removeLimitPanel = useCallback((panelId: string) => {
    setLimitPanels(prev => prev.filter(p => p.id !== panelId));
  }, []);

  // Update a specific panel's fields
  const updateLimitPanel = useCallback((panelId: string, updates: Partial<LimitOrderPanel>) => {
    setLimitPanels(prev => prev.map(p => 
      p.id === panelId ? { ...p, ...updates } : p
    ));
  }, []);

  // Callback for when a price level is clicked in the orderbook ladder
  const handleLadderClick = (price: number, side: 'yes' | 'no') => {
    // Create a new panel with the clicked price and side
    const action = side === 'yes' ? 'buy' : 'sell';
    addLimitPanel(action, price);
  };

  // Drag and drop handlers
  const handleDragStart = (panelId: string) => {
    setDraggedPanelId(panelId);
  };

  const handleDragOver = (e: React.DragEvent, targetPanelId: string) => {
    e.preventDefault();
    if (!draggedPanelId || draggedPanelId === targetPanelId) return;
    
    setLimitPanels(prev => {
      const draggedIdx = prev.findIndex(p => p.id === draggedPanelId);
      const targetIdx = prev.findIndex(p => p.id === targetPanelId);
      if (draggedIdx === -1 || targetIdx === -1) return prev;
      
      const newPanels = [...prev];
      const [draggedPanel] = newPanels.splice(draggedIdx, 1);
      newPanels.splice(targetIdx, 0, draggedPanel);
      return newPanels;
    });
  };

  const handleDragEnd = () => {
    setDraggedPanelId(null);
  };
  
  // Execute a single limit order panel
  const handleLimitPanelExecute = async (panel: LimitOrderPanel) => {
    if (!activeMarket || panel.quantity < 1 || !panel.price || panel.price < 1 || panel.price > 99) return;
    
    setOrderLoading(`limit-${panel.id}`);
    setOrderMessage(null);
    
    try {
      const request = { ticker: activeMarket, count: panel.quantity, price: panel.price, reduce_only: panel.reduceOnly };
      const result = panel.action === 'buy' 
        ? await placeBuyLimitOrder(request)
        : await placeSellLimitOrder(request);
      
      setOrderMessage({ type: 'success', text: `${panel.action.toUpperCase()} limit order placed! Order ID: ${result.order.order_id.slice(0, 8)}...` });
      setTimeout(clearMessage, 5000);
      
      // Refresh balance, positions, orders, and fills after successful order
      refreshBalanceAndPositions();
      refreshUserOrders();
      refreshMarketFills();
    } catch (err) {
      setOrderMessage({ type: 'error', text: err instanceof Error ? err.message : 'Order failed' });
    } finally {
      setOrderLoading(null);
    }
  };

  const handleMarketOrder = async (action: 'buy' | 'sell') => {
    if (!activeMarket || marketQuantity < 1) return;
    
    setOrderLoading(`market-${action}`);
    setOrderMessage(null);
    
    try {
      const request = { ticker: activeMarket, count: marketQuantity, reduce_only: marketReduceOnly };
      const result = action === 'buy'
        ? await placeBuyMarketOrder(request)
        : await placeSellMarketOrder(request);
      
      setOrderMessage({ type: 'success', text: `${action.toUpperCase()} market order filled! Order ID: ${result.order.order_id.slice(0, 8)}...` });
      setTimeout(clearMessage, 5000);
      
      // Refresh balance, positions, and fills after successful order
      refreshBalanceAndPositions();
      refreshMarketFills();
    } catch (err) {
      setOrderMessage({ type: 'error', text: err instanceof Error ? err.message : 'Order failed' });
    } finally {
      setOrderLoading(null);
    }
  };

  const handleCancelMarketOrders = async () => {
    if (!activeMarket) return;
    
    setOrderLoading('cancel-orders');
    setOrderMessage(null);
    
    try {
      await cancelMarketOrders(activeMarket);
      setOrderMessage({ type: 'success', text: `All orders for ${activeMarket} cancelled!` });
      setTimeout(clearMessage, 5000);
      
      // Refresh everything
      refreshBalanceAndPositions();
      refreshUserOrders();
    } catch (err) {
      setOrderMessage({ type: 'error', text: err instanceof Error ? err.message : 'Cancel failed' });
    } finally {
      setOrderLoading(null);
    }
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      {/* Navigation */}
      <nav className="border-b border-gray-700 px-4 py-3">
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-4">
            <Link 
              href="/"
              className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded-lg transition-colors text-sm"
            >
              ← Markets
            </Link>
            <h1 className="text-xl font-bold">{activeMarket || 'Trading Panel'}</h1>
          </div>
          {activeMarket && (
            <button
              onClick={() => {
                setActiveMarket('');
                setMarketTicker('');
                setSeriesTicker('');
                setEventTicker('');
              }}
              className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded-lg transition-colors text-sm"
            >
              Change Market
            </button>
          )}
        </div>
      </nav>

      {!activeMarket ? (
        /* Market Ticker Input */
        <div className="max-w-2xl mx-auto mt-20 p-4">
          <div className="bg-gray-800 rounded-lg p-8 border border-gray-700">
            <h2 className="text-2xl font-bold mb-6">Connect to Market</h2>
            
            <div className="space-y-4">
              <div>
                <label htmlFor="ticker" className="block text-sm font-medium mb-2">
                  Market Ticker
                </label>
                <input
                  id="ticker"
                  type="text"
                  value={marketTicker}
                  onChange={(e) => setMarketTicker(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && handleConnect()}
                  placeholder="e.g., KXINFL-24FEB"
                  className="w-full p-3 bg-gray-700 border border-gray-600 rounded-lg text-white placeholder-gray-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
                <p className="mt-2 text-sm text-gray-400">
                  Enter a market ticker to view its live orderbook. You can find tickers in the main markets page.
                </p>
              </div>

              <button
                onClick={handleConnect}
                disabled={!marketTicker.trim()}
                className="w-full py-3 px-6 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg font-semibold transition-colors"
              >
                Connect to Orderbook
              </button>
            </div>

            <div className="mt-6 p-4 bg-blue-900/20 border border-blue-800 rounded-lg">
              <h3 className="font-semibold mb-2 text-blue-400">💡 Tips:</h3>
              <ul className="text-sm text-gray-300 space-y-1">
                <li>• Best Bid (Yes): Highest price buyers are willing to pay</li>
                <li>• Best Ask (No): Lowest price sellers are willing to accept</li>
                <li>• Spread: Difference between best bid and ask</li>
                <li>• Green = Yes side (buy contracts), Red = No side (sell contracts)</li>
              </ul>
            </div>
          </div>
        </div>
      ) : (
        /* Three Column Trading Layout */
        <div className="h-[calc(100vh-57px)] p-2 grid grid-cols-3 gap-2">
          {/* Left Column: Orderbook Ladder */}
          <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
            <OrderbookLadder marketTicker={activeMarket} onPriceClick={handleLadderClick} userOrders={userOrders} />
          </div>

          {/* Middle Column: Trades (top) + Candlesticks (bottom) */}
          <div className="flex flex-col gap-2">
            {/* Top Half: Recent Trades */}
            <div className="flex-1 bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
              <TradesFeed marketTicker={activeMarket} />
            </div>

            {/* Bottom Half: Candlestick Chart */}
            <div className="flex-1 bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
              {seriesTicker && eventTicker ? (
                <CandlestickChart 
                  seriesTicker={seriesTicker}
                  eventTicker={eventTicker}
                  marketTicker={activeMarket}
                />
              ) : (
                <div className="flex items-center justify-center h-full text-gray-400">
                  <div className="text-center p-4">
                    <div className="text-2xl mb-2">📈</div>
                    <p className="text-sm">Candlestick chart unavailable</p>
                    <p className="text-xs mt-1 text-gray-500">
                      Missing: {!seriesTicker ? 'series ' : ''}{!eventTicker ? 'event' : ''} ticker
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Right Column: Trade Execution */}
          <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden flex flex-col">
            <div className="p-3 border-b border-gray-700">
              <h3 className="font-semibold text-sm">Trade Execution</h3>
            </div>
            
            {/* Balance Section */}
            <div className="p-3 border-b border-gray-700 bg-gray-900/50">
              <div className="text-xs text-gray-400 mb-2">Account Balance</div>
              {balanceLoading ? (
                <div className="text-sm text-gray-500">Loading...</div>
              ) : balanceError ? (
                <div className="text-sm text-red-400">{balanceError}</div>
              ) : balance ? (
                <div className="grid grid-cols-2 gap-2">
                  <div className="bg-gray-800 rounded p-2">
                    <div className="text-xs text-gray-400">Available</div>
                    <div className="text-lg font-mono font-bold text-green-400">
                      ${(balance.balance / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                  </div>
                  {balance.payout !== undefined && (
                    <div className="bg-gray-800 rounded p-2">
                      <div className="text-xs text-gray-400">Payout</div>
                      <div className="text-lg font-mono font-bold text-blue-400">
                        ${(balance.payout / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-sm text-gray-500">No balance data</div>
              )}
            </div>

            {/* Trade History & Position Panel */}
            <div className="p-3 border-b border-gray-700 max-h-64 overflow-y-auto">
              <div className="text-xs text-gray-400 mb-2 flex justify-between items-center">
                <span>Position: {activeMarket}</span>
                <button
                  onClick={refreshMarketFills}
                  className="text-[10px] text-blue-400 hover:text-blue-300"
                >
                  {fillsLoading ? '...' : '↻'}
                </button>
              </div>
              {(() => {
                // Calculate position stats from fills
                let netPosition = 0;
                let totalSpentCents = 0; // Total absolute spent (all buys + sells)
                let cashFlowCents = 0;   // Net cash flow (negative = paid out, positive = received)
                
                // Group trades by price and action for display
                const tradesByPriceAction = new Map<string, { action: 'buy' | 'sell', price: number, count: number }>();
                
                marketFills.forEach(fill => {
                  const count = fill.count;
                  const price = fill.yes_price;
                  
                  if (fill.action === 'buy') {
                    netPosition += count;
                    totalSpentCents += price * count;
                    cashFlowCents -= price * count; // Paid out
                  } else {
                    netPosition -= count;
                    totalSpentCents += price * count;
                    cashFlowCents += price * count; // Received
                  }
                  
                  // Group for display
                  const key = `${fill.action}-${price}`;
                  const existing = tradesByPriceAction.get(key);
                  if (existing) {
                    existing.count += count;
                  } else {
                    tradesByPriceAction.set(key, { action: fill.action, price, count });
                  }
                });
                
                // If YES wins (settles to 1): each contract is worth 100¢
                // PnL if YES = cashFlowCents + (netPosition * 100)
                const gainIfYesCents = cashFlowCents + (netPosition * 100);
                
                // If NO wins (settles to 0): contracts worth 0¢
                // PnL if NO = cashFlowCents + 0
                const gainIfNoCents = cashFlowCents;
                
                // Sort trades for display: buys first, then sells, by price descending
                const sortedTrades = Array.from(tradesByPriceAction.values())
                  .sort((a, b) => {
                    if (a.action !== b.action) return a.action === 'buy' ? -1 : 1;
                    return b.price - a.price;
                  });
                
                if (marketFills.length === 0 && !fillsLoading) {
                  return (
                    <div className="text-sm text-gray-500 bg-gray-900 rounded p-3 text-center">
                      No trades in this market
                    </div>
                  );
                }
                
                return (
                  <div className="space-y-2">
                    {/* Stats Grid */}
                    <div className="grid grid-cols-2 gap-1.5 text-xs">
                      <div className="bg-gray-900 rounded p-1.5">
                        <div className="text-gray-500 text-[10px]">Net Position</div>
                        <div className={`font-mono font-bold ${netPosition >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {netPosition >= 0 ? '+' : ''}{netPosition}
                        </div>
                      </div>
                      <div className="bg-gray-900 rounded p-1.5">
                        <div className="text-gray-500 text-[10px]">Total Spent</div>
                        <div className="font-mono font-bold text-yellow-400">
                          {(totalSpentCents / 100).toFixed(2)}¢
                        </div>
                      </div>
                      <div className="bg-gray-900 rounded p-1.5">
                        <div className="text-gray-500 text-[10px]">If YES (1)</div>
                        <div className={`font-mono font-bold ${gainIfYesCents >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {gainIfYesCents >= 0 ? '+' : ''}{(gainIfYesCents / 100).toFixed(2)}¢
                        </div>
                      </div>
                      <div className="bg-gray-900 rounded p-1.5">
                        <div className="text-gray-500 text-[10px]">If NO (0)</div>
                        <div className={`font-mono font-bold ${gainIfNoCents >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {gainIfNoCents >= 0 ? '+' : ''}{(gainIfNoCents / 100).toFixed(2)}¢
                        </div>
                      </div>
                    </div>
                    
                    {/* Individual Trades List */}
                    {sortedTrades.length > 0 && (
                      <div className="mt-2">
                        <div className="text-[10px] text-gray-500 mb-1">Trade History ({marketFills.length} fills)</div>
                        <div className="space-y-0.5 max-h-24 overflow-y-auto">
                          {sortedTrades.map((trade, idx) => (
                            <div 
                              key={idx}
                              className={`flex justify-between items-center text-[10px] p-1 rounded ${
                                trade.action === 'buy' ? 'bg-green-900/20' : 'bg-red-900/20'
                              }`}
                            >
                              <span className={trade.action === 'buy' ? 'text-green-400' : 'text-red-400'}>
                                {trade.action === 'buy' ? '🟢 BUY' : '🔴 SELL'}
                              </span>
                              <span className="font-mono text-gray-300">
                                {trade.count} @ {trade.price}¢
                              </span>
                              <span className="font-mono text-gray-500">
                                = {((trade.count * trade.price) / 100).toFixed(2)}¢
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}
            </div>

            {/* Order Form */}
            <div className="flex-1 flex flex-col p-3 overflow-y-auto gap-3">
              {/* Order Status Message */}
              {orderMessage && (
                <div className={`p-2 rounded text-xs ${
                  orderMessage.type === 'success' 
                    ? 'bg-green-900/50 border border-green-700 text-green-300'
                    : 'bg-red-900/50 border border-red-700 text-red-300'
                }`}>
                  {orderMessage.text}
                </div>
              )}

{/* Market Orders Panel */}
              <div className="p-2 bg-gray-900/50 rounded-lg border border-gray-700/50">
                <div className="text-xs font-medium text-yellow-500/80 mb-2">Market Orders</div>

                {/* Market Quantity */}
                <div className="mb-2">
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setMarketQuantity(Math.max(1, marketQuantity - 1))}
                      className="w-6 h-6 bg-gray-700 hover:bg-gray-600 rounded text-sm font-bold"
                    >−</button>
                    <input
                      type="number"
                      value={marketQuantity}
                      onChange={(e) => setMarketQuantity(Math.max(1, parseInt(e.target.value) || 1))}
                      className="flex-1 h-6 bg-gray-800 border border-gray-600 rounded text-center font-mono text-xs"
                      min={1}
                    />
                    <button
                      onClick={() => setMarketQuantity(marketQuantity + 1)}
                      className="w-6 h-6 bg-gray-700 hover:bg-gray-600 rounded text-sm font-bold"
                    >+</button>
                  </div>
                </div>

                {/* Market Reduce Only */}
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[9px] text-gray-400">Reduce Only</span>
                  <button
                    onClick={() => setMarketReduceOnly(!marketReduceOnly)}
                    className={`w-8 h-4 rounded-full transition-colors relative ${marketReduceOnly ? 'bg-yellow-600' : 'bg-gray-600'}`}
                  >
                    <div className={`w-3 h-3 bg-white rounded-full absolute top-0.5 transition-all ${marketReduceOnly ? 'left-4' : 'left-0.5'}`} />
                  </button>
                </div>
                
                {/* Market Order Buttons */}
                <div className="grid grid-cols-2 gap-1.5 mb-2">
                  <button
                    onClick={() => handleMarketOrder('buy')}
                    disabled={orderLoading !== null}
                    className="py-1.5 bg-green-800/60 hover:bg-green-700/70 disabled:bg-gray-700 disabled:text-gray-500 rounded text-xs font-medium transition-colors border border-green-700/30"
                  >
                    {orderLoading === 'market-buy' ? '...' : 'Buy Now'}
                  </button>
                  <button
                    onClick={() => handleMarketOrder('sell')}
                    disabled={orderLoading !== null}
                    className="py-1.5 bg-red-800/60 hover:bg-red-700/70 disabled:bg-gray-700 disabled:text-gray-500 rounded text-xs font-medium transition-colors border border-red-700/30"
                  >
                    {orderLoading === 'market-sell' ? '...' : 'Sell Now'}
                  </button>
                </div>

                {/* Add Limit Order Buttons */}
                <div className="pt-2 border-t border-gray-700/50">
                  <div className="text-[9px] text-gray-500 mb-1.5 text-center">Add Limit Order</div>
                  <div className="grid grid-cols-2 gap-1.5">
                    <button
                      onClick={() => addLimitPanel('buy')}
                      className="py-1.5 bg-green-900/40 hover:bg-green-800/60 rounded text-xs font-medium transition-colors border border-green-600/30 text-green-400"
                    >
                      + Buy Limit
                    </button>
                    <button
                      onClick={() => addLimitPanel('sell')}
                      className="py-1.5 bg-red-900/40 hover:bg-red-800/60 rounded text-xs font-medium transition-colors border border-red-600/30 text-red-400"
                    >
                      + Sell Limit
                    </button>
                  </div>
                </div>
              </div>

              {/* Limit Order Panels */}
              {limitPanels.map((panel) => (
                <div
                  key={panel.id}
                  draggable
                  onDragStart={() => handleDragStart(panel.id)}
                  onDragOver={(e) => handleDragOver(e, panel.id)}
                  onDragEnd={handleDragEnd}
                  className={`p-2 rounded-lg border transition-all cursor-move ${
                    panel.action === 'buy' 
                      ? 'bg-green-900/30 border-green-600/50' 
                      : 'bg-red-900/30 border-red-600/50'
                  } ${draggedPanelId === panel.id ? 'opacity-50 scale-95' : ''}`}
                >
                  {/* Panel Header */}
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-gray-500 cursor-grab">⋮⋮</span>
                      <span className={`text-xs font-bold ${
                        panel.action === 'buy' ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {panel.action === 'buy' ? '🟢 BUY LIMIT' : '🔴 SELL LIMIT'}
                      </span>
                    </div>
                    <button
                      onClick={() => removeLimitPanel(panel.id)}
                      className="w-5 h-5 flex items-center justify-center text-gray-500 hover:text-red-400 hover:bg-red-900/30 rounded transition-colors"
                    >
                      ✕
                    </button>
                  </div>

                  {/* Price Input */}
                  <div className="mb-2">
                    <div className="text-[9px] text-gray-500 mb-0.5">Price (¢)</div>
                    <input
                      type="number"
                      value={panel.price || ''}
                      onChange={(e) => updateLimitPanel(panel.id, { price: parseInt(e.target.value) || null })}
                      placeholder="Click ladder"
                      className="w-full h-7 bg-gray-800 border border-gray-600 rounded text-center font-mono text-sm"
                      min={1}
                      max={99}
                    />
                  </div>

                  {/* Quantity Input */}
                  <div className="mb-2">
                    <div className="text-[9px] text-gray-500 mb-0.5">Quantity</div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => updateLimitPanel(panel.id, { quantity: Math.max(1, panel.quantity - 1) })}
                        className="w-6 h-6 bg-gray-700 hover:bg-gray-600 rounded text-sm font-bold"
                      >−</button>
                      <input
                        type="number"
                        value={panel.quantity}
                        onChange={(e) => updateLimitPanel(panel.id, { quantity: Math.max(1, parseInt(e.target.value) || 1) })}
                        className="flex-1 h-6 bg-gray-800 border border-gray-600 rounded text-center font-mono text-xs"
                        min={1}
                      />
                      <button
                        onClick={() => updateLimitPanel(panel.id, { quantity: panel.quantity + 1 })}
                        className="w-6 h-6 bg-gray-700 hover:bg-gray-600 rounded text-sm font-bold"
                      >+</button>
                    </div>
                  </div>

                  {/* Reduce Only Toggle */}
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-[9px] text-gray-500">Reduce Only</span>
                    <button
                      onClick={() => updateLimitPanel(panel.id, { reduceOnly: !panel.reduceOnly })}
                      className={`w-8 h-4 rounded-full transition-colors relative ${panel.reduceOnly ? 'bg-yellow-600' : 'bg-gray-600'}`}
                    >
                      <div className={`w-3 h-3 bg-white rounded-full absolute top-0.5 transition-all ${panel.reduceOnly ? 'left-4' : 'left-0.5'}`} />
                    </button>
                  </div>

                  {/* Execute Button */}
                  <button
                    onClick={() => handleLimitPanelExecute(panel)}
                    disabled={orderLoading !== null || !panel.price}
                    className={`w-full py-1.5 rounded font-bold text-xs transition-colors ${
                      panel.price
                        ? panel.action === 'buy'
                          ? 'bg-green-600 hover:bg-green-500 text-white'
                          : 'bg-red-600 hover:bg-red-500 text-white'
                        : 'bg-gray-700 text-gray-500 cursor-not-allowed'
                    }`}
                  >
                    {orderLoading === `limit-${panel.id}` ? '...' : `EXECUTE ${panel.action.toUpperCase()}`}
                  </button>

                  {/* Order Summary */}
                  {panel.price && (
                    <div className="mt-1 text-[9px] text-gray-500 text-center">
                      {panel.action.toUpperCase()} {panel.quantity} @ {panel.price}¢
                    </div>
                  )}
                </div>
              ))}

              {/* Recent Fills */}
              <div className="p-3 bg-gray-900/80 rounded-lg border border-purple-600/50">
                <div className="text-sm font-semibold text-purple-400 mb-2">Recent Fills</div>
                {recentFills.length === 0 ? (
                  <div className="text-xs text-gray-500 text-center py-2">No fills yet</div>
                ) : (
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {recentFills.map((fill, idx) => (
                      <div 
                        key={fill.trade_id || idx} 
                        className={`text-xs p-1.5 rounded ${
                          fill.action === 'buy' ? 'bg-green-900/30' : 'bg-red-900/30'
                        }`}
                      >
                        <div className="flex justify-between items-center">
                          <span className={fill.action === 'buy' ? 'text-green-400' : 'text-red-400'}>
                            {fill.action.toUpperCase()}
                          </span>
                          <span className="font-mono text-gray-300">
                            {fill.count} @ {fill.yes_price}¢
                          </span>
                        </div>
                        <div className="flex justify-between items-center text-[10px] text-gray-500">
                          <span>{fill.market_ticker?.slice(0, 12)}...</span>
                          <span>{fill.is_taker ? 'Taker' : 'Maker'}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Cancel Orders Button */}
              {userOrders.length > 0 && (
                <button
                  onClick={handleCancelMarketOrders}
                  disabled={orderLoading !== null}
                  className="w-full py-2 bg-red-900/50 hover:bg-red-800/70 border border-red-600/50 rounded-lg text-red-400 text-sm font-medium transition-colors disabled:opacity-50"
                >
                  {orderLoading === 'cancel-orders' ? 'Cancelling...' : `Cancel All Orders (${userOrders.length})`}
                </button>
              )}

            </div>
          </div>
        </div>
      )}
    </div>
  );
}

