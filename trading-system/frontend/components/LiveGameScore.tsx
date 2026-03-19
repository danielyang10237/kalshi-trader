'use client';

import { useState, useEffect, useRef, useCallback } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const WS_URL = API_URL.replace(/^http/, 'ws');

interface Player {
  id: string;
  name: string;
  position: string;
  jersey: string;
}

interface Injury {
  name: string;
  position: string;
  jersey: string;
  status: string;
  injury: string;
  return_date: string;
}

interface Leader {
  name: string;
  position: string;
  jersey: string;
  stat: string;
  stat_name: string;
  status: string;
}

interface RosterData {
  espn_game_id: string;
  roster: {
    home: Player[];
    away: Player[];
    injuries: { home: Injury[]; away: Injury[] };
    leaders: { home: Leader[]; away: Leader[] };
    predictor: { home_win_pct: string; away_win_pct: string } | null;
    odds: { spread: number; over_under: number; home_ml: number; away_ml: number } | null;
  };
}

interface TeamStats {
  score: number;
  fgm?: number;
  fga?: number;
  fg3m?: number;
  fg3a?: number;
  ftm?: number;
  fta?: number;
  oreb?: number;
  dreb?: number;
  ast?: number;
  stl?: number;
  tov?: number;
  blk?: number;
  pf?: number;
}

interface GameEvent {
  type: string;
  team: string;
  points?: number;
  ts: number;
}

interface GameState {
  game_id: string;
  home_team: string | null;
  away_team: string | null;
  home: TeamStats;
  away: TeamStats;
  possession: string | null;
  quarter: number;
  stopped?: boolean;
  events?: GameEvent[];
  // Enriched fields from engine
  home_wp?: number | null;
  prior_home_wp?: number | null;
  trader?: Record<string, unknown>;
}

