/**
 * WebSocket manager and game state engine.
 * Port of WebSocketManager.swift — event-sourced state with WS sync.
 */

import { wsUrl } from './api';

// ---- Data Models ----

export interface GameEvent {
  id: string;
  timestamp: number;
  type: string;
  team: string;
  value?: number | null;
  quarter: number;
  detail?: string | null;
  groupId: string;
}

export interface TeamStats {
  score: number;
  fgm: number;
  fga: number;
  fg3m: number;
  fg3a: number;
  ftm: number;
  fta: number;
  oreb: number;
  dreb: number;
  tov: number;
  stl: number;
  pf: number;
  timeouts_used: number;
  period_fouls: Record<string, number>;
}

export interface GameSnapshot {
  game_id: string;
  timestamp: number;
  home_team: string;
  away_team: string;
  possession?: string | null;
  quarter: number;
  time_range: string;
  timer_seconds: number;
  stopped: boolean;
  home: TeamStats;
  away: TeamStats;
  events: GameEvent[];
  last_action: string;
}

function zeroStats(): TeamStats {
  return {
    score: 0, fgm: 0, fga: 0, fg3m: 0, fg3a: 0,
    ftm: 0, fta: 0, oreb: 0, dreb: 0,
    tov: 0, stl: 0, pf: 0,
    timeouts_used: 0, period_fouls: {},
  };
}

function uuid(): string {
  return crypto.randomUUID();
}

// ---- State Manager ----

export type Listener = () => void;

export class GameStateManager {
  // Public state
  possession: string | null = null;
  quarter = 1;
  timeRange = '12-9';
  timerSeconds = 300;
  timerRunning = false;
  isConnected = false;
  isStopped = false;
  homeStats: TeamStats = zeroStats();
  awayStats: TeamStats = zeroStats();
  lastAction = '';
  events: GameEvent[] = [];

  readonly gameId: string;
  readonly homeTeam: string;
  readonly awayTeam: string;

  private ws: WebSocket | null = null;
  private isSetup: boolean;
  private snapshotTimestamp = 0;
  private timerInterval: ReturnType<typeof setInterval> | null = null;
  private listeners: Set<Listener> = new Set();
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;

  constructor(gameId: string, homeTeam: string, awayTeam: string, alreadyStarted: boolean) {
    this.gameId = gameId;
    this.homeTeam = homeTeam;
    this.awayTeam = awayTeam;
    this.isSetup = alreadyStarted;
    this.restoreFromLocal();
  }

  // ---- Subscribe ----

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private notify() {
    this.listeners.forEach((l) => l());
  }

  // ---- Connection ----

  connect() {
    if (this.ws) return;
    const url = wsUrl(this.gameId);
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.isConnected = true;
      this.notify();
      if (!this.isSetup) {
        this.sendSetup();
      }
    };

    this.ws.onmessage = (e) => {
      if (typeof e.data === 'string') this.handleMessage(e.data);
    };

