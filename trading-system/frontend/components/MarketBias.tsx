'use client';

import { useState, useEffect, useRef, useCallback } from 'react';

const WS_URL = (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000').replace(/^http/, 'ws');
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface Trade {
  market_ticker: string;
  yes_price: number;
  no_price: number;
  count: number;
  taker_side: string;
  ts: number;
}

interface BiasProps {
  primaryTicker: string;   // e.g. KXNBAGAME-...-TOR
  secondaryTicker: string; // e.g. KXNBAGAME-...-DAL
}

// Extract team code from ticker: last segment after the final dash
function teamLabel(ticker: string): string {
  const parts = ticker.split('-');
  return parts[parts.length - 1] || ticker;
}

interface WindowStats {
  primaryContracts: number;  // contracts favoring primary team
  secondaryContracts: number;
  primaryValue: number;      // dollar value
  secondaryValue: number;
}

function computeWindow(trades: Trade[], windowMs: number, primaryTicker: string): WindowStats {
  const now = Date.now();
  const cutoff = (now - windowMs) / 1000; // trades use unix seconds

  const stats: WindowStats = {
    primaryContracts: 0, secondaryContracts: 0,
    primaryValue: 0, secondaryValue: 0,
  };

  for (const t of trades) {
    if (t.ts < cutoff) continue;

    const isPrimary = t.market_ticker === primaryTicker;
    const isYesTaker = t.taker_side === 'yes';

    // yes taker on primary market = bullish primary team
    // no taker on primary market = bullish secondary team
    // yes taker on secondary market = bullish secondary team
    // no taker on secondary market = bullish primary team
    const bullishPrimary = (isPrimary && isYesTaker) || (!isPrimary && !isYesTaker);

    const price = isYesTaker ? t.yes_price : t.no_price;
    const value = (price * t.count) / 100;

    if (bullishPrimary) {
      stats.primaryContracts += t.count;
      stats.primaryValue += value;
    } else {
      stats.secondaryContracts += t.count;
      stats.secondaryValue += value;
    }
  }

  return stats;
}

function BiasBar({
  label,
  stats,
  primaryLabel,
  secondaryLabel,
}: {
  label: string;
  stats: WindowStats;
  primaryLabel: string;
  secondaryLabel: string;
}) {
  const total = stats.primaryContracts + stats.secondaryContracts;
  const ratio = total === 0 ? 0.5 : stats.primaryContracts / total;
  const barHeight = 120;

  const totalValue = stats.primaryValue + stats.secondaryValue;

  return (
    <div className="flex flex-col items-center flex-1">
      <div className="text-[10px] text-gray-400 font-medium uppercase tracking-wide mb-2">{label}</div>

      {/* Bias bar */}
      <div
        className="w-full rounded overflow-hidden relative"
        style={{ height: barHeight }}
      >
        {/* Secondary (top) */}
        <div
          className="absolute top-0 left-0 right-0 bg-amber-600/60 flex items-center justify-center transition-all duration-500"
          style={{ height: `${(1 - ratio) * 100}%` }}
        >
          {stats.secondaryContracts > 0 && (
            <span className="text-[10px] font-bold text-amber-200">
              {stats.secondaryContracts}
            </span>
          )}
        </div>
        {/* Primary (bottom) */}
        <div
          className="absolute bottom-0 left-0 right-0 bg-blue-600/60 flex items-center justify-center transition-all duration-500"
          style={{ height: `${ratio * 100}%` }}
        >
          {stats.primaryContracts > 0 && (
            <span className="text-[10px] font-bold text-blue-200">
              {stats.primaryContracts}
            </span>
          )}
        </div>
        {/* Center line */}
        <div className="absolute top-1/2 left-0 right-0 border-t border-gray-500/40" />
      </div>

      {/* Stats below */}
      <div className="mt-2 text-center w-full space-y-0.5">
        <div className="text-[9px] text-gray-500">
          {total} contracts · ${totalValue.toFixed(0)}
        </div>
        {total > 0 && (
          <div className="text-[9px]">
            <span className="text-blue-400">{primaryLabel} {(ratio * 100).toFixed(0)}%</span>
            {' / '}
            <span className="text-amber-400">{secondaryLabel} {((1 - ratio) * 100).toFixed(0)}%</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default function MarketBias({ primaryTicker, secondaryTicker }: BiasProps) {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [connected, setConnected] = useState<[boolean, boolean]>([false, false]);
  const [totalVolume, setTotalVolume] = useState<{ primary: number; secondary: number }>({ primary: 0, secondary: 0 });
  const wsRefs = useRef<(WebSocket | null)[]>([null, null]);
  const reconnectRefs = useRef<(ReturnType<typeof setTimeout> | null)[]>([null, null]);
  const tradesRef = useRef<Trade[]>([]);

  const primaryLabel = teamLabel(primaryTicker);
  const secondaryLabel = teamLabel(secondaryTicker);

  // Fetch total volume from market info
  useEffect(() => {
    async function fetchVolumes() {
      try {
        const [r1, r2] = await Promise.all([
          fetch(`${API_URL}/api/markets/${primaryTicker}`),
          fetch(`${API_URL}/api/markets/${secondaryTicker}`),
        ]);
        const [d1, d2] = await Promise.all([r1.json(), r2.json()]);
        setTotalVolume({
          primary: d1.market?.volume || 0,
          secondary: d2.market?.volume || 0,
        });
      } catch {}
    }
    fetchVolumes();
    const interval = setInterval(fetchVolumes, 30000);
    return () => clearInterval(interval);
  }, [primaryTicker, secondaryTicker]);

  // Connect to both trade WebSockets
  const connectWs = useCallback((ticker: string, idx: number) => {
    function connect() {
      const ws = new WebSocket(`${WS_URL}/ws/trades/${ticker}`);
      wsRefs.current[idx] = ws;

      ws.onopen = () => setConnected(prev => {
        const next: [boolean, boolean] = [...prev];
        next[idx] = true;
        return next;
      });

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'trade' && data.msg) {
            const trade: Trade = data.msg;
            tradesRef.current = [trade, ...tradesRef.current].slice(0, 500);
            setTrades([...tradesRef.current]);
          }
        } catch {}
      };

      ws.onerror = () => ws.close();
      ws.onclose = () => {
        setConnected(prev => {
          const next: [boolean, boolean] = [...prev];
          next[idx] = false;
          return next;
        });
        reconnectRefs.current[idx] = setTimeout(connect, 3000);
      };
    }
    connect();
  }, []);

  useEffect(() => {
    connectWs(primaryTicker, 0);
    connectWs(secondaryTicker, 1);

    return () => {
      wsRefs.current.forEach(ws => ws?.close());
      reconnectRefs.current.forEach(t => t && clearTimeout(t));
    };
  }, [primaryTicker, secondaryTicker, connectWs]);

  // Prune old trades (> 1 hour) every 30s
  useEffect(() => {
    const interval = setInterval(() => {
      const cutoff = (Date.now() - 3600_000) / 1000;
      tradesRef.current = tradesRef.current.filter(t => t.ts >= cutoff);
      setTrades([...tradesRef.current]);
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  const stats1m = computeWindow(trades, 60_000, primaryTicker);
  const stats10m = computeWindow(trades, 600_000, primaryTicker);
  const stats1h = computeWindow(trades, 3600_000, primaryTicker);

  const bothConnected = connected[0] && connected[1];

  return (
    <div className="flex flex-col h-full bg-gray-900 text-white rounded-lg overflow-hidden">
      {/* Header */}
      <div className="p-3 border-b border-gray-700">
        <div className="flex justify-between items-center mb-2">
          <h3 className="text-sm font-bold">Market Bias</h3>
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${bothConnected ? 'bg-green-500' : 'bg-yellow-500'}`} />
            <span className="text-xs text-gray-400">
              {bothConnected ? 'Live' : 'Partial'}
            </span>
          </div>
        </div>

        {/* Total volume */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="bg-gray-800 rounded px-2 py-1.5">
            <div className="text-gray-400">{primaryLabel} Vol</div>
            <div className="font-mono font-semibold text-blue-400">
              {totalVolume.primary.toLocaleString()}
            </div>
          </div>
          <div className="bg-gray-800 rounded px-2 py-1.5">
            <div className="text-gray-400">{secondaryLabel} Vol</div>
            <div className="font-mono font-semibold text-amber-400">
              {totalVolume.secondary.toLocaleString()}
            </div>
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="px-3 pt-2 flex justify-center gap-4 text-[10px]">
        <div className="flex items-center gap-1">
          <div className="w-2 h-2 rounded-sm bg-blue-600/60" />
          <span className="text-blue-400">{primaryLabel}</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-2 h-2 rounded-sm bg-amber-600/60" />
          <span className="text-amber-400">{secondaryLabel}</span>
        </div>
      </div>

      {/* Bias bars */}
      <div className="flex-1 flex gap-3 p-3 items-stretch">
        <BiasBar label="1 min" stats={stats1m} primaryLabel={primaryLabel} secondaryLabel={secondaryLabel} />
        <BiasBar label="10 min" stats={stats10m} primaryLabel={primaryLabel} secondaryLabel={secondaryLabel} />
        <BiasBar label="1 hour" stats={stats1h} primaryLabel={primaryLabel} secondaryLabel={secondaryLabel} />
      </div>
    </div>
  );
}
