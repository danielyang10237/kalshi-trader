'use client';

import { useState, useEffect, useRef, useCallback } from 'react';

interface OrderbookLevel {
  price: number;
  priceDollars: string;
  size: number;
}

interface OrderbookState {
  yes: OrderbookLevel[];
  no: OrderbookLevel[];
  marketTicker: string;
  lastUpdate: number;
}

interface UserOrder {
  price: number;
  size: number;
  action: 'buy' | 'sell';
}

interface OrderbookProps {
  marketTicker: string;
  wsUrl?: string;
  onPriceClick?: (price: number, side: 'yes' | 'no') => void;
  userOrders?: UserOrder[];
}

export default function OrderbookLadder({ marketTicker, wsUrl = 'ws://localhost:8000', onPriceClick, userOrders = [] }: OrderbookProps) {
  const [orderbook, setOrderbook] = useState<OrderbookState>({
    yes: [],
    no: [],
    marketTicker: '',
    lastUpdate: 0
  });
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const seqRef = useRef<number>(0);

  const processSnapshot = useCallback((msg: any) => {
    const yesLevels: OrderbookLevel[] = (msg.yes || []).map((level: [number, number]) => ({
      price: level[0],
      priceDollars: level[0].toString(), // price is already in cents
      size: level[1]
    }));

    const noLevels: OrderbookLevel[] = (msg.no || []).map((level: [number, number]) => ({
      price: level[0],
      priceDollars: level[0].toString(), // price is already in cents
      size: level[1]
    }));

    // Sort: Both descending (highest bids at top)
    yesLevels.sort((a, b) => b.price - a.price);
    noLevels.sort((a, b) => b.price - a.price);

    setOrderbook({
      yes: yesLevels,
      no: noLevels,
      marketTicker: msg.market_ticker,
      lastUpdate: Date.now()
    });
  }, []);

  const processDelta = useCallback((msg: any) => {
    setOrderbook(prev => {
      const side = msg.side === 'yes' ? 'yes' : 'no';
      const levels = [...prev[side]];
      
      const existingIndex = levels.findIndex(l => l.price === msg.price);
      
      if (existingIndex >= 0) {
        const newSize = levels[existingIndex].size + msg.delta;
        if (newSize <= 0) {
          // Remove level if size goes to 0 or negative
          levels.splice(existingIndex, 1);
        } else {
          levels[existingIndex] = { ...levels[existingIndex], size: newSize };
        }
      } else if (msg.delta > 0) {
        // Add new level - price is already in cents
        levels.push({
          price: msg.price,
          priceDollars: msg.price.toString(), // Convert to string, already in cents
          size: msg.delta
        });
      }

      // Re-sort: BOTH sides descending (highest bids first)
      levels.sort((a, b) => b.price - a.price);

      return {
        ...prev,
        [side]: levels,
        lastUpdate: Date.now()
      };
    });
  }, []);

  useEffect(() => {
    if (!marketTicker) return;

    // Connect to our backend WebSocket proxy (which handles Kalshi auth)
    const wsEndpoint = `${wsUrl}/ws/orderbook/${marketTicker}`;
    const ws = new WebSocket(wsEndpoint);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('[Orderbook] Connected to WebSocket proxy');
      setConnected(true);
      setError(null);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        
        if (data.type === 'orderbook_snapshot') {
          seqRef.current = data.seq;
          processSnapshot(data.msg);
        } else if (data.type === 'orderbook_delta') {
          // Check sequence number for consistency
          if (data.seq !== seqRef.current + 1) {
            console.warn('[Orderbook] Sequence gap detected, might need to re-subscribe');
          }
          seqRef.current = data.seq;
          processDelta(data.msg);
        }
      } catch (err) {
        console.error('[Orderbook] Error processing message:', err);
      }
    };

    ws.onerror = (err) => {
      console.error('[Orderbook] WebSocket error:', err);
      setError('WebSocket connection error');
      setConnected(false);
    };

    ws.onclose = () => {
      console.log('[Orderbook] WebSocket closed');
      setConnected(false);
    };

    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }, [marketTicker, wsUrl, processSnapshot, processDelta]);

  // In Kalshi orderbooks:
  // - Yes side: bids for Yes contracts (sorted highest first)
  // - No side: bids for No contracts (sorted highest first)
  // To BUY Yes, you can either bid on Yes side OR take the complement of a No bid
  // Since Yes + No = 100¢, the best Yes ask = 100 - (best No bid)
  
  const bestYesBid = orderbook.yes[0]; // Highest price someone will pay for Yes
  const bestNoBid = orderbook.no[0];   // Highest price someone will pay for No
  
  // Best Yes ask is the complement of best No bid
  const bestYesAsk = bestNoBid ? 100 - bestNoBid.price : null;
  
  // Spread between Yes bid and Yes ask
  const spread = bestYesBid && bestYesAsk ? bestYesAsk - bestYesBid.price : 0;
  const midPrice = bestYesBid && bestYesAsk ? (bestYesBid.price + bestYesAsk) / 2 : 0;
  
  // Debug: log values
  if (bestYesBid && bestNoBid) {
    console.log(`Best Yes Bid: ${bestYesBid.price}¢, Best No Bid: ${bestNoBid.price}¢, Yes Ask: ${bestYesAsk}¢, Spread: ${spread}¢, Mid: ${midPrice}¢`);
  }

  // Create lookup maps for YES and NO sizes by price
  const yesSizeByPrice = new Map<number, number>();
  const noSizeByPrice = new Map<number, number>();
  
  orderbook.yes.forEach(level => {
    yesSizeByPrice.set(level.price, level.size);
  });
  
  orderbook.no.forEach(level => {
    noSizeByPrice.set(level.price, level.size);
  });

  // Create lookup maps for user's buy and sell orders by price
  const userBuysByPrice = new Map<number, number>();
  const userSellsByPrice = new Map<number, number>();
  
  userOrders.forEach(order => {
    if (order.action === 'buy') {
      userBuysByPrice.set(order.price, (userBuysByPrice.get(order.price) || 0) + order.size);
    } else {
      userSellsByPrice.set(order.price, (userSellsByPrice.get(order.price) || 0) + order.size);
    }
  });
  
  // Generate all price levels from 99 down to 1
  const allPrices = Array.from({ length: 99 }, (_, i) => 99 - i);

  return (
    <div className="flex flex-col h-full bg-gray-900 text-white">
      {/* Header */}
      <div className="p-4 border-b border-gray-700">
        <div className="flex justify-between items-center mb-2">
          <h2 className="text-xl font-bold">{marketTicker}</h2>
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
            <span className="text-xs">{connected ? 'Live' : 'Disconnected'}</span>
          </div>
        </div>
        
        {/* Market Info */}
        <div className="grid grid-cols-3 gap-4 text-sm">
          <div>
            <div className="text-gray-400 text-xs">Best Bid (Yes)</div>
            <div className="text-green-400 font-mono text-lg">
              {bestYesBid ? `${bestYesBid.price}¢` : '—'}
            </div>
          </div>
          <div className="text-center">
            <div className="text-gray-400 text-xs">Spread / Mid</div>
            <div className="font-mono text-lg">
              {bestYesBid && bestYesAsk ? `${spread}¢ / ${midPrice.toFixed(1)}¢` : '—'}
            </div>
          </div>
          <div className="text-right">
            <div className="text-gray-400 text-xs">Best Ask (Yes)</div>
            <div className="text-red-400 font-mono text-lg">
              {bestYesAsk !== null ? `${bestYesAsk}¢` : '—'}
            </div>
          </div>
        </div>
      </div>

      {error && (
        <div className="p-4 bg-red-900/20 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Top-of-Book Panel */}
      <div className="p-4 border-b border-gray-700 bg-gray-800/50">
        <div className="grid grid-cols-2 gap-4 mb-4">
          {/* YES Side - Top 3 Levels */}
          <div className="bg-gray-900/50 rounded-lg p-3">
            <div className="text-xs text-gray-400 mb-2 font-semibold">YES SIDE (Top 3)</div>
            <div className="space-y-1.5">
              {orderbook.yes.slice(0, 3).map((level, idx) => (
                <div key={idx} className="flex justify-between items-center">
                  <span className={`font-mono ${idx === 0 ? 'text-green-400 font-bold text-base' : 'text-green-500 text-sm'}`}>
                    {level.price}¢
                  </span>
                  <span className={`font-mono ${idx === 0 ? 'text-gray-300 font-semibold' : 'text-gray-400 text-sm'}`}>
                    {level.size.toLocaleString()}
                  </span>
                  {idx === 0 && <span className="text-xs text-green-400 ml-2">BEST BID</span>}
                </div>
              ))}
              {orderbook.yes.length === 0 && (
                <div className="text-gray-500 text-sm">No bids</div>
              )}
            </div>
          </div>

          {/* NO Side - Top 3 Levels */}
          <div className="bg-gray-900/50 rounded-lg p-3">
            <div className="text-xs text-gray-400 mb-2 font-semibold">NO SIDE (Top 3)</div>
            <div className="space-y-1.5">
              {orderbook.no.slice(0, 3).map((level, idx) => (
                <div key={idx} className="flex justify-between items-center">
                  <span className={`font-mono ${idx === 0 ? 'text-red-400 font-bold text-base' : 'text-red-500 text-sm'}`}>
                    {level.price}¢
                  </span>
                  <span className={`font-mono ${idx === 0 ? 'text-gray-300 font-semibold' : 'text-gray-400 text-sm'}`}>
                    {level.size.toLocaleString()}
                  </span>
                  {idx === 0 && <span className="text-xs text-red-400 ml-2">BEST BID</span>}
                </div>
              ))}
              {orderbook.no.length === 0 && (
                <div className="text-gray-500 text-sm">No bids</div>
              )}
            </div>
          </div>
        </div>

        {/* Spread & Depth Info */}
        <div className="grid grid-cols-3 gap-4 pt-3 border-t border-gray-700">
          <div className="text-center">
            <div className="text-xs text-gray-400 mb-1">Spread</div>
            <div className="font-mono font-bold text-yellow-400">
              {bestYesBid && bestYesAsk ? (
                <>
                  {spread}¢ 
                  <span className="text-xs text-gray-400 ml-1">
                    ({((spread / bestYesBid.price) * 100).toFixed(2)}%)
                  </span>
                </>
              ) : '—'}
            </div>
          </div>
          <div className="text-center">
            <div className="text-xs text-gray-400 mb-1">Top YES Depth</div>
            <div className="font-mono font-semibold text-green-400">
              {bestYesBid ? bestYesBid.size.toLocaleString() : '—'}
            </div>
          </div>
          <div className="text-center">
            <div className="text-xs text-gray-400 mb-1">Top NO Depth</div>
            <div className="font-mono font-semibold text-red-400">
              {bestNoBid ? bestNoBid.size.toLocaleString() : '—'}
            </div>
          </div>
        </div>
      </div>

      {/* Orderbook Ladder */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-sm font-mono">
          <thead className="sticky top-0 bg-gray-800 border-b border-gray-700">
            <tr>
              <th className="text-left p-1.5 text-green-400 text-xs">Yes</th>
              <th className="text-center p-1.5 text-cyan-400 text-xs">My Buy</th>
              <th className="text-center p-1.5 text-xs">Price</th>
              <th className="text-center p-1.5 text-orange-400 text-xs">My Sell</th>
              <th className="text-right p-1.5 text-red-400 text-xs">No</th>
            </tr>
          </thead>
          <tbody>
            {!connected ? (
              <tr>
                <td colSpan={5} className="text-center p-8 text-gray-500">
                  Not connected
                </td>
              </tr>
            ) : (
              allPrices.map((price) => {
                const yesSize = yesSizeByPrice.get(price) || 0;
                const noSize = noSizeByPrice.get(price) || 0;
                const userBuys = userBuysByPrice.get(price) || 0;
                const userSells = userSellsByPrice.get(price) || 0;
                const isBestYesBid = bestYesBid && price === bestYesBid.price;
                const isBestNoBid = bestNoBid && price === bestNoBid.price;
                const hasYes = yesSize > 0;
                const hasNo = noSize > 0;
                const hasUserBuys = userBuys > 0;
                const hasUserSells = userSells > 0;

                return (
                  <tr
                    key={price}
                    className={`border-b border-gray-800 hover:bg-gray-800/50 ${
                      isBestYesBid || isBestNoBid ? 'bg-blue-900/20' : ''
                    } ${hasUserBuys || hasUserSells ? 'bg-purple-900/10' : ''}`}
                  >
                    {/* Yes Size - clickable */}
                    <td 
                      className={`p-1 text-left ${
                        hasYes ? 'text-green-400 font-semibold' : 'text-gray-700'
                      } ${onPriceClick ? 'hover:bg-green-900/30 cursor-pointer' : ''}`}
                      onClick={() => onPriceClick?.(price, 'yes')}
                    >
                      {yesSize.toLocaleString()}
                      {isBestYesBid && <span className="ml-1 text-[10px] text-blue-400">▼</span>}
                    </td>

                    {/* User Buy Orders */}
                    <td className={`p-1 text-center ${
                      hasUserBuys ? 'text-cyan-400 font-bold bg-cyan-900/20' : 'text-gray-700'
                    }`}>
                      {hasUserBuys ? userBuys.toLocaleString() : ''}
                    </td>
                    
                    {/* Price */}
                    <td className={`p-1 text-center font-bold text-xs ${
                      isBestYesBid ? 'text-green-400' : 
                      isBestNoBid ? 'text-red-400' : 
                      hasYes || hasNo ? 'text-gray-300' : 'text-gray-600'
                    }`}>
                      {price}
                    </td>

                    {/* User Sell Orders */}
                    <td className={`p-1 text-center ${
                      hasUserSells ? 'text-orange-400 font-bold bg-orange-900/20' : 'text-gray-700'
                    }`}>
                      {hasUserSells ? userSells.toLocaleString() : ''}
                    </td>
                    
                    {/* No Size - clickable */}
                    <td 
                      className={`p-1 text-right ${
                        hasNo ? 'text-red-400 font-semibold' : 'text-gray-700'
                      } ${onPriceClick ? 'hover:bg-red-900/30 cursor-pointer' : ''}`}
                      onClick={() => onPriceClick?.(price, 'no')}
                    >
                      {isBestNoBid && <span className="mr-1 text-[10px] text-blue-400">▲</span>}
                      {noSize.toLocaleString()}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Footer Stats */}
      <div className="p-2 border-t border-gray-700 flex justify-between text-xs text-gray-400">
        <div>Yes Levels: {orderbook.yes.length}</div>
        <div>No Levels: {orderbook.no.length}</div>
        <div>Last Update: {orderbook.lastUpdate ? new Date(orderbook.lastUpdate).toLocaleTimeString() : '—'}</div>
      </div>
    </div>
  );
}

