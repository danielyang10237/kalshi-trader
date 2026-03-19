'use client';

import { useState, useEffect, useRef } from 'react';
import { fetchNBAGames, checkGameStatus, KalshiEvent } from '@/lib/api';
import { GameStateManager } from '@/lib/gameState';

// ============================================================
// Router — state-based SPA navigation
// ============================================================

type Route =
  | { page: 'list' }
  | { page: 'detail'; event: KalshiEvent }
  | { page: 'dashboard'; gameId: string; homeTeam: string; awayTeam: string; alreadyStarted: boolean };

export default function App() {
  const [route, setRoute] = useState<Route>({ page: 'list' });

  switch (route.page) {
    case 'list':
      return <GameListPage onSelect={(e) => setRoute({ page: 'detail', event: e })} />;
    case 'detail':
      return (
        <GameDetailPage
          event={route.event}
          onBack={() => setRoute({ page: 'list' })}
          onStart={(gameId, home, away, started) =>
            setRoute({ page: 'dashboard', gameId, homeTeam: home, awayTeam: away, alreadyStarted: started })
          }
        />
      );
    case 'dashboard':
      return (
        <DashboardPage
          gameId={route.gameId}
          homeTeam={route.homeTeam}
          awayTeam={route.awayTeam}
          alreadyStarted={route.alreadyStarted}
          onExit={() => setRoute({ page: 'list' })}
        />
      );
  }
}

// ============================================================
// Game List
// ============================================================

