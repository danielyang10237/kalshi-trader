"use client";

import { useEffect, useState, useCallback } from "react";
import SimOrderbook from "@/components/SimOrderbook";
import SimControls from "@/components/SimControls";
import SimTradeLog from "@/components/SimTradeLog";
import SimAccountPanel from "@/components/SimAccountPanel";
import SimReplayPanel from "@/components/SimReplayPanel";
import SimGameFeed from "@/components/SimGameFeed";
import SimFeederToggle from "@/components/SimFeederToggle";
import {
  checkHealth,
  getConfig,
  setConfig,
  resetAccount,
  listGames,
  selectGame,
  type HistoricalGame,
} from "@/lib/api";

const SIM_URL = process.env.NEXT_PUBLIC_SIM_URL || "http://localhost:9000";
const WS_URL = SIM_URL.replace("http://", "ws://").replace("https://", "wss://");

export default function SimulatorPage() {
  const [connected, setConnected] = useState(false);
  const [homeTicker, setHomeTicker] = useState("");
  const [awayTicker, setAwayTicker] = useState("");

  // Game selector
  const [games, setGames] = useState<HistoricalGame[]>([]);
  const [selectedGameId, setSelectedGameId] = useState("");
  const [selectedGame, setSelectedGame] = useState<HistoricalGame | null>(null);
  const [selectingGame, setSelectingGame] = useState(false);

  // Check server health + load config + games
  useEffect(() => {
    async function init() {
      const healthy = await checkHealth();
      setConnected(healthy);
      if (healthy) {
        const [cfg, gamesData] = await Promise.all([getConfig(), listGames()]);
        setHomeTicker(cfg.home_ticker);
        setAwayTicker(cfg.away_ticker);
        setGames(gamesData.games);
      }
    }
    init();
    const interval = setInterval(async () => {
      setConnected(await checkHealth());
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  async function handleSelectGame(gameId: string) {
    if (!gameId) return;
    setSelectingGame(true);
    try {
      const result = await selectGame(gameId);
      if (result.success && result.game) {
        setSelectedGameId(gameId);
        setSelectedGame(result.game);
        setHomeTicker(result.game.home_ticker);
        setAwayTicker(result.game.away_ticker);
      }
    } catch (e) {
      console.error("Failed to select game:", e);
    }
    setSelectingGame(false);
  }

  async function handleResetAll() {
    await resetAccount();
  }

  return (
    <div className="min-h-screen p-4 max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold">Sim Exchange</h1>
          <div className="flex items-center gap-1.5">
            <div
              className={`w-2.5 h-2.5 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
            />
            <span className="text-xs text-gray-400">
              {connected ? "Connected" : "Disconnected"}
            </span>
          </div>
        </div>
        <button
          onClick={handleResetAll}
          className="px-3 py-1.5 text-xs bg-red-700 hover:bg-red-600 rounded font-medium"
        >
          Reset All
        </button>
      </div>

      {/* Game Selector */}
      <div className="border border-gray-700 rounded-lg p-4 mb-4">
        <h3 className="text-sm font-semibold mb-3">Select Game</h3>
        <div className="flex gap-3 items-end">
          <div className="flex-1">
            <div className="grid grid-cols-5 gap-2">
              {games.map((game) => (
                <button
                  key={game.game_id}
                  onClick={() => handleSelectGame(game.game_id)}
                  disabled={selectingGame}
                  className={`px-3 py-2 text-xs rounded border transition-colors text-left ${
                    selectedGameId === game.game_id
                      ? "border-blue-500 bg-blue-900/30"
                      : "border-gray-700 bg-gray-800 hover:bg-gray-700"
                  }`}
                >
                  <div className="font-semibold">
                    {game.away_team} @ {game.home_team}
                  </div>
                  <div className="text-gray-400 text-[10px] mt-0.5">
                    {game.date}
                  </div>
                  {game.note && (
                    <div className="text-[10px] mt-0.5 text-yellow-400">
                      {game.note}
                    </div>
                  )}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Active tickers */}
        {(homeTicker || awayTicker) && (
          <div className="flex gap-3 items-center mt-3 pt-3 border-t border-gray-800">
            <div className="flex-1">
              <span className="text-[10px] text-gray-500">Home: </span>
              <span className="text-xs font-mono text-gray-300">
                {homeTicker}
              </span>
            </div>
            <div className="flex-1">
              <span className="text-[10px] text-gray-500">Away: </span>
              <span className="text-xs font-mono text-gray-300">
                {awayTicker}
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-3 gap-4">
        {/* Left: Orderbooks */}
        <div className="space-y-4">
          <SimOrderbook
            label={`Home YES${selectedGame ? ` (${selectedGame.home_team})` : ""}`}
            ticker={homeTicker}
            wsUrl={`${WS_URL}/trade-api/ws/v2`}
            tint="blue"
          />
          <SimOrderbook
            label={`Away YES${selectedGame ? ` (${selectedGame.away_team})` : ""}`}
            ticker={awayTicker}
            wsUrl={`${WS_URL}/trade-api/ws/v2`}
            tint="amber"
          />
        </div>

        {/* Center: Game Feed + Controls + Replay */}
        <div className="space-y-4">
          {selectedGame && (
            <SimGameFeed
              homeTeam={selectedGame.home_team}
              awayTeam={selectedGame.away_team}
            />
          )}
          <SimControls />
          <SimReplayPanel />
          <SimFeederToggle />
        </div>

        {/* Right: Trade Log + Account */}
        <div className="space-y-4">
          <SimAccountPanel wsUrl={WS_URL} />
          <SimTradeLog wsUrl={WS_URL} />
        </div>
      </div>
    </div>
  );
}
