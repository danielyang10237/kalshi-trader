"use client";

import { useEffect, useState } from "react";
import {
  loadReplay,
  startReplay,
  pauseReplay,
  stopReplay,
  getReplayStatus,
  setReplaySpeed,
  seekReplay,
  setReplayParams,
  type ReplayStatus,
} from "@/lib/api";

export default function SimReplayPanel() {
  const [filepath, setFilepath] = useState("");
  const [status, setStatus] = useState<ReplayStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Local state for param sliders (synced from status)
  const [spread, setSpread] = useState(4);
  const [levels, setLevels] = useState(5);
  const [volMult, setVolMult] = useState(1.0);

  useEffect(() => {
    const interval = setInterval(refreshStatus, 1000);
    return () => clearInterval(interval);
  }, []);

  // Sync local sliders from server status on first load
  useEffect(() => {
    if (status && spread === 4 && levels === 5 && volMult === 1.0) {
      setSpread(status.spread);
      setLevels(status.levels);
      setVolMult(status.volume_mult);
    }
  }, [status?.spread, status?.levels, status?.volume_mult]);

  async function refreshStatus() {
    try {
      const s = await getReplayStatus();
      setStatus(s);
    } catch {}
  }

  async function handleLoad() {
    setLoading(true);
    setError("");
    try {
      const result = await loadReplay(filepath);
      if (!result.success) {
        setError(result.error || "Failed to load");
      }
      await refreshStatus();
    } catch (e: any) {
      setError(e.message);
    }
    setLoading(false);
  }

  async function handlePlay() {
    await startReplay(status?.speed || 1.0);
    await refreshStatus();
  }

  async function handlePause() {
    await pauseReplay();
    await refreshStatus();
  }

  async function handleStop() {
    await stopReplay();
    await refreshStatus();
  }

  async function handleSpeedChange(speed: number) {
    await setReplaySpeed(speed);
    await refreshStatus();
  }

  async function handleParamChange(params: { spread?: number; levels?: number; volume_mult?: number }) {
    await setReplayParams(params);
  }

  return (
    <div className="border border-gray-700 rounded-lg p-4">
      <h3 className="text-sm font-semibold mb-3">Historical Replay</h3>

      {/* File input */}
      <div className="flex gap-2 mb-3">
        <input
          type="text"
          value={filepath}
          onChange={(e) => setFilepath(e.target.value)}
          placeholder="Path to kalshi_live CSV file..."
          className="flex-1 px-2 py-1.5 text-xs bg-gray-800 border border-gray-600 rounded font-mono focus:outline-none focus:border-blue-500"
        />
        <button
          onClick={handleLoad}
          disabled={loading || !filepath}
          className="px-3 py-1.5 text-xs bg-gray-700 hover:bg-gray-600 disabled:bg-gray-800 disabled:text-gray-600 rounded"
        >
          {loading ? "..." : "Load"}
        </button>
      </div>

      {error && (
        <div className="text-xs text-red-400 mb-2">{error}</div>
      )}

      {/* Metadata */}
      {status?.metadata && Object.keys(status.metadata).length > 0 && (
        <div className="text-xs text-gray-400 mb-3 space-y-0.5">
          {status.metadata.home_team && (
            <div>
              {status.metadata.away_team} @ {status.metadata.home_team} ({status.metadata.game_date})
            </div>
          )}
          <div>Events: {status.total_events}</div>
        </div>
      )}

      {/* Controls */}
      <div className="flex items-center gap-2 mb-3">
        {!status?.playing ? (
          <button
            onClick={handlePlay}
            disabled={!status || status.total_events === 0}
            className="px-3 py-1.5 text-xs bg-green-700 hover:bg-green-600 disabled:bg-gray-700 disabled:text-gray-500 rounded font-medium"
          >
            Play
          </button>
        ) : status.paused ? (
          <button
            onClick={handlePlay}
            className="px-3 py-1.5 text-xs bg-green-700 hover:bg-green-600 rounded font-medium"
          >
            Resume
          </button>
        ) : (
          <button
            onClick={handlePause}
            className="px-3 py-1.5 text-xs bg-yellow-700 hover:bg-yellow-600 rounded font-medium"
          >
            Pause
          </button>
        )}
        <button
          onClick={handleStop}
          disabled={!status?.playing}
          className="px-3 py-1.5 text-xs bg-red-700 hover:bg-red-600 disabled:bg-gray-700 disabled:text-gray-500 rounded font-medium"
        >
          Stop
        </button>

        {/* Speed selector */}
        <div className="flex gap-1 ml-auto">
          {[0.5, 1, 2, 5].map((s) => (
            <button
              key={s}
              onClick={() => handleSpeedChange(s)}
              className={`px-2 py-1 text-[10px] rounded ${
                status?.speed === s
                  ? "bg-blue-600 text-white"
                  : "bg-gray-700 text-gray-400 hover:bg-gray-600"
              }`}
            >
              {s}x
            </button>
          ))}
        </div>
      </div>

      {/* Book params */}
      {status && status.total_events > 0 && (
        <div className="space-y-2 mb-3 pt-3 border-t border-gray-800">
          <div className="text-[10px] text-gray-500 uppercase font-semibold">Replay Book Params</div>
          <div>
            <div className="flex justify-between text-xs text-gray-400 mb-0.5">
              <span>Spread</span>
              <span className="font-mono">{spread}c</span>
            </div>
            <input
              type="range"
              min={1}
              max={16}
              value={spread}
              onChange={(e) => {
                const v = Number(e.target.value);
                setSpread(v);
                handleParamChange({ spread: v });
              }}
              className="w-full h-1 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
            />
          </div>
          <div>
            <div className="flex justify-between text-xs text-gray-400 mb-0.5">
              <span>Levels</span>
              <span className="font-mono">{levels}</span>
            </div>
            <input
              type="range"
              min={1}
              max={15}
              value={levels}
              onChange={(e) => {
                const v = Number(e.target.value);
                setLevels(v);
                handleParamChange({ levels: v });
              }}
              className="w-full h-1 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
            />
          </div>
          <div>
            <div className="flex justify-between text-xs text-gray-400 mb-1">
              <span>Volume</span>
              <span className="font-mono">{volMult}x</span>
            </div>
            <div className="flex gap-1">
              {[1, 10, 25, 100, 500, 1000, 5000].map((v) => (
                <button
                  key={v}
                  onClick={() => {
                    setVolMult(v);
                    handleParamChange({ volume_mult: v });
                  }}
                  className={`flex-1 px-1 py-1 text-[10px] rounded ${
                    volMult === v
                      ? "bg-blue-600 text-white"
                      : "bg-gray-700 text-gray-400 hover:bg-gray-600"
                  }`}
                >
                  {v >= 1000 ? `${v / 1000}k` : v}x
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Clickable progress bar */}
      {status && status.total_events > 0 && (
        <div>
          <div
            className="w-full bg-gray-800 rounded-full h-3 cursor-pointer relative group"
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const pct = (e.clientX - rect.left) / rect.width;
              const targetIndex = Math.round(pct * status.total_events);
              seekReplay(targetIndex).then(() => refreshStatus());
            }}
          >
            <div
              className="bg-blue-500 h-3 rounded-full transition-all duration-300 pointer-events-none"
              style={{ width: `${status.progress_pct}%` }}
            />
            <div className="absolute inset-0 rounded-full group-hover:bg-white/5 transition-colors" />
          </div>
          <div className="flex justify-between text-[10px] text-gray-500 mt-1">
            <span>
              {status.events_played} / {status.total_events}
            </span>
            <span>{status.progress_pct.toFixed(1)}%</span>
          </div>
        </div>
      )}
    </div>
  );
}