function GameListPage({ onSelect }: { onSelect: (e: KalshiEvent) => void }) {
  const [events, setEvents] = useState<KalshiEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setEvents(await fetchNBAGames());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="max-w-lg mx-auto px-4 py-6">
        <h1 className="text-2xl font-bold mb-6">NBA Games</h1>
        {loading ? (
          <div className="text-center text-gray-400 py-12">Loading games...</div>
        ) : error ? (
          <div className="text-center py-12">
            <div className="text-orange-400 text-4xl mb-4">&#9888;</div>
            <div className="text-gray-400 mb-4">{error}</div>
            <button onClick={load} className="px-4 py-2 bg-blue-600 rounded-lg text-sm font-semibold">Retry</button>
          </div>
        ) : events.length === 0 ? (
          <div className="text-center text-gray-500 py-12">No active NBA games</div>
        ) : (
          <div className="space-y-2">
            {events.map((event) => (
              <button
                key={event.event_ticker}
                onClick={() => onSelect(event)}
                className="block w-full text-left px-4 py-4 bg-gray-900 rounded-xl hover:bg-gray-800 transition-colors"
              >
                <div className="text-base font-semibold">{event.title}</div>
                {event.sub_title && <div className="text-sm text-gray-400 mt-1">{event.sub_title}</div>}
              </button>
            ))}
            <button onClick={load} className="w-full mt-4 py-2 text-sm text-gray-400 hover:text-white">Refresh</button>
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// Game Detail
// ============================================================

function parseTeamCodes(subTitle?: string): { home: string; away: string } {
  if (!subTitle) return { home: '???', away: '???' };
  const parenIdx = subTitle.indexOf('(');
  if (parenIdx === -1) return { home: '???', away: '???' };
  const teamPart = subTitle.slice(0, parenIdx).trim();
  const parts = teamPart.split(' at ');
  if (parts.length !== 2) return { home: '???', away: '???' };
  return { home: parts[1].trim(), away: parts[0].trim() };
}

function GameDetailPage({
  event,
  onBack,
  onStart,
}: {
  event: KalshiEvent;
  onBack: () => void;
  onStart: (gameId: string, home: string, away: string, started: boolean) => void;
}) {
  const [loading, setLoading] = useState(true);
  const [gameStarted, setGameStarted] = useState(false);
  const teams = parseTeamCodes(event.sub_title);

  useEffect(() => {
    (async () => {
      setLoading(true);
      const status = await checkGameStatus(event.event_ticker);
      setGameStarted(status.started);
      setLoading(false);
    })();
  }, [event.event_ticker]);

  return (
    <div className="min-h-screen bg-black text-white flex flex-col items-center justify-center px-4">
      <button onClick={onBack} className="absolute top-4 left-4 text-gray-400 hover:text-white text-sm">&larr; Back</button>
      <h1 className="text-2xl font-bold text-center mb-2">{event.title}</h1>
      {event.sub_title && <div className="text-gray-400 text-sm mb-8">{event.sub_title}</div>}
      {loading ? (
        <div className="text-gray-400">Checking game status...</div>
      ) : (
        <button
          onClick={() => onStart(event.event_ticker, teams.home, teams.away, gameStarted)}
          className={`px-12 py-4 rounded-xl text-lg font-bold text-white ${
            gameStarted ? 'bg-green-600 hover:bg-green-700' : 'bg-blue-600 hover:bg-blue-700'
          } transition-colors`}
        >
          {gameStarted ? 'Resume Game' : 'Start Game'}
        </button>
      )}
    </div>
  );
}

// ============================================================
// Dashboard
// ============================================================

function useGameState(mgr: GameStateManager) {
  const [, setTick] = useState(0);
  useEffect(() => mgr.subscribe(() => setTick((t) => t + 1)), [mgr]);
  return {
    possession: mgr.possession,
    quarter: mgr.quarter,
    timeRange: mgr.timeRange,
    timerSeconds: mgr.timerSeconds,
    timerRunning: mgr.timerRunning,
    isConnected: mgr.isConnected,
    isStopped: mgr.isStopped,
    homeStats: mgr.homeStats,
    awayStats: mgr.awayStats,
    lastAction: mgr.lastAction,
    events: mgr.events,
  };
}

function DashboardPage({
  gameId, homeTeam, awayTeam, alreadyStarted, onExit,
}: {
  gameId: string; homeTeam: string; awayTeam: string; alreadyStarted: boolean; onExit: () => void;
}) {
  const mgrRef = useRef<GameStateManager | null>(null);
  if (!mgrRef.current) {
    mgrRef.current = new GameStateManager(gameId, homeTeam, awayTeam, alreadyStarted);
  }
  const mgr = mgrRef.current;
  const state = useGameState(mgr);

  useEffect(() => { mgr.connect(); return () => mgr.disconnect(); }, [mgr]);
  useEffect(() => { if (state.isStopped) onExit(); }, [state.isStopped]);

  const side = state.possession ?? 'home';
  const otherSide = side === 'home' ? 'away' : 'home';
  const teamCode = side === 'home' ? homeTeam : awayTeam;
  const otherTeamCode = side === 'home' ? awayTeam : homeTeam;
  const teamColor = side === 'home' ? 'bg-blue-600' : 'bg-orange-500';
  const otherColor = side === 'home' ? 'bg-orange-500' : 'bg-blue-600';
  const teamTextColor = side === 'home' ? 'text-blue-400' : 'text-orange-400';
  const isOT = state.quarter >= 5;
  const timeRanges = state.quarter <= 3 ? ['12-9', '9-6', '6-3', '3-0']
    : state.quarter === 4 ? ['12-9', '9-7', '7-5', '5-0'] : [];

  // FT modal
  const [showFT, setShowFT] = useState(false);
  const [ftTeam, setFtTeam] = useState('home');
  const [ftCount, setFtCount] = useState(0);

  // Rebound prompt
  const [showRebound, setShowRebound] = useState(false);
  const [reboundGroupId, setReboundGroupId] = useState('');
  const [reboundMissTeam, setReboundMissTeam] = useState('home');

  // Timeout
  const [showTimeout, setShowTimeout] = useState(false);
  const [timeoutTeam, setTimeoutTeam] = useState('home');
  const [timeoutSeconds, setTimeoutSeconds] = useState(75);
  const timeoutRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Timer row
  const [showTimer, setShowTimer] = useState(false);
  const [selectedTimeRange, setSelectedTimeRange] = useState('12-9');

  useEffect(() => {
    if (state.timeRange === '5-0' || state.quarter >= 5) {
      setShowTimer(true);
      setSelectedTimeRange(state.timeRange);
    }
  }, []);

  const makeEvent = (type: string, team: string, value?: number | null, detail?: string | null, groupId?: string) =>
    mgr.makeEvent(type, team, value, detail, groupId);

  const switchPossession = (groupId?: string) =>
    mgr.pushEvent(makeEvent('possession', otherSide, null, `${otherTeamCode} Ball`, groupId));

  const selectQuarter = (q: number) => {
    mgr.pushEvent(makeEvent('quarter', side, q, q >= 5 ? 'OT' : `Q${q}`));
    if (q >= 5) {
      setSelectedTimeRange(''); setShowTimer(true);
      mgr.timerSeconds = 300; mgr.stopGameTimer(); mgr.toggleGameTimer();
    } else {
      setSelectedTimeRange('12-9');
      mgr.pushEvent(makeEvent('time_range', side, null, '12-9'));
      setShowTimer(false); mgr.stopGameTimer();
    }
  };

  const selectTimeRange = (range: string) => {
    setSelectedTimeRange(range);
    mgr.pushEvent(makeEvent('time_range', side, null, range));
    if (state.quarter === 4 && range === '5-0') {
      setShowTimer(true); mgr.timerSeconds = 300; mgr.stopGameTimer(); mgr.toggleGameTimer();
    } else {
      setShowTimer(false); mgr.stopGameTimer();
    }
  };

  const timerDisplay = `${Math.floor(state.timerSeconds / 60)}:${String(state.timerSeconds % 60).padStart(2, '0')}`;

  const timeoutsRemaining = (team: string) => {
    const used = team === 'home' ? state.homeStats.timeouts_used : state.awayStats.timeouts_used;
    return Math.max(0, 7 - used);
  };

  const startTimeoutFn = (team: string) => {
    setTimeoutTeam(team); setTimeoutSeconds(75); setShowTimeout(true);
    timeoutRef.current = setInterval(() => {
      setTimeoutSeconds((s) => { if (s <= 1) { if (timeoutRef.current) clearInterval(timeoutRef.current); return 0; } return s - 1; });
    }, 1000);
  };
  const cancelTimeout = () => { if (timeoutRef.current) clearInterval(timeoutRef.current); setShowTimeout(false); };
  const endTimeout = () => {
    if (timeoutRef.current) clearInterval(timeoutRef.current); setShowTimeout(false);
    const code = timeoutTeam === 'home' ? homeTeam : awayTeam;
    mgr.pushEvent(makeEvent('timeout', timeoutTeam, null, `${code} Timeout`));
  };
  const timeoutDisplay = `${Math.floor(timeoutSeconds / 60)}:${String(timeoutSeconds % 60).padStart(2, '0')}`;

  // ---- Tipoff ----
  if (state.possession === null && !showFT && !showTimeout) {
    return (
      <div className="fixed inset-0 bg-black flex">
        <button onClick={() => mgr.pushEvent(makeEvent('possession', 'home', null, `${homeTeam} Ball`))}
          className="flex-1 flex flex-col items-center justify-center hover:bg-blue-900/20 transition-colors">
          <div className="text-4xl font-bold text-blue-400">{homeTeam}</div>
          <div className="text-sm text-gray-500 mt-2">Tap for possession</div>
        </button>
        <div className="w-px bg-gray-700" />
        <button onClick={() => mgr.pushEvent(makeEvent('possession', 'away', null, `${awayTeam} Ball`))}
          className="flex-1 flex flex-col items-center justify-center hover:bg-orange-900/20 transition-colors">
          <div className="text-4xl font-bold text-orange-400">{awayTeam}</div>
          <div className="text-sm text-gray-500 mt-2">Tap for possession</div>
        </button>
        <div className="absolute top-3 left-1/2 -translate-x-1/2 text-xs text-gray-500 bg-gray-900/90 px-3 py-1 rounded-lg">TIPOFF</div>
      </div>
    );
  }

  // ---- Free Throw ----
  if (showFT) {
    return <FreeThrowModal team={ftTeam} teamCode={ftTeam === 'home' ? homeTeam : awayTeam}
      totalFTs={ftCount} color={ftTeam === 'home' ? 'blue' : 'orange'} mgr={mgr} quarter={state.quarter}
      onDone={(lastMissed) => {
        setShowFT(false);
        if (lastMissed) {
          setReboundMissTeam(ftTeam);
          setReboundGroupId(mgr.events[mgr.events.length - 1]?.groupId ?? crypto.randomUUID());
          setShowRebound(true);
        } else {
          const other = ftTeam === 'home' ? 'away' : 'home';
          const otherCode = ftTeam === 'home' ? awayTeam : homeTeam;
          mgr.pushEvent(makeEvent('possession', other, null, `${otherCode} Ball`));
        }
      }} />;
  }

  // ---- Timeout ----
  if (showTimeout) {
    const toCode = timeoutTeam === 'home' ? homeTeam : awayTeam;
    const toColor = timeoutTeam === 'home' ? 'text-blue-400' : 'text-orange-400';
    const toBg = timeoutTeam === 'home' ? 'bg-blue-600' : 'bg-orange-500';
    return (
      <div className="fixed inset-0 bg-black flex flex-col items-center justify-center">
        <div className={`text-3xl font-bold ${toColor} mb-6`}>{toCode} TIMEOUT</div>
        <div className={`text-7xl font-black font-mono ${timeoutSeconds <= 10 ? 'text-red-500' : 'text-white'}`}>{timeoutDisplay}</div>
        <div className="flex gap-1 mt-6">
          {Array.from({ length: 7 }).map((_, i) => (
            <span key={i} className={`text-sm ${i < timeoutsRemaining(timeoutTeam) ? toColor : 'text-gray-700'}`}>&#9201;</span>
          ))}
        </div>
        <div className="flex gap-6 mt-12">
          <button onClick={cancelTimeout} className="px-8 py-3 bg-gray-700 rounded-xl text-base font-semibold">Cancel</button>
          <button onClick={endTimeout} className={`px-8 py-3 ${toBg} rounded-xl text-base font-semibold text-white`}>End Timeout</button>
        </div>
      </div>
    );
  }

  // ---- Main Dashboard ----
  return (
    <div className="fixed inset-0 bg-black flex">
      {/* LEFT: Scoring */}
      <div className="flex-1 flex flex-col p-2 gap-1.5 overflow-hidden">
        {/* Time taskbar */}
        <div className="bg-gray-900/85 rounded-xl p-2 space-y-1.5">
          <div className="flex gap-1.5 items-center">
            <button onClick={onExit} className="w-9 h-9 bg-gray-800 rounded-lg text-gray-400 hover:text-white flex items-center justify-center text-lg">&#8249;</button>
            <div className="w-px h-6 bg-gray-700" />
            {[1, 2, 3, 4].map((q) => (
              <button key={q} onClick={() => selectQuarter(q)}
                className={`px-3 h-9 rounded-lg text-sm font-bold ${state.quarter === q ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400'}`}>Q{q}</button>
            ))}
            <button onClick={() => selectQuarter(5)}
              className={`px-3 h-9 rounded-lg text-sm font-bold ${isOT ? 'bg-red-600 text-white' : 'bg-gray-800 text-gray-400'}`}>OT</button>
          </div>
          {timeRanges.length > 0 && (
            <div className="flex gap-1.5">
              {timeRanges.map((r) => (
                <button key={r} onClick={() => selectTimeRange(r)}
                  className={`flex-1 h-9 rounded-lg text-sm font-semibold ${selectedTimeRange === r ? 'bg-green-600/80 text-white' : 'bg-gray-800 text-gray-400'}`}>{r}</button>
              ))}
            </div>
          )}
          {showTimer && (
            <div className="flex gap-1.5 items-center justify-center">
              {[-10, -5, -2].map((d) => (
                <button key={d} onClick={() => mgr.adjustTimer(d)} className="px-2 h-6 bg-gray-800 rounded text-xs font-semibold text-red-400">{d}</button>
              ))}
              <div className={`w-14 text-center font-mono font-black text-base ${state.timerSeconds <= 60 ? 'text-red-500' : 'text-white'}`}>{timerDisplay}</div>
              {[2, 5, 10].map((d) => (
                <button key={d} onClick={() => mgr.adjustTimer(d)} className="px-2 h-6 bg-gray-800 rounded text-xs font-semibold text-green-400">+{d}</button>
              ))}
              <div className="w-px h-4 bg-gray-700" />
              <button onClick={() => mgr.toggleGameTimer()}
                className={`w-8 h-6 rounded text-xs font-bold text-white ${state.timerRunning ? 'bg-orange-500' : 'bg-green-600'}`}>
                {state.timerRunning ? '||' : '\u25B6'}
              </button>
            </div>
          )}
        </div>

        {/* Possession indicator */}
        <div className="flex items-center gap-2 bg-gray-900/85 rounded-lg px-3 py-1.5 self-start">
          <div className={`w-2.5 h-2.5 rounded-full ${side === 'home' ? 'bg-blue-500' : 'bg-orange-500'}`} />
          <span className={`text-sm font-bold ${teamTextColor}`}>{teamCode} Possession</span>
        </div>

        {/* Scoreboard */}
        <div className="bg-gray-900/85 rounded-xl p-2">
          <div className="flex items-center justify-center gap-4">
            <div className={`w-24 h-16 rounded-xl flex flex-col items-center justify-center ${side === 'away' ? 'bg-orange-500/20' : 'bg-orange-500/5'}`}>
              <div className="text-xs font-semibold text-orange-400">{awayTeam}</div>
              <div className="text-3xl font-black">{state.awayStats.score}</div>
            </div>
            <span className="text-gray-500 text-lg">&mdash;</span>
            <div className={`w-24 h-16 rounded-xl flex flex-col items-center justify-center ${side === 'home' ? 'bg-blue-500/20' : 'bg-blue-500/5'}`}>
              <div className="text-xs font-semibold text-blue-400">{homeTeam}</div>
              <div className="text-3xl font-black">{state.homeStats.score}</div>
            </div>
          </div>
          <div className="flex justify-center gap-8 mt-2">
            <div className="flex gap-0.5">
              {Array.from({ length: 7 }).map((_, i) => (
                <span key={i} className={`text-[8px] ${i < timeoutsRemaining('away') ? 'text-orange-400' : 'text-gray-700'}`}>&#9201;</span>
              ))}
            </div>
            <div className="flex gap-0.5">
              {Array.from({ length: 7 }).map((_, i) => (
                <span key={i} className={`text-[8px] ${i < timeoutsRemaining('home') ? 'text-blue-400' : 'text-gray-700'}`}>&#9201;</span>
              ))}
            </div>
          </div>
        </div>

        {/* Shot buttons */}
        <div className="bg-gray-900/85 rounded-xl p-2 space-y-1.5">
          <div className="grid grid-cols-2 gap-1.5">
            <ShotBtn label="+2 Made" color={teamColor} onClick={() => {
              const g = crypto.randomUUID();
              mgr.pushEvent(makeEvent('fg_made', side, 2, `${teamCode} +2 Made`, g));
              mgr.pushEvent(makeEvent('possession', otherSide, null, `${otherTeamCode} Ball`, g));
            }} />
            <ShotBtn label="+3 Made" color={teamColor} onClick={() => {
              const g = crypto.randomUUID();
              mgr.pushEvent(makeEvent('fg_made', side, 3, `${teamCode} +3 Made`, g));
              mgr.pushEvent(makeEvent('possession', otherSide, null, `${otherTeamCode} Ball`, g));
            }} />
            <ShotBtn label="+2 Miss" color="bg-gray-600" onClick={() => {
              const g = crypto.randomUUID();
              mgr.pushEvent(makeEvent('fg_miss', side, 2, `${teamCode} +2 Miss`, g));
              setReboundGroupId(g); setReboundMissTeam(side); setShowRebound(true);
            }} />
            <ShotBtn label="+3 Miss" color="bg-gray-600" onClick={() => {
              const g = crypto.randomUUID();
              mgr.pushEvent(makeEvent('fg_miss', side, 3, `${teamCode} +3 Miss`, g));
              setReboundGroupId(g); setReboundMissTeam(side); setShowRebound(true);
            }} />
          </div>
          <div className="grid grid-cols-5 gap-1">
            <FoulBtn label="And1 2pt" onClick={() => {
              const g = crypto.randomUUID();
              mgr.pushEvent(makeEvent('fg_made', side, 2, `${teamCode} And1 2pt`, g));
              mgr.pushEvent(makeEvent('foul', otherSide, null, `${otherTeamCode} Foul`, g));
              setFtTeam(side); setFtCount(1); setShowFT(true);
            }} />
            <FoulBtn label="Foul 2pt" onClick={() => {
              const g = crypto.randomUUID();
              mgr.pushEvent(makeEvent('foul', otherSide, null, `${otherTeamCode} Foul 2pt`, g));
              setFtTeam(side); setFtCount(2); setShowFT(true);
            }} />
            <FoulBtn label="And1 3pt" onClick={() => {
              const g = crypto.randomUUID();
              mgr.pushEvent(makeEvent('fg_made', side, 3, `${teamCode} And1 3pt`, g));
              mgr.pushEvent(makeEvent('foul', otherSide, null, `${otherTeamCode} Foul`, g));
              setFtTeam(side); setFtCount(1); setShowFT(true);
            }} />
            <FoulBtn label="Foul 3pt" onClick={() => {
              const g = crypto.randomUUID();
              mgr.pushEvent(makeEvent('foul', otherSide, null, `${otherTeamCode} Foul 3pt`, g));
              setFtTeam(side); setFtCount(3); setShowFT(true);
            }} />
            <FoulBtn label="Off Foul" onClick={() => {
              const g = crypto.randomUUID();
              mgr.pushEvent(makeEvent('off_foul', side, null, `${teamCode} Off Foul`, g));
              mgr.pushEvent(makeEvent('possession', otherSide, null, `${otherTeamCode} Ball`, g));
            }} />
          </div>
        </div>

        {state.lastAction && (
          <div className="bg-gray-900/85 rounded-lg px-3 py-1 self-start text-xs text-gray-400">{state.lastAction}</div>
        )}
      </div>

      {/* RIGHT: Controls */}
      <div className="w-20 bg-gray-900/85 rounded-xl m-2 flex flex-col items-center py-2 gap-2">
        <div className="flex-1" />
        <SideBtn icon="&#8644;" label="Switch" color={otherColor} onClick={() => switchPossession()} />
        <SideBtn icon="&#9995;" label="Steal" color={otherColor} onClick={() => {
          const g = crypto.randomUUID();
          mgr.pushEvent(makeEvent('steal', otherSide, null, `${otherTeamCode} Steal`, g));
          mgr.pushEvent(makeEvent('turnover', side, null, `${teamCode} TOV`, g));
          mgr.pushEvent(makeEvent('possession', otherSide, null, `${otherTeamCode} Ball`, g));
        }} />
        <SideBtn icon="&#9888;" label="TOV" color="bg-red-600/70" onClick={() => {
          const g = crypto.randomUUID();
          mgr.pushEvent(makeEvent('turnover', side, null, `${teamCode} TOV`, g));
          mgr.pushEvent(makeEvent('possession', otherSide, null, `${otherTeamCode} Ball`, g));
        }} />
        <SideBtn icon="&#9201;" label="T/O" color="bg-yellow-600/80" onClick={() => startTimeoutFn(side)} />
        <div className="flex-1" />
        <SideBtn icon="&#8617;" label="Undo" color="bg-gray-600" onClick={() => mgr.undoLast()} small />
        <button onClick={() => mgr.sendStop()}
          className="w-16 h-7 bg-red-600 rounded-lg text-[10px] font-semibold text-white">Stop</button>
      </div>

      {/* Rebound overlay */}
      {showRebound && (
        <ReboundPrompt missTeam={reboundMissTeam} homeTeam={homeTeam} awayTeam={awayTeam}
          groupId={reboundGroupId} mgr={mgr} onDone={() => setShowRebound(false)} />
      )}
    </div>
  );
}

// ============================================================
// Shared Components
// ============================================================

function ShotBtn({ label, color, onClick }: { label: string; color: string; onClick: () => void }) {
  return <button onClick={onClick} className={`${color} text-white font-bold text-sm h-10 rounded-xl active:opacity-70`}>{label}</button>;
}

function FoulBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return <button onClick={onClick} className="bg-purple-600/80 text-white text-[10px] font-semibold h-8 rounded-lg leading-tight active:opacity-70">{label}</button>;
}

function SideBtn({ icon, label, color, onClick, small }: {
  icon: string; label: string; color: string; onClick: () => void; small?: boolean;
}) {
  return (
    <button onClick={onClick} className={`${color} w-16 ${small ? 'h-10' : 'h-12'} rounded-xl flex flex-col items-center justify-center text-white active:opacity-70`}>
      <span className={small ? 'text-base' : 'text-xl'} dangerouslySetInnerHTML={{ __html: icon }} />
      <span className="text-[9px] font-semibold">{label}</span>
    </button>
  );
}

function ReboundPrompt({ missTeam, homeTeam, awayTeam, groupId, mgr, onDone }: {
  missTeam: string; homeTeam: string; awayTeam: string; groupId: string;
  mgr: GameStateManager; onDone: () => void;
}) {
  const missCode = missTeam === 'home' ? homeTeam : awayTeam;
  const otherTeam = missTeam === 'home' ? 'away' : 'home';
  const otherCode = missTeam === 'home' ? awayTeam : homeTeam;
  const missColor = missTeam === 'home' ? 'bg-blue-600' : 'bg-orange-500';
  const otherRebColor = missTeam === 'home' ? 'bg-orange-500' : 'bg-blue-600';
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-gray-900 rounded-2xl p-6 shadow-2xl">
        <div className="text-lg font-bold text-center mb-4">Rebound?</div>
        <div className="flex gap-5">
          <button onClick={() => { mgr.pushEvent(mgr.makeEvent('oreb', missTeam, null, `${missCode} O-Reb`, groupId)); onDone(); }}
            className={`${missColor} w-28 h-20 rounded-xl flex flex-col items-center justify-center text-white`}>
            <span className="text-2xl">&#8593;</span>
            <span className="text-sm font-bold">O-Reb</span>
            <span className="text-xs">{missCode}</span>
          </button>
          <button onClick={() => {
            mgr.pushEvent(mgr.makeEvent('dreb', otherTeam, null, `${otherCode} D-Reb`, groupId));
            mgr.pushEvent(mgr.makeEvent('possession', otherTeam, null, `${otherCode} Ball`, groupId));
            onDone();
          }} className={`${otherRebColor} w-28 h-20 rounded-xl flex flex-col items-center justify-center text-white`}>
            <span className="text-2xl">&#127935;</span>
            <span className="text-sm font-bold">D-Reb</span>
            <span className="text-xs">{otherCode}</span>
          </button>
        </div>
      </div>
    </div>
  );
}

function FreeThrowModal({ team, teamCode, totalFTs, color, mgr, quarter, onDone }: {
  team: string; teamCode: string; totalFTs: number; color: 'blue' | 'orange';
  mgr: GameStateManager; quarter: number; onDone: (lastFTMissed: boolean) => void;
}) {
  const [completed, setCompleted] = useState(0);
  const [made, setMade] = useState(0);
  const [missed, setMissed] = useState(0);
  const [ftEventCount, setFtEventCount] = useState(0);
  const [lastWasMiss, setLastWasMiss] = useState(false);
  const colorClass = color === 'blue' ? 'text-blue-400' : 'text-orange-400';

  const checkDone = (n: number, wasMiss: boolean) => {
    if (n >= totalFTs) setTimeout(() => onDone(wasMiss), 500);
  };

  return (
    <div className="fixed inset-0 bg-black flex flex-col items-center justify-center">
      <div className={`text-2xl font-bold ${colorClass} mb-2`}>{teamCode} Free Throws</div>
      <div className="text-gray-400 text-lg mb-6">FT {completed + 1} of {totalFTs}</div>
      <div className="flex gap-8 mb-8">
        <div className="text-center"><div className="text-4xl font-bold text-green-400">{made}</div><div className="text-sm text-gray-500">Made</div></div>
        <div className="text-center"><div className="text-4xl font-bold text-red-400">{missed}</div><div className="text-sm text-gray-500">Missed</div></div>
      </div>
      {completed < totalFTs && (
        <div className="flex gap-6">
          <button onClick={() => {
            mgr.pushEvent(mgr.makeEvent('ft_made', team, null, `${teamCode} FT Made`));
            setMade((m) => m + 1);
            setCompleted((c) => { const n = c + 1; checkDone(n, false); return n; });
            setFtEventCount((c) => c + 1);
            setLastWasMiss(false);
          }} className="w-28 h-16 bg-green-600 rounded-xl text-xl font-bold text-white active:opacity-70">Made</button>
          <button onClick={() => {
            mgr.pushEvent(mgr.makeEvent('ft_miss', team, null, `${teamCode} FT Miss`));
            setMissed((m) => m + 1);
            setCompleted((c) => { const n = c + 1; checkDone(n, true); return n; });
            setFtEventCount((c) => c + 1);
            setLastWasMiss(true);
          }} className="w-28 h-16 bg-red-600 rounded-xl text-xl font-bold text-white active:opacity-70">Missed</button>
        </div>
      )}
      <button onClick={() => { mgr.undoLastN(ftEventCount); onDone(false); }}
        className="mt-12 text-red-400 text-sm flex items-center gap-2">&#8617; Cancel &amp; Undo</button>
    </div>
  );
}