    this.ws.onclose = () => {
      this.ws = null;
      this.isConnected = false;
      this.notify();
      if (!this.isStopped) {
        this.reconnectTimeout = setTimeout(() => this.connect(), 2000);
      }
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  disconnect() {
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
    this.reconnectTimeout = null;
    this.ws?.close();
    this.ws = null;
    this.isConnected = false;
    this.stopGameTimer();
    this.notify();
  }

  private sendSetup() {
    this.sendJSON({
      action: 'setup',
      home_team: this.homeTeam,
      away_team: this.awayTeam,
    });
    this.isSetup = true;
  }

  // ---- Event Engine ----

  makeEvent(type: string, team: string, value?: number | null, detail?: string | null, groupId?: string): GameEvent {
    return {
      id: uuid(),
      timestamp: Date.now() / 1000,
      type,
      team,
      value: value ?? null,
      quarter: this.quarter,
      detail: detail ?? null,
      groupId: groupId ?? uuid(),
    };
  }

  pushEvent(event: GameEvent) {
    this.events.push(event);
    this.recomputeState();
    this.lastAction = event.detail ?? event.type;
    this.snapshotTimestamp = Date.now() / 1000;
    this.sendSnapshot();
    this.persistLocally();
    this.notify();
  }

  undoLast() {
    const lastSignificant = [...this.events].reverse().find((e) => e.type !== 'time_range');
    if (!lastSignificant) return;
    const gid = lastSignificant.groupId;
    this.events = this.events.filter((e) => e.groupId !== gid);
    this.recomputeState();
    const prev = [...this.events].reverse().find((e) => e.type !== 'time_range');
    this.lastAction = prev ? `Undo → ${prev.detail ?? prev.type}` : '';
    this.snapshotTimestamp = Date.now() / 1000;
    this.sendSnapshot();
    this.persistLocally();
    this.notify();
  }

  undoLastN(count: number) {
    for (let i = 0; i < count; i++) {
      const idx = this.events.map((e) => e.type).lastIndexOf('ft_made') !== -1
        ? (() => {
            for (let j = this.events.length - 1; j >= 0; j--) {
              if (this.events[j].type !== 'time_range') return j;
            }
            return -1;
          })()
        : -1;
      // Simpler: remove last non-time_range event
      for (let j = this.events.length - 1; j >= 0; j--) {
        if (this.events[j].type !== 'time_range') {
          this.events.splice(j, 1);
          break;
        }
      }
    }
    this.recomputeState();
    const prev = [...this.events].reverse().find((e) => e.type !== 'time_range');
    this.lastAction = prev ? `Undo → ${prev.detail ?? prev.type}` : '';
    this.snapshotTimestamp = Date.now() / 1000;
    this.sendSnapshot();
    this.persistLocally();
    this.notify();
  }

  // ---- State Computation ----

  recomputeState() {
    const home = zeroStats();
    const away = zeroStats();
    let currentPossession: string | null = null;
    let currentQuarter = 1;
    let currentTimeRange = '12-9';

    for (const event of this.events) {
      const isHome = event.team === 'home';
      const stats = isHome ? home : away;

      switch (event.type) {
        case 'fg_made': {
          const pts = event.value ?? 2;
          stats.score += pts;
          stats.fgm += 1;
          stats.fga += 1;
          if (pts === 3) { stats.fg3m += 1; stats.fg3a += 1; }
          break;
        }
        case 'fg_miss': {
          const pts = event.value ?? 2;
          stats.fga += 1;
          if (pts === 3) stats.fg3a += 1;
          break;
        }
        case 'ft_made':
          stats.ftm += 1; stats.fta += 1; stats.score += 1;
          break;
        case 'ft_miss':
          stats.fta += 1;
          break;
        case 'steal':
          stats.stl += 1;
          break;
        case 'turnover':
          stats.tov += 1;
          break;
        case 'oreb':
          stats.oreb += 1;
          break;
        case 'dreb':
          stats.dreb += 1;
          break;
        case 'foul': {
          const qKey = String(event.quarter);
          stats.pf += 1;
          stats.period_fouls[qKey] = (stats.period_fouls[qKey] ?? 0) + 1;
          break;
        }
        case 'off_foul': {
          const qKey = String(event.quarter);
          stats.pf += 1;
          stats.tov += 1;
          stats.period_fouls[qKey] = (stats.period_fouls[qKey] ?? 0) + 1;
          break;
        }
        case 'timeout':
          stats.timeouts_used += 1;
          break;
        case 'possession':
          currentPossession = event.team;
          break;
        case 'quarter':
          currentQuarter = event.value ?? currentQuarter;
          break;
        case 'time_range':
          currentTimeRange = event.detail ?? currentTimeRange;
          break;
      }
    }

    this.homeStats = home;
    this.awayStats = away;
    this.possession = currentPossession;
    this.quarter = currentQuarter;
    this.timeRange = currentTimeRange;
  }

  // ---- Timer ----

  startGameTimer() {
    this.stopGameTimer();
    this.timerRunning = true;
    this.notify();
    this.timerInterval = setInterval(() => {
      if (this.timerSeconds > 0) {
        this.timerSeconds -= 1;
        this.snapshotTimestamp = Date.now() / 1000;
        this.sendSnapshot();
        this.persistLocally();
        this.notify();
      } else {
        this.stopGameTimer();
      }
    }, 1000);
  }

  stopGameTimer() {
    if (this.timerInterval) {
      clearInterval(this.timerInterval);
      this.timerInterval = null;
    }
    this.timerRunning = false;
    this.notify();
  }

  toggleGameTimer() {
    if (this.timerRunning) this.stopGameTimer();
    else this.startGameTimer();
  }

  adjustTimer(delta: number) {
    this.timerSeconds = Math.min(300, Math.max(0, this.timerSeconds + delta));
    this.snapshotTimestamp = Date.now() / 1000;
    this.sendSnapshot();
    this.persistLocally();
    this.notify();
  }

  // ---- Snapshot ----

  private buildSnapshot(): GameSnapshot & { action: string } {
    return {
      action: 'snapshot',
      game_id: this.gameId,
      timestamp: this.snapshotTimestamp,
      home_team: this.homeTeam,
      away_team: this.awayTeam,
      possession: this.possession,
      quarter: this.quarter,
      time_range: this.timeRange,
      timer_seconds: this.timerSeconds,
      stopped: this.isStopped,
      home: this.homeStats,
      away: this.awayStats,
      events: this.events,
      last_action: this.lastAction,
    };
  }

  sendSnapshot() {
    const snapshot = this.buildSnapshot();
    this.sendJSON(snapshot);
  }

  sendStop() {
    this.isStopped = true;
    this.sendJSON({ action: 'stop', game_id: this.gameId });
    this.clearLocal();
    this.notify();
  }

  // ---- Network ----

  private sendJSON(obj: any) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  private handleMessage(text: string) {
    let msg: any;
    try { msg = JSON.parse(text); } catch { return; }

    if (msg.stopped) {
      this.isStopped = true;
      this.disconnect();
      this.clearLocal();
      this.notify();
      return;
    }

    const serverTimestamp = msg.timestamp ?? 0;
    if (serverTimestamp > this.snapshotTimestamp && msg.events?.length > 0) {
      this.events = msg.events;
      this.timerSeconds = msg.timer_seconds ?? this.timerSeconds;
      this.recomputeState();
      this.lastAction = msg.last_action ?? '';
      this.snapshotTimestamp = serverTimestamp;
      this.persistLocally();
      this.notify();
    } else if (this.events.length > 0) {
      this.sendSnapshot();
    }
  }

  // ---- Local Persistence (localStorage) ----

  private get persistKey() { return `game_events_${this.gameId}`; }
  private get persistMetaKey() { return `game_meta_${this.gameId}`; }

  persistLocally() {
    try {
      localStorage.setItem(this.persistKey, JSON.stringify(this.events));
      localStorage.setItem(this.persistMetaKey, JSON.stringify({
        quarter: this.quarter,
        timeRange: this.timeRange,
        timerSeconds: this.timerSeconds,
        timestamp: this.snapshotTimestamp,
        lastAction: this.lastAction,
      }));
    } catch {}
  }

  private restoreFromLocal() {
    try {
      const eventsStr = localStorage.getItem(this.persistKey);
      if (!eventsStr) return;
      const restored: GameEvent[] = JSON.parse(eventsStr);
      if (!Array.isArray(restored) || restored.length === 0) return;
      this.events = restored;
      this.recomputeState();

      const metaStr = localStorage.getItem(this.persistMetaKey);
      if (metaStr) {
        const meta = JSON.parse(metaStr);
        this.timerSeconds = meta.timerSeconds ?? 300;
        this.snapshotTimestamp = meta.timestamp ?? 0;
        this.lastAction = meta.lastAction ?? '';
      }
    } catch {}
  }

  private clearLocal() {
    try {
      localStorage.removeItem(this.persistKey);
      localStorage.removeItem(this.persistMetaKey);
    } catch {}
  }
}
