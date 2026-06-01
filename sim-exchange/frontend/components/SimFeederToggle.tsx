"use client";

import { useEffect, useState } from "react";
import {
  getFeederStatus,
  toggleFeeder,
  type FeederStatus,
} from "@/lib/api";

export default function SimFeederToggle() {
  const [status, setStatus] = useState<FeederStatus | null>(null);
  const [wsUrl, setWsUrl] = useState("ws://localhost:8000/nba/ws/input/");

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 2000);
    return () => clearInterval(interval);
  }, []);

  async function refresh() {
    try {
      const s = await getFeederStatus();
      setStatus(s);
      if (s.trading_ws_url) setWsUrl(s.trading_ws_url);
    } catch {}
  }

  async function handleToggle() {
    if (!status) return;
    const next = !status.enabled;
    try {
      const s = await toggleFeeder(next, wsUrl);
      setStatus(s);
    } catch (e) {
      console.error("Failed to toggle feeder:", e);
    }
  }

  const enabled = status?.enabled ?? false;
  const connected = status?.connected ?? false;

  return (
    <div className="border border-gray-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">Game State Feeder</h3>
        <div className="flex items-center gap-2">
          {enabled && (
            <div className="flex items-center gap-1">
              <div
                className={`w-2 h-2 rounded-full ${connected ? "bg-green-500" : "bg-yellow-500"}`}
              />
              <span className="text-[10px] text-gray-400">
                {connected ? "Connected" : "Disconnected"}
              </span>
            </div>
          )}
          <button
            onClick={handleToggle}
            className={`px-3 py-1 text-xs rounded font-medium transition-colors ${
              enabled
                ? "bg-green-700 hover:bg-green-600 text-white"
                : "bg-gray-700 hover:bg-gray-600 text-gray-300"
            }`}
          >
            {enabled ? "ON" : "OFF"}
          </button>
        </div>
      </div>

      <p className="text-[10px] text-gray-500 mb-2">
        Sends PBP box score snapshots to the trading system during replay.
      </p>

      {/* WS URL config */}
      <div className="flex gap-2 mb-2">
        <input
          type="text"
          value={wsUrl}
          onChange={(e) => setWsUrl(e.target.value)}
          placeholder="ws://localhost:8000/nba/ws/input/"
          className="flex-1 px-2 py-1 text-[10px] bg-gray-800 border border-gray-600 rounded font-mono focus:outline-none focus:border-blue-500"
        />
      </div>

      {/* Live stats when enabled */}
      {status && enabled && status.total_actions > 0 && (
        <div className="text-xs text-gray-400 space-y-0.5 pt-2 border-t border-gray-800">
          <div className="flex justify-between">
            <span>
              {status.away_team} {status.away_score} - {status.home_score} {status.home_team}
            </span>
            <span className="font-mono">
              Q{status.period} {Math.floor(status.clock / 60)}:{String(status.clock % 60).padStart(2, "0")}
            </span>
          </div>
          <div className="text-[10px] text-gray-500">
            Actions: {status.actions_played} / {status.total_actions}
          </div>
        </div>
      )}
    </div>
  );
}
