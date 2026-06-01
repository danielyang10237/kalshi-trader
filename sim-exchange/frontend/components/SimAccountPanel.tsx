"use client";

import { useEffect, useState } from "react";
import { getAccount, resetAccount, type AccountState } from "@/lib/api";

interface Props {
  wsUrl: string;
  onReset?: () => void;
}

export default function SimAccountPanel({ wsUrl, onReset }: Props) {
  const [account, setAccountState] = useState<AccountState | null>(null);

  useEffect(() => {
    loadAccount();
    const interval = setInterval(loadAccount, 3000);

    // Also listen for events
    const ws = new WebSocket(`${wsUrl}/sim/ws/events`);
    ws.onmessage = () => loadAccount();
    return () => {
      clearInterval(interval);
      ws.close();
    };
  }, [wsUrl]);

  async function loadAccount() {
    try {
      const data = await getAccount();
      setAccountState(data);
    } catch {}
  }

  async function handleReset() {
    await resetAccount();
    await loadAccount();
    onReset?.();
  }

  if (!account) return null;

  const pnl = account.balance - account.initial_balance;

  return (
    <div className="border border-gray-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">Account</h3>
        <button
          onClick={handleReset}
          className="px-2 py-1 text-xs bg-red-700 hover:bg-red-600 rounded"
        >
          Reset
        </button>
      </div>

      <div className="space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-gray-400">Balance</span>
          <span className="font-mono">
            ${(account.balance / 100).toFixed(2)}
          </span>
        </div>

        <div className="flex justify-between text-sm">
          <span className="text-gray-400">P&L</span>
          <span
            className={`font-mono ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}
          >
            {pnl >= 0 ? "+" : ""}${(pnl / 100).toFixed(2)}
          </span>
        </div>

        {/* Positions */}
        {Object.entries(account.positions).map(([ticker, net]) => (
          <div key={ticker} className="flex justify-between text-sm">
            <span className="text-gray-400 truncate max-w-[120px]">
              {ticker.split("-").pop()}
            </span>
            <span
              className={`font-mono ${net > 0 ? "text-green-400" : net < 0 ? "text-red-400" : "text-gray-400"}`}
            >
              {net > 0 ? "+" : ""}
              {net} contracts
            </span>
          </div>
        ))}

        {Object.keys(account.positions).length === 0 && (
          <div className="text-xs text-gray-500">No positions</div>
        )}

        <div className="flex justify-between text-xs text-gray-500 pt-1 border-t border-gray-800">
          <span>Fills: {account.fills.length}</span>
          <span>Resting: {account.resting_orders}</span>
        </div>
      </div>
    </div>
  );
}
