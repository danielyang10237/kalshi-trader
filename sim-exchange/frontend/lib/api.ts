const SIM_URL = process.env.NEXT_PUBLIC_SIM_URL || "http://localhost:9000";

// =============================================================================
// Types
// =============================================================================

export interface BookLevel {
  price: number;
  size: number;
}

export interface BookSide {
  yes: [number, number][];
  no: [number, number][];
  market_ticker: string;
}

export interface BookState {
  home_ticker: string;
  away_ticker: string;
  home: BookSide;
  away: BookSide;
  home_best_ask: number | null;
  home_best_bid: number | null;
  away_best_ask: number | null;
  away_best_bid: number | null;
  total_bids: number;
  total_asks: number;
  seq: number;
}

export interface AccountState {
  balance: number;
  initial_balance: number;
  positions: Record<string, number>;
  fills: Fill[];
  resting_orders: number;
}

export interface Fill {
  trade_id: string;
  ticker: string;
  action: string;
  side: string;
  count: number;
  yes_price: number;
  ts: number;
}

export interface SimConfig {
  home_ticker: string;
  away_ticker: string;
  initial_balance: number;
  port: number;
}

export interface OrderLog {
  order_id: string;
  client_order_id: string;
  ticker: string;
  action: string;
  price: number;
  count: number;
  remaining: number;
  status: string;
  time_in_force: string;
  order_type: string;
  is_mm: boolean;
  created_at: number;
  fills: Fill[];
}

export interface ReplayStatus {
  playing: boolean;
  paused: boolean;
  speed: number;
  progress_pct: number;
  current_index: number;
  total_events: number;
  events_played: number;
  metadata: Record<string, string>;
  spread: number;
  levels: number;
  volume_mult: number;
}

// =============================================================================
// Admin API (sim-specific endpoints)
// =============================================================================

async function simFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${SIM_URL}/sim${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  return res.json();
}

export async function getBook(): Promise<BookState> {
  return simFetch("/book");
}

export async function seedBook(
  midpoint: number,
  spread: number,
  depth: number,
  levels: number,
): Promise<{ success: boolean; book: BookState }> {
  return simFetch("/book/seed", {
    method: "POST",
    body: JSON.stringify({ midpoint, spread, depth, levels }),
  });
}

export async function clearBook(): Promise<{ success: boolean }> {
  return simFetch("/book/clear", { method: "POST" });
}

export async function getAccount(): Promise<AccountState> {
  return simFetch("/account");
}

export async function resetAccount(): Promise<{ success: boolean }> {
  return simFetch("/account/reset", { method: "POST" });
}

export async function getConfig(): Promise<SimConfig> {
  return simFetch("/config");
}

export async function setConfig(
  config: Partial<SimConfig>,
): Promise<{ success: boolean }> {
  return simFetch("/config", {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export async function getTrades(
  limit: number = 100,
): Promise<{ orders: OrderLog[] }> {
  return simFetch(`/trades?limit=${limit}`);
}

// Replay
export async function loadReplay(
  filepath: string,
): Promise<{ success: boolean; metadata?: Record<string, string>; total_events?: number; error?: string }> {
  return simFetch("/replay/load", {
    method: "POST",
    body: JSON.stringify({ filepath }),
  });
}

export async function startReplay(
  speed: number = 1.0,
): Promise<{ success: boolean }> {
  return simFetch("/replay/start", {
    method: "POST",
    body: JSON.stringify({ speed }),
  });
}

export async function pauseReplay(): Promise<{ success: boolean }> {
  return simFetch("/replay/pause", { method: "POST" });
}

export async function stopReplay(): Promise<{ success: boolean }> {
  return simFetch("/replay/stop", { method: "POST" });
}

export async function getReplayStatus(): Promise<ReplayStatus> {
  return simFetch("/replay/status");
}

export async function seekReplay(
  index: number,
): Promise<{ success: boolean; index?: number }> {
  return simFetch("/replay/seek", {
    method: "POST",
    body: JSON.stringify({ index }),
  });
}

export async function setReplayParams(
  params: { spread?: number; levels?: number; volume_mult?: number },
): Promise<{ success: boolean }> {
  return simFetch("/replay/params", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function setReplaySpeed(
  speed: number,
): Promise<{ success: boolean }> {
  return simFetch("/replay/speed", {
    method: "POST",
    body: JSON.stringify({ speed }),
  });
}

// Games catalog
export interface HistoricalGame {
  game_id: string;
  away_team: string;
  home_team: string;
  date: string;
  home_ticker: string;
  away_ticker: string;
  note: string;
  kalshi_file: string;
}

export async function listGames(): Promise<{ games: HistoricalGame[] }> {
  return simFetch("/games");
}

export async function selectGame(
  game_id: string,
): Promise<{ success: boolean; game?: HistoricalGame; replay_loaded?: boolean; total_events?: number; error?: string }> {
  return simFetch("/games/select", {
    method: "POST",
    body: JSON.stringify({ game_id }),
  });
}

// Play-by-play
export interface PBPPlay {
  text: string;
  away_score: number;
  home_score: number;
  period: string;
  clock: string;
  scoring: boolean;
}

export interface PBPResponse {
  plays: PBPPlay[];
  score: {
    away_score: number;
    home_score: number;
    period: string;
    clock: string;
  };
}

export async function getPBP(limit: number = 20): Promise<PBPResponse> {
  return simFetch(`/pbp?limit=${limit}`);
}

// Health check
export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${SIM_URL}/`);
    return res.ok;
  } catch {
    return false;
  }
}
