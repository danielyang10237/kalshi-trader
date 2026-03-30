'use client';

import { useState, useEffect, useRef } from 'react';

interface Trade {
  market_ticker: string;
  yes_price: number;
  no_price: number;
  count: number;
  taker_side: string;
  ts: number;
}

interface MarketInfo {
  liquidity?: number;
  liquidity_dollars?: string;
  volume?: number;
  volume_24h?: number;
}

interface TradesFeedProps {
  marketTicker: string;
  wsUrl?: string;
  apiUrl?: string;
}

export default function TradesFeed({ 
  marketTicker,
  wsUrl = 'ws://localhost:8000',
  apiUrl = 'http://localhost:8000'
}: TradesFeedProps) {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [marketInfo, setMarketInfo] = useState<MarketInfo | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Fetch market info for liquidity
  useEffect(() => {
    const fetchMarketInfo = async () => {
      try {
        const response = await fetch(`${apiUrl}/api/markets/${marketTicker}`);
        if (response.ok) {
          const data = await response.json();
          setMarketInfo({
            liquidity: data.market?.liquidity,
            liquidity_dollars: data.market?.liquidity_dollars,
            volume: data.market?.volume,
            volume_24h: data.market?.volume_24h,
          });
        }
      } catch (err) {
        console.error('[TradesFeed] Failed to fetch market info:', err);
      }
    };
    
    if (marketTicker) {
      fetchMarketInfo();
      // Refresh every 30 seconds
      const interval = setInterval(fetchMarketInfo, 30000);
      return () => clearInterval(interval);
    }
  }, [marketTicker, apiUrl]);

  useEffect(() => {
    const connectWebSocket = () => {
      try {
        // Connect to our backend proxy for trades
        const ws = new WebSocket(`${wsUrl}/ws/trades/${marketTicker}`);
        wsRef.current = ws;

        ws.onopen = () => {
          console.log('[TradesFeed] WebSocket connected');
          setConnected(true);
          setError(null);
        };

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            
            // Handle trade updates
            if (data.type === 'trade' && data.msg) {
              const raw = data.msg;
              // Support both old format (yes_price in cents) and new format (yes_price_dollars as string)
              const trade: Trade = {
                market_ticker: raw.market_ticker,
                yes_price: raw.yes_price ?? Math.round(parseFloat(raw.yes_price_dollars || '0') * 100),
                no_price: raw.no_price ?? Math.round(parseFloat(raw.no_price_dollars || '0') * 100),
                count: raw.count ?? parseFloat(raw.count_fp || '0'),
                taker_side: raw.taker_side,
                ts: raw.ts ?? Date.now() / 1000,
              };
              
              // Only add if it matches our market (backend should filter, but double-check)
              if (trade.market_ticker === marketTicker) {
                setTrades(prev => {
                  const newTrades = [trade, ...prev];
                  // Keep only the 20 most recent
                  return newTrades.slice(0, 20);
                });
              }
            }
          } catch (err) {
            console.error('[TradesFeed] Failed to parse message:', err);
          }
        };

        ws.onerror = () => {
          // Note: WebSocket error events don't contain useful info for security reasons
          console.error('[TradesFeed] WebSocket error (check backend logs for details)');
          // Don't set error here - let onclose handle it
        };

        ws.onclose = (event) => {
          console.log('[TradesFeed] WebSocket closed:', event.code, event.reason || 'No reason');
          setConnected(false);
          
          // Only show error if it wasn't a clean close
          if (event.code !== 1000 && event.code !== 1001) {
            setError(`Connection closed (${event.code})`);
          }
          
          // Attempt to reconnect after 3 seconds
          reconnectTimeoutRef.current = setTimeout(() => {
            console.log('[TradesFeed] Attempting to reconnect...');
            setError(null);
            connectWebSocket();
          }, 3000);
        };
      } catch (err) {
        console.error('[TradesFeed] Failed to connect:', err);
        setError('Failed to connect');
      }
    };

    connectWebSocket();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [marketTicker, wsUrl]);

  const formatTime = (ts: number) => {
    const date = new Date(ts * 1000);
    return date.toLocaleTimeString();
  };

  return (
    <div className="flex flex-col h-full bg-gray-900 text-white rounded-lg overflow-hidden">
      {/* Header */}
      <div className="p-3 border-b border-gray-700">
        <div className="flex justify-between items-center mb-2">
          <div>
            <h3 className="text-sm font-bold">Recent Trades</h3>
          </div>
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
            <span className="text-xs text-gray-400">
              {connected ? 'Live' : 'Disconnected'}
            </span>
          </div>
        </div>
        
        {/* Liquidity Info */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="bg-gray-800 rounded px-2 py-1.5">
            <div className="text-gray-400">Liquidity</div>
            <div className="font-mono font-semibold text-blue-400">
              {marketInfo?.liquidity !== undefined 
                ? `${marketInfo.liquidity.toLocaleString()} contracts`
                : '—'}
            </div>
          </div>
          <div className="bg-gray-800 rounded px-2 py-1.5">
            <div className="text-gray-400">Liquidity ($)</div>
            <div className="font-mono font-semibold text-green-400">
              {marketInfo?.liquidity_dollars 
                ? `$${parseFloat(marketInfo.liquidity_dollars).toLocaleString()}`
                : '—'}
            </div>
          </div>
        </div>
      </div>

      {/* Error display */}
      {error && (
        <div className="p-2 bg-red-900/20 text-red-400 text-sm text-center">
          {error}
        </div>
      )}

      {/* Trades Table Header */}
      <div className="grid grid-cols-5 gap-2 px-4 py-2 bg-gray-800 text-xs font-semibold text-gray-400 border-b border-gray-700">
        <div>Time</div>
        <div className="text-right">Side</div>
        <div className="text-right">Price</div>
        <div className="text-right">Size</div>
        <div className="text-right">Value</div>
      </div>

      {/* Trades List */}
      <div className="flex-1 overflow-y-auto">
        {trades.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-500 text-sm">
            {connected ? 'Waiting for trades...' : 'Connecting...'}
          </div>
        ) : (
          <div className="divide-y divide-gray-800">
            {trades.map((trade, index) => {
              const isBuy = trade.taker_side === 'yes';
              const price = isBuy ? trade.yes_price : trade.no_price;
              const value = (price * trade.count / 100).toFixed(2);
              
              return (
                <div 
                  key={`${trade.ts}-${index}`}
                  className={`grid grid-cols-5 gap-2 px-4 py-2 text-sm hover:bg-gray-800/50 transition-colors ${
                    index === 0 ? 'bg-gray-800/30' : ''
                  }`}
                >
                  <div className="text-gray-400 font-mono text-xs">
                    {formatTime(trade.ts)}
                  </div>
                  <div className={`text-right font-semibold ${
                    isBuy ? 'text-green-400' : 'text-red-400'
                  }`}>
                    {trade.taker_side.toUpperCase()}
                  </div>
                  <div className="text-right font-mono">
                    {price}¢
                  </div>
                  <div className="text-right font-mono">
                    {trade.count}
                  </div>
                  <div className="text-right font-mono text-gray-400">
                    ${value}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer with stats */}
      {trades.length > 0 && (
        <div className="p-3 border-t border-gray-700 bg-gray-800/50">
          <div className="grid grid-cols-3 gap-4 text-xs">
            <div>
              <div className="text-gray-400">Total Volume</div>
              <div className="font-mono text-white">
                {trades.reduce((sum, t) => sum + t.count, 0)} contracts
              </div>
            </div>
            <div>
              <div className="text-gray-400">Buy/Sell</div>
              <div className="font-mono">
                <span className="text-green-400">
                  {trades.filter(t => t.taker_side === 'yes').length}
                </span>
                {' / '}
                <span className="text-red-400">
                  {trades.filter(t => t.taker_side === 'no').length}
                </span>
              </div>
            </div>
            <div>
              <div className="text-gray-400">Last Price</div>
              <div className="font-mono text-white">
                {trades[0]?.yes_price}¢
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

