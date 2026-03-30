"use client";

import { useEffect, useRef, useState } from "react";
import type { OrderLog } from "@/lib/api";
import { getTrades } from "@/lib/api";

interface Props {
  wsUrl: string;
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
      // On any event, refresh the trade log
      loadTrades();
    };

    return () => ws.close();
  }, [wsUrl]);

  async function loadTrades() {
    try {
      const data = await getTrades(50);
      setOrders(data.orders.reverse()); // newest first
    } catch {}
  }

  function formatTime(ts: number) {
    return new Date(ts * 1000).toLocaleTimeString();
  }

  return (
    <div className="border border-gray-700 rounded-lg p-3">
      <h3 className="text-sm font-semibold mb-2">Trade Log</h3>
      <div className="max-h-[300px] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="text-gray-500 sticky top-0 bg-gray-900">
            <tr>
              <th className="text-left py-1">Time</th>
              <th className="text-left">Ticker</th>
              <th className="text-left">Action</th>
              <th className="text-right">Price</th>
              <th className="text-right">Size</th>
              <th className="text-right">Status</th>
            </tr>
          </thead>
          <tbody>
            {orders
              .filter((o) => !o.is_mm)
              .map((order) => (
                <tr
                  key={order.order_id}
                  className="border-t border-gray-800 hover:bg-gray-800/50"
                >
                  <td className="py-1 text-gray-400 font-mono">
                    {formatTime(order.created_at)}
                  </td>
                  <td className="font-mono truncate max-w-[100px]">
                    {order.ticker.split("-").pop()}
                  </td>
                  <td
                    className={
                      order.action === "buy" ? "text-green-400" : "text-red-400"
                    }
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
                </tr>
              ))}
            {orders.filter((o) => !o.is_mm).length === 0 && (
              <tr>
                <td colSpan={6} className="text-center text-gray-500 py-4">
                  No trades yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
