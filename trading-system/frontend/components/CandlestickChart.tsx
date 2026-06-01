'use client';

import { useState, useEffect, useRef, useCallback } from 'react';

interface CandlestickRaw {
  end_period_ts: number;
  yes_bid: {
    open: number;
    close: number;
    high: number;
    low: number;
  };
  yes_ask: {
    open: number;
    close: number;
    high: number;
    low: number;
  };
  price: {
    open: number;
    close: number;
    high: number;
    low: number;
    mean?: number;
  };
  volume: number;
  open_interest: number;
}

interface Candlestick {
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  ts: number;
}

interface CandlestickChartProps {
  seriesTicker: string;
  eventTicker: string;
  marketTicker: string;
  apiUrl?: string;
  wsUrl?: string;
}

export default function CandlestickChart({
  seriesTicker,
  eventTicker,
  marketTicker,
  apiUrl = 'http://localhost:8000',
  wsUrl = 'ws://localhost:8000',
}: CandlestickChartProps) {
  const [candlesticks, setCandlesticks] = useState<Candlestick[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<number>(0);
  const [periodInterval, setPeriodInterval] = useState<1 | 60>(1);
  const [wsConnected, setWsConnected] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const periodIntervalRef = useRef(periodInterval);

  // Keep ref in sync so the WS callback always uses current interval
  useEffect(() => {
    periodIntervalRef.current = periodInterval;
  }, [periodInterval]);

  // Bucket a trade timestamp to its candle end_period_ts
  const getBucketEnd = useCallback((tradeTs: number, intervalSec: number) => {
    return (Math.floor(tradeTs / intervalSec) + 1) * intervalSec;
  }, []);

  // Process an incoming trade into the candlestick array
  const processTrade = useCallback((tradePrice: number, tradeTs: number, tradeCount: number) => {
    const intervalSec = periodIntervalRef.current * 60;
    const bucketEnd = getBucketEnd(tradeTs, intervalSec);

    setCandlesticks(prev => {
      if (prev.length === 0) {
        return [{
          open: tradePrice,
          close: tradePrice,
          high: tradePrice,
          low: tradePrice,
          volume: tradeCount,
          ts: bucketEnd,
        }];
      }

      const lastCandle = prev[prev.length - 1];

      if (bucketEnd === lastCandle.ts) {
        // Same bucket — update in place
        const updated = [...prev];
        updated[updated.length - 1] = {
          ...lastCandle,
          close: tradePrice,
          high: Math.max(lastCandle.high, tradePrice),
          low: Math.min(lastCandle.low, tradePrice),
          volume: lastCandle.volume + tradeCount,
        };
        return updated;
      }

      if (bucketEnd > lastCandle.ts) {
        // New bucket(s) — forward-fill gaps, then append
        const newCandles = [...prev];
        const lastPrice = lastCandle.close;

        // Fill any empty intervals between last candle and this trade
        for (let ts = lastCandle.ts + intervalSec; ts < bucketEnd; ts += intervalSec) {
          newCandles.push({
            open: lastPrice,
            close: lastPrice,
            high: lastPrice,
            low: lastPrice,
            volume: 0,
            ts,
          });
        }

        // New candle for this trade
        newCandles.push({
          open: tradePrice,
          close: tradePrice,
          high: tradePrice,
          low: tradePrice,
          volume: tradeCount,
          ts: bucketEnd,
        });

        return newCandles;
      }

      // Trade belongs to an older bucket — ignore (shouldn't happen)
      return prev;
    });

    setLastUpdate(Date.now());
  }, [getBucketEnd]);

  // One-time REST fetch for historical candles
  const fetchCandlesticks = useCallback(async () => {
    try {
      setLoading(true);
      const endTs = Math.floor(Date.now() / 1000);
      const hoursBack = periodInterval === 1 ? 1 : 24;
      const startTs = endTs - (60 * 60 * hoursBack);

      const response = await fetch(
        `${apiUrl}/api/series/${seriesTicker}/events/${eventTicker}/candlesticks?` +
        `start_ts=${startTs}&end_ts=${endTs}&period_interval=${periodInterval}` +
        `&ticker=${encodeURIComponent(marketTicker)}`
      );

      if (!response.ok) {
        throw new Error(`Failed to fetch candlesticks: ${response.statusText}`);
      }

      const data = await response.json();

      const marketIndex = data.market_tickers?.indexOf(marketTicker);
      if (marketIndex === -1 || !data.market_candlesticks?.[marketIndex]) {
        throw new Error('Market not found in event candlesticks');
      }

      const rawCandles: CandlestickRaw[] = data.market_candlesticks[marketIndex] || [];

      let transformedCandles: Candlestick[] = rawCandles.map((raw) => ({
        open: raw.price.open,
        close: raw.price.close,
        high: raw.price.high,
        low: raw.price.low,
        volume: raw.volume,
        ts: raw.end_period_ts,
      }));

      // Forward-fill missing intervals
      if (transformedCandles.length > 0) {
        const filledCandles: Candlestick[] = [];
        const intervalSeconds = periodInterval * 60;

        const expectedTimestamps: number[] = [];
        for (let ts = startTs + intervalSeconds; ts <= endTs; ts += intervalSeconds) {
          expectedTimestamps.push(ts);
        }

        let lastPrice = transformedCandles[0].close;
        let candleIndex = 0;

        for (const expectedTs of expectedTimestamps) {
          const tolerance = intervalSeconds / 2;
          const matchingCandle = transformedCandles.find(
            (c, idx) => idx >= candleIndex && Math.abs(c.ts - expectedTs) < tolerance
          );

          if (matchingCandle) {
            filledCandles.push(matchingCandle);
            lastPrice = matchingCandle.close;
            candleIndex++;
          } else {
            filledCandles.push({
              open: lastPrice,
              close: lastPrice,
              high: lastPrice,
              low: lastPrice,
              volume: 0,
              ts: expectedTs,
            });
          }
        }

        transformedCandles = filledCandles;
      }

      setCandlesticks(transformedCandles);
      setLastUpdate(Date.now());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch candlesticks');
      console.error('[Candlesticks] Error:', err);
    } finally {
      setLoading(false);
    }
  }, [seriesTicker, eventTicker, marketTicker, periodInterval, apiUrl]);

  // Fetch historical data on mount and when interval changes
  useEffect(() => {
    fetchCandlesticks();
  }, [fetchCandlesticks]);

  // WebSocket connection for live trades
  useEffect(() => {
    if (!marketTicker) return;

    const wsEndpoint = `${wsUrl}/ws/trades/${marketTicker}`;
    const ws = new WebSocket(wsEndpoint);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('[Candlesticks] Trade WebSocket connected');
      setWsConnected(true);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'trade' && data.msg) {
          const msg = data.msg;
          if (msg.market_ticker === marketTicker) {
            processTrade(msg.yes_price, msg.ts, msg.count);
          }
        }
      } catch (err) {
        console.error('[Candlesticks] Error processing trade:', err);
      }
    };

    ws.onerror = () => {
      console.error('[Candlesticks] Trade WebSocket error');
      setWsConnected(false);
    };

    ws.onclose = () => {
      console.log('[Candlesticks] Trade WebSocket closed');
      setWsConnected(false);
    };

    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }, [marketTicker, wsUrl, processTrade]);

  // Draw candlesticks on canvas
  useEffect(() => {
    if (!canvasRef.current || candlesticks.length === 0) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    const padding = { top: 20, right: 60, bottom: 30, left: 10 };
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;

    // Clear canvas
    ctx.fillStyle = '#111827';
    ctx.fillRect(0, 0, width, height);

    // Find min/max prices
    const prices = candlesticks.flatMap(c => [c.low, c.high]);
    const dataMin = Math.min(...prices);
    const dataMax = Math.max(...prices);
    const minPrice = Math.max(0, dataMin - 5);
    const maxPrice = Math.min(100, dataMax + 5);
    const priceRange = maxPrice - minPrice || 1;

    // Scale functions
    const xScale = (i: number) => padding.left + (i / candlesticks.length) * chartWidth;
    const yScale = (price: number) => padding.top + chartHeight - ((price - minPrice) / priceRange) * chartHeight;

    // Draw grid lines
    ctx.strokeStyle = '#374151';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 5; i++) {
      const y = padding.top + (chartHeight / 5) * i;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();

      const price = maxPrice - (priceRange / 5) * i;
      ctx.fillStyle = '#9CA3AF';
      ctx.font = '11px monospace';
      ctx.textAlign = 'left';
      ctx.fillText(`${price.toFixed(1)}¢`, width - padding.right + 5, y + 4);
    }

    // Draw candlesticks
    const candleWidth = Math.max(1, chartWidth / candlesticks.length - 2);
    candlesticks.forEach((candle, i) => {
      const x = xScale(i);
      const openY = yScale(candle.open);
      const closeY = yScale(candle.close);
      const highY = yScale(candle.high);
      const lowY = yScale(candle.low);

      const isFlat = candle.open === candle.close &&
                     candle.high === candle.low &&
                     candle.open === candle.high;

      if (isFlat) {
        ctx.strokeStyle = '#6B7280';
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(x, openY);
        ctx.lineTo(x + candleWidth, openY);
        ctx.stroke();
        ctx.setLineDash([]);
      } else {
        const isGreen = candle.close >= candle.open;
        const color = isGreen ? '#10B981' : '#EF4444';

        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x + candleWidth / 2, highY);
        ctx.lineTo(x + candleWidth / 2, lowY);
        ctx.stroke();

        ctx.fillStyle = color;
        const bodyHeight = Math.abs(closeY - openY) || 1;
        const bodyY = Math.min(openY, closeY);
        ctx.fillRect(x, bodyY, candleWidth, bodyHeight);
      }
    });

    // Draw title
    ctx.fillStyle = '#F3F4F6';
    ctx.font = 'bold 14px sans-serif';
    ctx.textAlign = 'left';
    const title = periodInterval === 1
      ? '1-Minute Candlesticks (Last Hour)'
      : '1-Hour Candlesticks (Last 24 Hours)';
    ctx.fillText(title, padding.left, 15);

  }, [candlesticks]);

  return (
    <div className="flex flex-col h-full bg-gray-900 text-white">
      {/* Header */}
      <div className="p-4 border-b border-gray-700">
        <div className="flex justify-between items-center">
          <div className="flex-1">
            <h3 className="text-lg font-bold">{marketTicker}</h3>
            <div className="flex items-center gap-2 text-xs text-gray-400">
              <span>{periodInterval === 1 ? '1-minute' : '1-hour'} intervals</span>
              <span className="text-gray-600">|</span>
              <div className="flex items-center gap-1">
                <div className={`w-1.5 h-1.5 rounded-full ${wsConnected ? 'bg-green-500' : 'bg-red-500'}`} />
                <span>{wsConnected ? 'Live' : 'Disconnected'}</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <label htmlFor="period-select" className="text-sm text-gray-400">
                Interval:
              </label>
              <select
                id="period-select"
                value={periodInterval}
                onChange={(e) => setPeriodInterval(Number(e.target.value) as 1 | 60)}
                className="px-3 py-1 bg-gray-800 border border-gray-600 rounded text-sm text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              >
                <option value={1}>1 Minute</option>
                <option value={60}>1 Hour</option>
              </select>
            </div>
            <div className="text-right">
              <div className="text-xs text-gray-400">Last Update</div>
              <div className="text-sm font-mono">
                {lastUpdate ? new Date(lastUpdate).toLocaleTimeString() : '—'}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Chart */}
      <div className="p-4" style={{ height: '500px' }}>
        {loading && !lastUpdate && (
          <div className="flex items-center justify-center h-full text-gray-400">
            Loading candlesticks...
          </div>
        )}
        {error && (
          <div className="p-4 bg-red-900/20 text-red-400 text-sm rounded">
            {error}
          </div>
        )}
        {!loading && candlesticks.length === 0 && !error && (
          <div className="flex items-center justify-center h-full text-gray-400">
            No candlestick data available
          </div>
        )}
        {candlesticks.length > 0 && (
          <canvas
            ref={canvasRef}
            className="w-full h-full"
          />
        )}
      </div>

      {/* Stats */}
      {candlesticks.length > 0 && (() => {
        const firstCandle = candlesticks[0];
        const lastCandle = candlesticks[candlesticks.length - 1];
        const allHighs = candlesticks.map(c => c.high).filter(h => typeof h === 'number');
        const allLows = candlesticks.map(c => c.low).filter(l => typeof l === 'number');

        return (
          <div className="p-4 border-t border-gray-700 grid grid-cols-4 gap-4 text-sm">
            <div>
              <div className="text-gray-400 text-xs">Open</div>
              <div className="font-mono text-gray-100">
                {firstCandle?.open != null ? `${firstCandle.open.toFixed(1)}¢` : 'N/A'}
              </div>
            </div>
            <div>
              <div className="text-gray-400 text-xs">Close</div>
              <div className="font-mono text-gray-100">
                {lastCandle?.close != null ? `${lastCandle.close.toFixed(1)}¢` : 'N/A'}
              </div>
            </div>
            <div>
              <div className="text-gray-400 text-xs">High</div>
              <div className="font-mono text-green-400">
                {allHighs.length > 0 ? `${Math.max(...allHighs).toFixed(1)}¢` : 'N/A'}
              </div>
            </div>
            <div>
              <div className="text-gray-400 text-xs">Low</div>
              <div className="font-mono text-red-400">
                {allLows.length > 0 ? `${Math.min(...allLows).toFixed(1)}¢` : 'N/A'}
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
