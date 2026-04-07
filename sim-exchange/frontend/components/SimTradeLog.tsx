"use client";

import { useEffect, useRef, useState } from "react";
import type { OrderLog } from "@/lib/api";
import { getTrades } from "@/lib/api";

interface Props {
  wsUrl: string;
}

function formatClock(period: string | number | undefined, clock: string | number | undefined): string {
  if (!period && !clock) return "";
  const p = typeof period === "number" ? (period <= 4 ? `Q${period}` : `OT${period - 4}`) : period || "";
  if (typeof clock === "number") {
    const m = Math.floor(clock / 60);
    const s = clock % 60;
    return `${p} ${m}:${String(s).padStart(2, "0")}`;
  }
  return `${p} ${clock || ""}`;
}

export default function SimTradeLog({ wsUrl }: Props) {
  const [orders, setOrders] = useState<OrderLog[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  // Initial load
  useEffect(() => {
    loadTrades();
  }, []);

  // Real-time updates via GUI WebSocket
  useEffect(() => {
    const ws = new WebSocket(`${wsUrl}/sim/ws/events`);
    wsRef.current = ws;

    ws.onmessage = () => {
      loadTrades();
    };

    return () => ws.close();
  }, [wsUrl]);

  async function loadTrades() {
    try {
      const data = await getTrades(100);
      setOrders(data.orders.reverse()); // newest first
    } catch {}
  }

  function formatTime(ts: number) {
    return new Date(ts * 1000).toLocaleTimeString();
  }

  const filtered = orders.filter((o) => !o.is_mm);

  return (
    <div className="border border-gray-700 rounded-lg p-3">
      <h3 className="text-sm font-semibold mb-2">Trade Log</h3>
      <div className="max-h-[400px] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="text-gray-500 sticky top-0 bg-gray-900">
            <tr>
              <th className="text-left py-1">Time</th>
              <th className="text-left">Action</th>
              <th className="text-right">Price</th>
              <th className="text-right">Size</th>
              <th className="text-right">Status</th>
              <th className="text-right">Bid/Ask</th>
              <th className="text-right">Score</th>
              <th className="text-right">Game</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((order, i) => {
              const m = order.market;
              return (
                <tr
                  key={`${order.order_id}-${i}`}
                  className="border-t border-gray-800 hover:bg-gray-800/50"
                >
                  <td className="py-1 text-gray-400 font-mono text-[10px]">
                    {formatTime(order.created_at)}
                  </td>
                  <td
                    className={`font-semibold ${
                      order.action === "buy" ? "text-green-400" : "text-red-400"
                    }`}
                  >
                    {order.action.toUpperCase()}
                  </td>
                  <td className="text-right font-mono">{order.price}c</td>
                  <td className="text-right font-mono">{order.count}</td>
                  <td className="text-right">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] ${
                        order.status === "filled"
                          ? "bg-green-900/50 text-green-400"
                          : order.status === "resting"
                            ? "bg-yellow-900/50 text-yellow-400"
                            : "bg-red-900/50 text-red-400"
                      }`}
                    >
                      {order.status}
                    </span>
                  </td>
                  <td className="text-right font-mono text-[10px] text-gray-400">
                    {m ? `${m.best_bid ?? "—"}/${m.best_ask ?? "—"}` : "—"}
                  </td>
                  <td className="text-right font-mono text-[10px] text-gray-400">
                    {m ? `${m.away_score}-${m.home_score}` : "—"}
                  </td>
                  <td className="text-right font-mono text-[10px] text-gray-400">
                    {m ? formatClock(m.period, m.clock) : "—"}
                  </td>
                </tr>
              );
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={8} className="text-center text-gray-500 py-4">
                  No trades yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {filtered.length > 0 && (
        <div className="text-[10px] text-gray-500 mt-2 pt-2 border-t border-gray-800">
          {filtered.length} orders | {filtered.filter(o => o.status === "filled").length} filled | {filtered.filter(o => o.status === "resting").length} resting
        </div>
      )}
    </div>
  );
}
