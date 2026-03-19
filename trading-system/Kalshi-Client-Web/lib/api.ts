// Same origin — served from the FastAPI backend
const BASE = '';

export interface KalshiMarket {
  ticker: string;
  status: string;
  yes_sub_title?: string;
  no_sub_title?: string;
}

export interface KalshiEvent {
  event_ticker: string;
  title: string;
  sub_title?: string;
  series_ticker?: string;
  markets?: KalshiMarket[];
}

interface EventsResponse {
  events: KalshiEvent[];
  cursor?: string;
}

export async function fetchNBAGames(): Promise<KalshiEvent[]> {
  const all: KalshiEvent[] = [];
  let cursor: string | undefined;

  do {
    const params = new URLSearchParams({
      series_ticker: 'KXNBAGAME',
      with_nested_markets: 'true',
      limit: '200',
    });
    if (cursor) params.set('cursor', cursor);

    const resp = await fetch(`${BASE}/api/events?${params}`);
    if (!resp.ok) throw new Error(`Failed to fetch games: ${resp.statusText}`);
    const data: EventsResponse = await resp.json();
    all.push(...data.events);
    cursor = data.cursor && data.cursor.length > 0 ? data.cursor : undefined;
  } while (cursor);

  return all.filter(
    (e) => e.markets?.some((m) => m.status === 'active') ?? false
  );
}

export async function checkGameStatus(
  eventTicker: string
): Promise<{ started: boolean }> {
  try {
    const resp = await fetch(`${BASE}/nba/games/${eventTicker}`);
    if (!resp.ok) return { started: false };
    const data = await resp.json();
    return { started: !!data.home_team };
  } catch {
    return { started: false };
  }
}

export function wsUrl(gameId: string): string {
  const loc = typeof window !== 'undefined' ? window.location : null;
  if (loc) {
    const proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${loc.host}/nba/ws/input/${gameId}`;
  }
  return `wss://palisadescapital.co/nba/ws/input/${gameId}`;
}
