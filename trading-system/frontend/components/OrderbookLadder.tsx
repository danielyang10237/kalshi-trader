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

interface BestPrice {
  yesBid: number | null;
  yesAsk: number | null;
}

interface OrderbookProps {
  marketTicker: string;
  wsUrl?: string;
  onPriceClick?: (price: number, side: 'yes' | 'no') => void;
  userOrders?: UserOrder[];
  onBestPriceChange?: (bp: BestPrice) => void;
}

export default function OrderbookLadder({ marketTicker, wsUrl = 'ws://localhost:8000', onPriceClick, userOrders = [], onBestPriceChange }: OrderbookProps) {
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
    // Support both old format (yes/no with [int, int]) and new format (yes_dollars_fp/no_dollars_fp with [string, string])
    const rawYes = msg.yes_dollars_fp || msg.yes || [];
    const rawNo = msg.no_dollars_fp || msg.no || [];

    const parseLevels = (levels: any[]): OrderbookLevel[] =>
      levels.map((level: any) => {
        const price = typeof level[0] === 'string' ? Math.round(parseFloat(level[0]) * 100) : level[0];
        const size = typeof level[1] === 'string' ? parseFloat(level[1]) : level[1];
        return { price, priceDollars: (price / 100).toFixed(2), size };
      });

    const yesLevels = parseLevels(rawYes);
    const noLevels = parseLevels(rawNo);

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

      // Support both old format (price/delta as int) and new format (price_dollars/delta_fp as string)
      const price = msg.price_dollars ? Math.round(parseFloat(msg.price_dollars) * 100) : msg.price;
      const delta = msg.delta_fp ? parseFloat(msg.delta_fp) : msg.delta;

      const existingIndex = levels.findIndex(l => l.price === price);

      if (existingIndex >= 0) {
        const newSize = levels[existingIndex].size + delta;
        if (newSize <= 0) {
          levels.splice(existingIndex, 1);
        } else {
          levels[existingIndex] = { ...levels[existingIndex], size: newSize };
        }
      } else if (delta > 0) {
        levels.push({
          price,
          priceDollars: (price / 100).toFixed(2),
          size: delta
        });
      }

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

    const wsEndpoint = `${wsUrl}/ws/orderbook/${marketTicker}`;
    const ws = new WebSocket(wsEndpoint);
    wsRef.current = ws;

    ws.onopen = () => {
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
          if (data.seq !== seqRef.current + 1) {
            console.warn('[Orderbook] Sequence gap detected');
          }
          seqRef.current = data.seq;
          processDelta(data.msg);
        }
      } catch (err) {
        console.error('[Orderbook] Error processing message:', err);
      }
    };

    ws.onerror = () => {
      setError('WebSocket connection error');
      setConnected(false);
    };

    ws.onclose = () => {
      setConnected(false);
    };

    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }, [marketTicker, wsUrl, processSnapshot, processDelta]);

  const bestYesBid = orderbook.yes[0];
  const bestNoBid = orderbook.no[0];
  const bestYesAsk = bestNoBid ? 100 - bestNoBid.price : null;
  const spread = bestYesBid && bestYesAsk ? bestYesAsk - bestYesBid.price : 0;

  // Report best prices up to parent
  useEffect(() => {
    if (onBestPriceChange) {
      onBestPriceChange({
        yesBid: bestYesBid?.price ?? null,
        yesAsk: bestYesAsk,
      });
    }
  }, [bestYesBid?.price, bestYesAsk]);

  // Create lookup maps
  const yesSizeByPrice = new Map<number, number>();
  const noSizeByPrice = new Map<number, number>();

  orderbook.yes.forEach(level => {
    yesSizeByPrice.set(level.price, level.size);
  });

  orderbook.no.forEach(level => {
    noSizeByPrice.set(level.price, level.size);
  });

  const userBuysByPrice = new Map<number, number>();
  const userSellsByPrice = new Map<number, number>();

  userOrders.forEach(order => {
    if (order.action === 'buy') {
      userBuysByPrice.set(order.price, (userBuysByPrice.get(order.price) || 0) + order.size);
    } else {
      userSellsByPrice.set(order.price, (userSellsByPrice.get(order.price) || 0) + order.size);
    }
  });

  const allPrices = Array.from({ length: 99 }, (_, i) => 99 - i);

  // Extract short label from ticker (e.g., "ATL" from "KXNBAGAME-26FEB19ATLPHI-ATL")
  const shortLabel = marketTicker.split('-').pop() || marketTicker;

  return (
    <div className="flex flex-col h-full bg-gray-900 text-white">
      {/* Compact Header */}
      <div className="px-2 py-1.5 border-b border-gray-700">
        <div className="flex justify-between items-center">
          <span className="text-xs font-bold truncate">{shortLabel}</span>
          <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
        </div>
        <div className="flex justify-between items-center text-[10px] font-mono mt-0.5">
          <span className="text-green-400">{bestYesBid ? bestYesBid.price : '—'}</span>
          <span className="text-gray-500">{spread || '—'}</span>
          <span className="text-red-400">{bestYesAsk !== null ? bestYesAsk : '—'}</span>
        </div>
        <div className="flex justify-between items-center text-[8px] text-gray-500">
          <span>bid</span>
          <span>spread</span>
          <span>ask</span>
        </div>
      </div>

      {error && (
        <div className="px-2 py-1 bg-red-900/20 text-red-400 text-[10px]">
          {error}
        </div>
      )}

      {/* Orderbook Ladder */}
      <div className="flex-1 overflow-auto scrollbar-hide" style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}>
        <table className="w-full text-xs font-mono">
          <thead className="sticky top-0 bg-gray-800 border-b border-gray-700">
            <tr>
              <th className="text-center p-0.5 text-[10px]">Price</th>
              <th className="text-center p-0.5 text-[10px]">Qty</th>
              <th className="text-center p-0.5 text-purple-400 text-[10px]">Mine</th>
            </tr>
          </thead>
          <tbody>
            {!connected ? (
              <tr>
                <td colSpan={3} className="text-center p-4 text-gray-500 text-[10px]">
                  Not connected
                </td>
              </tr>
            ) : (
              allPrices.map((price) => {
                const buySize = yesSizeByPrice.get(price) || 0;
                const sellSize = noSizeByPrice.get(100 - price) || 0;
                const userBuys = userBuysByPrice.get(price) || 0;
                const userSells = userSellsByPrice.get(price) || 0;
                const isBestBid = bestYesBid && price === bestYesBid.price;
                const isBestAsk = bestNoBid && price === 100 - bestNoBid.price;
                const hasBuy = buySize > 0;
                const hasSell = sellSize > 0;
                const hasUserBuys = userBuys > 0;
                const hasUserSells = userSells > 0;
                const isSellZone = bestYesAsk !== null && price >= bestYesAsk;
                const clickSide = isSellZone ? 'no' : 'yes';

                return (
                  <tr
                    key={price}
                    className={`border-b border-gray-800 ${
                      onPriceClick ? 'hover:bg-gray-800/50 cursor-pointer' : ''
                    } ${isBestBid || isBestAsk ? 'bg-blue-900/20' : ''
                    } ${hasUserBuys || hasUserSells ? 'bg-purple-900/10' : ''}`}
                    onClick={() => onPriceClick?.(price, clickSide)}
                  >
                    <td className={`p-0.5 text-center font-bold text-[10px] ${
                      isBestBid ? 'text-green-400' :
                      isBestAsk ? 'text-red-400' :
                      hasBuy || hasSell ? 'text-gray-300' : 'text-gray-600'
                    }`}>
                      {price}
                    </td>

                    <td className="p-0.5 text-[10px]">
                      {hasBuy && (
                        <div className="text-green-400 font-semibold text-left">
                          {buySize.toLocaleString()}
                        </div>
                      )}
                      {hasSell && (
                        <div className="text-red-400 font-semibold text-right">
                          {sellSize.toLocaleString()}
                        </div>
                      )}
                    </td>

                    <td className="p-0.5 text-center text-[10px]">
                      {hasUserBuys && (
                        <span className="text-cyan-400 font-bold">
                          {userBuys.toLocaleString()}
                        </span>
                      )}
                      {hasUserBuys && hasUserSells && (
                        <span className="text-gray-600 mx-0.5">/</span>
                      )}
                      {hasUserSells && (
                        <span className="text-orange-400 font-bold">
                          {userSells.toLocaleString()}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