export default function LiveGameScore({ gameId, priorHomeWp, homeBid, homeAsk, awayBid, awayAsk }: {
  gameId: string;
  priorHomeWp?: number | null;
  homeBid?: number | null;
  homeAsk?: number | null;
  awayBid?: number | null;
  awayAsk?: number | null;
}) {
  const [state, setState] = useState<GameState | null>(null);
  const [connected, setConnected] = useState(false);
  const [started, setStarted] = useState(false);
  const [roster, setRoster] = useState<RosterData | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLoggedTradeTime = useRef<number>(0);

  const handleMessage = useCallback((data: GameState) => {
    // Log paper trades (only once per trade)
    const trade = (data as unknown as Record<string, unknown>).last_trade as Record<string, unknown> | undefined;
    if (trade?.paper && typeof trade.time === 'number' && trade.time > lastLoggedTradeTime.current) {
      lastLoggedTradeTime.current = trade.time as number;
      console.log(
        `%c[PAPER TRADE] ${trade.action} ${(trade.side as string).toUpperCase()} YES ${trade.size}@${trade.price}¢ | edge=${trade.edge}¢ | model=${trade.model_price}¢ | ${trade.ticker}`,
        'color: #facc15; font-weight: bold; font-size: 13px'
      );
    }
    // Log debug info from engine
    const debug = (data as unknown as Record<string, unknown>)._debug as Record<string, unknown> | undefined;
    if (debug) {
      console.log(
        `%c[DEBUG] engine: home=${debug.engine_home_team} away=${debug.engine_away_team} | ` +
        `snapshot: home=${debug.snapshot_home_team} away=${debug.snapshot_away_team} | ` +
        `score: ${debug.home_score}-${debug.away_score} (diff=${debug.score_diff}) | ` +
        `prior=${debug.prior_home_wp} home_wp=${debug.home_wp}`,
        'color: #60a5fa; font-size: 11px'
      );
    }
    if (data.stopped) {
      setState(null);
      setStarted(false);
      return;
    }
    if (data.home_team) {
      setStarted(true);
      setState(data);
    }
  }, []);

  // Always connect to feed WebSocket so we get live updates including initial setup
  useEffect(() => {
    if (!gameId) return;

    function connect() {
      const ws = new WebSocket(`${WS_URL}/nba/ws/feed/${gameId}`);
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        reconnectRef.current = setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        try {
          handleMessage(JSON.parse(e.data) as GameState);
        } catch {}
      };
    }

    connect();

    return () => {
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      wsRef.current?.close();
    };
  }, [gameId, handleMessage]);

  // Fetch roster once on mount — server caches it, so this is a single call
  useEffect(() => {
    if (!gameId) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_URL}/nba/games/${gameId}/roster`);
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setRoster(data);
      } catch {}
    })();
    return () => { cancelled = true; };
  }, [gameId]);

  const handleStop = async () => {
    try {
      await fetch(`${API_URL}/nba/games/${gameId}/stop`, { method: 'POST' });
      setState(null);
      setStarted(false);
    } catch (err) {
      console.error('Failed to stop game:', err);
    }
  };

  if (!gameId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-600 text-xs p-4">
        <div className="text-center">
          <div className="text-lg mb-1">Live Game Score</div>
          <div className="text-[10px]">No game selected</div>
        </div>
      </div>
    );
  }

  // Parse team names from ticker (e.g. KXNBAGAME-26MAR09PHICLE -> away=PHI, home=CLE)
  const parseTeams = (ticker: string) => {
    const m = ticker.replace('KXNBAGAME-', '').match(/\d{2}[A-Z]{3}\d{1,2}([A-Z]{3})([A-Z]{3,4})$/);
    return m ? { away: m[1], home: m[2] } : { away: '???', home: '???' };
  };

  const liveActive = started && state && state.home_team;
  const teams = liveActive
    ? { away: state.away_team!, home: state.home_team! }
    : parseTeams(gameId);
  const lastEvents = liveActive ? [...(state?.events || [])].reverse().slice(0, 8) : [];

  return (
    <div className="flex flex-col h-full p-3">
      {/* Header with connection indicator and stop button */}
      <div className="flex items-center justify-between mb-3">
        <div className="text-[10px] text-gray-500 font-medium uppercase tracking-wide">
          {liveActive ? 'Live Score' : 'Pre-Game'}
        </div>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
          {liveActive && (
            <button
              onClick={handleStop}
              className="px-2 py-0.5 text-[10px] bg-red-600 hover:bg-red-700 text-white rounded transition-colors"
            >
              Stop
            </button>
          )}
        </div>
      </div>

      {/* Scoreboard */}
      <div className="bg-gray-900/50 rounded-lg p-4 mb-3">
        <div className="flex items-center justify-between">
          {/* Away team */}
          <div className="text-center flex-1">
            <div className="text-xs text-gray-400 mb-1">{teams.away}</div>
            <div className="text-3xl font-bold text-white">{liveActive ? state!.away?.score ?? 0 : '—'}</div>
          </div>

          {/* Separator */}
          <div className="text-gray-600 text-lg font-light px-3">@</div>

          {/* Home team */}
          <div className="text-center flex-1">
            <div className="text-xs text-gray-400 mb-1">{teams.home}</div>
            <div className="text-3xl font-bold text-white">{liveActive ? state!.home?.score ?? 0 : '—'}</div>
          </div>
        </div>

        {liveActive && (
          <div className="text-center mt-2">
            <span className="text-[10px] text-gray-500">
              {state!.quarter <= 4 ? `Q${state!.quarter}` : state!.quarter === 5 ? 'OT' : `${state!.quarter - 4}OT`}
            </span>
          </div>
        )}
      </div>

      {/* Live Posterior Win Probability (from GAM) */}
      {liveActive && state?.home_wp != null && (
        <div className="bg-blue-900/30 border border-blue-800/50 rounded-lg p-2 mb-3">
          <div className="text-[10px] text-blue-400 font-medium uppercase tracking-wide mb-1">Posterior Model (Live)</div>
          <div className="flex items-center justify-between text-xs">
            <div className="text-center flex-1">
              <div className="text-gray-400">{teams.away}</div>
              <div className="font-mono font-bold text-blue-300">{((1 - state.home_wp!) * 100).toFixed(1)}%</div>
            </div>
            <div className="text-gray-600 text-[10px] px-2">P(win)</div>
            <div className="text-center flex-1">
              <div className="text-gray-400">{teams.home}</div>
              <div className="font-mono font-bold text-blue-300">{(state.home_wp! * 100).toFixed(1)}%</div>
            </div>
          </div>
        </div>
      )}

      {/* Prior Win Probability */}
      {priorHomeWp != null && (
        <div className="bg-gray-900/50 rounded-lg p-2 mb-3">
          <div className="text-[10px] text-gray-500 font-medium uppercase tracking-wide mb-1">Prior Model</div>
          <div className="flex items-center justify-between text-xs">
            <div className="text-center flex-1">
              <div className="text-gray-400">{teams.away}</div>
              <div className="font-mono font-bold text-white">{((1 - priorHomeWp) * 100).toFixed(1)}%</div>
            </div>
            <div className="text-gray-600 text-[10px] px-2">P(win)</div>
            <div className="text-center flex-1">
              <div className="text-gray-400">{teams.home}</div>
              <div className="font-mono font-bold text-white">{(priorHomeWp * 100).toFixed(1)}%</div>
            </div>
          </div>
        </div>
      )}

      {/* Best Bid/Ask */}
      {(homeBid != null || homeAsk != null || awayBid != null || awayAsk != null) && (
        <div className="bg-gray-900/50 rounded-lg p-2 mb-3">
          <div className="text-[10px] text-gray-500 font-medium uppercase tracking-wide mb-1">Market Prices</div>
          <div className="flex items-center justify-between text-xs">
            <div className="text-center flex-1">
              <div className="text-gray-400 mb-0.5">{teams.away}</div>
              <div className="flex justify-center gap-2 font-mono text-[11px]">
                <span className="text-green-400">{awayBid != null ? `${awayBid}¢` : '—'}</span>
                <span className="text-gray-600">/</span>
                <span className="text-red-400">{awayAsk != null ? `${awayAsk}¢` : '—'}</span>
              </div>
            </div>
            <div className="text-gray-600 text-[9px] px-1">bid/ask</div>
            <div className="text-center flex-1">
              <div className="text-gray-400 mb-0.5">{teams.home}</div>
              <div className="flex justify-center gap-2 font-mono text-[11px]">
                <span className="text-green-400">{homeBid != null ? `${homeBid}¢` : '—'}</span>
                <span className="text-gray-600">/</span>
                <span className="text-red-400">{homeAsk != null ? `${homeAsk}¢` : '—'}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ESPN Data */}
      {roster && (() => {
        const r = roster.roster;
        const hasRoster = r.home.length > 0 || r.away.length > 0;
        const hasInjuries = r.injuries?.home?.length > 0 || r.injuries?.away?.length > 0;
        const hasLeaders = r.leaders?.home?.length > 0 || r.leaders?.away?.length > 0;
        const teamName = (side: 'home' | 'away') => teams[side];

        return (
          <>
            {/* Odds & Predictor */}
            {(r.odds || r.predictor) && (
              <div className="flex items-center gap-3 mb-2 text-[9px] text-gray-400">
                {r.odds && (
                  <>
                    <span>Spread: <span className="text-gray-300">{r.odds.spread > 0 ? '+' : ''}{r.odds.spread}</span></span>
                    <span>O/U: <span className="text-gray-300">{r.odds.over_under}</span></span>
                    <span>ML: <span className="text-gray-300">{r.odds.home_ml}/{r.odds.away_ml}</span></span>
                  </>
                )}
                {r.predictor && (
                  <span>Win%: <span className="text-gray-300">{r.predictor.home_win_pct}H / {r.predictor.away_win_pct}A</span></span>
                )}
              </div>
            )}

            {/* Active Roster (in-game) */}
            {hasRoster && (
              <div className="mb-2">
                <div className="text-[10px] text-gray-500 font-medium uppercase tracking-wide mb-1">Active Roster</div>
                <div className="grid grid-cols-2 gap-2">
                  {(['away', 'home'] as const).map((side) => (
                    <div key={side}>
                      <div className="text-[10px] text-gray-400 font-semibold mb-0.5">{teamName(side)}</div>
                      <div className="space-y-px">
                        {r[side].map((p, i) => (
                          <div key={i} className="text-[9px] text-gray-300">
                            {p.jersey ? `#${p.jersey} ` : ''}{p.name} {p.position ? `(${p.position})` : ''}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Injuries */}
            {hasInjuries && (
              <div className="mb-2">
                <div className="text-[10px] text-gray-500 font-medium uppercase tracking-wide mb-1">Injuries</div>
                <div className="grid grid-cols-2 gap-2">
                  {(['away', 'home'] as const).map((side) => (
                    <div key={side}>
                      <div className="text-[10px] text-gray-400 font-semibold mb-0.5">{teamName(side)}</div>
                      <div className="space-y-px">
                        {(r.injuries?.[side] || []).map((inj, i) => (
                          <div key={i} className="text-[9px]">
                            <span className={inj.status === 'Out' ? 'text-red-400' : inj.status === 'Day-To-Day' ? 'text-yellow-400' : 'text-orange-400'}>
                              {inj.status}
                            </span>
                            {' '}
                            <span className="text-gray-400">
                              {inj.name} {inj.position ? `(${inj.position})` : ''} — {inj.injury}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Leaders (pre-game) */}
            {hasLeaders && (
              <div className="mb-2">
                <div className="text-[10px] text-gray-500 font-medium uppercase tracking-wide mb-1">Key Players</div>
                <div className="grid grid-cols-2 gap-2">
                  {(['away', 'home'] as const).map((side) => (
                    <div key={side}>
                      <div className="text-[10px] text-gray-400 font-semibold mb-0.5">{teamName(side)}</div>
                      <div className="space-y-px">
                        {(r.leaders?.[side] || []).map((l, i) => (
                          <div key={i} className="text-[9px] text-gray-400">
                            <span className={l.status && l.status !== 'Active' ? 'text-red-400' : 'text-gray-200'}>
                              {l.name}
                            </span>
                            {' '}{l.stat} {l.stat_name}
                            {l.status && l.status !== 'Active' && <span className="text-red-400"> ({l.status})</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        );
      })()}

      {/* Recent events */}
      {lastEvents.length > 0 && (
        <div className="flex-1 overflow-y-auto">
          <div className="text-[10px] text-gray-500 font-medium uppercase tracking-wide mb-1">Recent</div>
          <div className="space-y-1">
            {lastEvents.map((ev, i) => (
              <div key={i} className="flex justify-between text-[10px] text-gray-400 py-0.5">
                <span>{ev.team === 'home' ? teams.home : teams.away} {ev.type}{ev.points ? ` +${ev.points}` : ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
