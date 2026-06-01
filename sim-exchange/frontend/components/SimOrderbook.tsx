"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  label: string;
  ticker: string;
  wsUrl: string;
  tint?: string; // "blue" | "amber"
}

interface Level {
  price: number;
  size: number;
}

export default function SimOrderbook({ label, ticker, wsUrl, tint = "blue" }: Props) {
  const [asks, setAsks] = useState<Level[]>([]);
  const [bids, setBids] = useState<Level[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const bookRef = useRef<{ yes: Map<number, number>; no: Map<number, number> }>({
    yes: new Map(),
    no: new Map(),
  });

  useEffect(() => {
    if (!ticker) return;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      ws.send(
        JSON.stringify({
          id: 1,
          cmd: "subscribe",
          params: { channels: ["orderbook_delta"], market_tickers: [ticker] },
        }),
      );
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "orderbook_snapshot") {
          const msg = data.msg;
          const yesMap = new Map<number, number>();
          const noMap = new Map<number, number>();
          for (const [price, size] of msg.yes || []) {
            yesMap.set(price, size);
          }
          for (const [price, size] of msg.no || []) {
            noMap.set(price, size);
          }
          bookRef.current = { yes: yesMap, no: noMap };
          updateDisplay();
        } else if (data.type === "orderbook_delta") {
          const msg = data.msg;
          const map =
            msg.side === "yes" ? bookRef.current.yes : bookRef.current.no;
          const current = map.get(msg.price) || 0;
          const newSize = current + msg.delta;
          if (newSize <= 0) {
            map.delete(msg.price);
          } else {
            map.set(msg.price, newSize);
          }
          updateDisplay();
        }
      } catch {}
    };

    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    return () => {
      ws.close();
    };
  }, [ticker, wsUrl]);

  function updateDisplay() {
    // "yes" = bids (buy YES) at YES prices
    const bidLevels: Level[] = [];
    bookRef.current.yes.forEach((size, price) => {
      bidLevels.push({ price, size });
    });
    bidLevels.sort((a, b) => b.price - a.price);

    // "no" = asks (sell YES) at NO prices, convert to YES prices
    const askLevels: Level[] = [];
    bookRef.current.no.forEach((size, noPrice) => {
      askLevels.push({ price: 100 - noPrice, size });
    });
    askLevels.sort((a, b) => a.price - b.price);

    setAsks(askLevels.slice(0, 10));
    setBids(bidLevels.slice(0, 10));
  }

  const maxSize = Math.max(
    ...asks.map((l) => l.size),
    ...bids.map((l) => l.size),
    1,
  );

  const tintColors =
    tint === "amber"
      ? { ask: "bg-amber-900/30", bid: "bg-amber-700/30", border: "border-amber-700" }
      : { ask: "bg-blue-900/30", bid: "bg-blue-700/30", border: "border-blue-700" };

  return (
    <div className={`border ${tintColors.border} rounded-lg p-3`}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold">{label}</h3>
        <div className="flex items-center gap-1.5">
          <div
            className={`w-2 h-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
          />
          <span className="text-xs text-gray-400 font-mono truncate max-w-[140px]">
            {ticker || "no ticker"}
          </span>
        </div>
      </div>

      {/* Header */}
      <div className="grid grid-cols-3 text-xs text-gray-500 mb-1 px-1">
        <span>Size</span>
        <span className="text-center">Price</span>
        <span className="text-right">Size</span>
      </div>

      {/* Asks (reversed so lowest is at bottom near spread) */}
      <div className="space-y-px">
        {[...asks].reverse().map((level) => (
          <div
            key={`ask-${level.price}`}
            className="grid grid-cols-3 text-xs py-0.5 px-1 relative"
          >
            <div
              className={`absolute inset-0 ${tintColors.ask}`}
              style={{
                width: `${(level.size / maxSize) * 100}%`,
                right: 0,
                left: "auto",
              }}
            />
            <span className="relative text-gray-400">{level.size}</span>
            <span className="relative text-center text-red-400 font-mono">
              {level.price}c
            </span>
            <span className="relative text-right" />
          </div>
        ))}
      </div>

      {/* Spread indicator */}
      <div className="text-center text-xs text-gray-500 py-1 border-y border-gray-700 my-1">
        {asks.length > 0 && bids.length > 0
          ? `Spread: ${asks[0].price - bids[0].price}c`
          : "No market"}
      </div>

      {/* Bids */}
      <div className="space-y-px">
        {bids.map((level) => (
          <div
            key={`bid-${level.price}`}
            className="grid grid-cols-3 text-xs py-0.5 px-1 relative"
          >
            <div
              className={`absolute inset-0 ${tintColors.bid}`}
              style={{ width: `${(level.size / maxSize) * 100}%` }}
            />
            <span className="relative" />
            <span className="relative text-center text-green-400 font-mono">
              {level.price}c
            </span>
            <span className="relative text-right text-gray-400">
              {level.size}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
