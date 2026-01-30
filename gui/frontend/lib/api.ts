const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export interface Series {
  ticker: string;
  title: string;
  frequency?: string;
  category?: string;
}

export interface Market {
  ticker: string;
  title: string;
  subtitle?: string;
  status: string;
  yes_bid?: number;
  yes_ask?: number;
  no_bid?: number;
  no_ask?: number;
  last_price?: number;
  volume?: number;
  volume_24h?: number;
  open_interest?: number;
  close_time?: string;
  expiration_time?: string;
  result?: string;
  series_ticker?: string;
  event_ticker?: string;
  liquidity?: number;
  liquidity_dollars?: string;
}

export interface Event {
  event_ticker: string;
  title: string;
  sub_title?: string;
  series_ticker: string;
  category?: string;
  mutually_exclusive?: boolean;
}

export interface SeriesResponse {
  series: Series[];
  cursor?: string;
}

export interface MarketsResponse {
  markets: Market[];
  cursor?: string;
}

export interface EventsResponse {
  events: Event[];
  cursor?: string;
}

export interface SeriesTickersResponse {
  series_tickers: string[];
}

export async function fetchSeries(limit: number = 100, cursor?: string, tags?: string[]): Promise<SeriesResponse> {
  const params = new URLSearchParams({ limit: limit.toString() });
  if (cursor) params.append('cursor', cursor);
  if (tags && tags.length > 0) params.append('tags', tags.join(','));
  
  const response = await fetch(`${API_URL}/api/series?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch series: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchMarkets(
  seriesTicker?: string,
  limit: number = 100,
  cursor?: string,
  status?: string
): Promise<MarketsResponse> {
  const params = new URLSearchParams({ limit: limit.toString() });
  if (seriesTicker) params.append('series_ticker', seriesTicker);
  if (cursor) params.append('cursor', cursor);
  if (status) params.append('status', status);
  
  const response = await fetch(`${API_URL}/api/markets?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch markets: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchMarket(ticker: string): Promise<{ market: Market }> {
  const response = await fetch(`${API_URL}/api/markets/${ticker}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch market: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchConfiguredSeriesTickers(): Promise<SeriesTickersResponse> {
  const response = await fetch(`${API_URL}/api/config/series-tickers`);
  if (!response.ok) {
    throw new Error(`Failed to fetch configured series tickers: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchSeriesTags(): Promise<string[]> {
  const response = await fetch(`${API_URL}/api/config/series-tags`);
  if (!response.ok) {
    throw new Error(`Failed to fetch series tags: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchEvents(
  seriesTicker?: string,
  status?: string,
  limit: number = 100,
  cursor?: string,
  withNestedMarkets: boolean = false
): Promise<EventsResponse> {
  const params = new URLSearchParams({ limit: limit.toString() });
  if (seriesTicker) params.append('series_ticker', seriesTicker);
  if (status) params.append('status', status);
  if (cursor) params.append('cursor', cursor);
  if (withNestedMarkets) params.append('with_nested_markets', 'true');
  
  const response = await fetch(`${API_URL}/api/events?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch events: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchMarketsByEvent(
  eventTicker: string,
  status?: string,
  limit: number = 100,
  cursor?: string
): Promise<MarketsResponse> {
  const params = new URLSearchParams({ limit: limit.toString() });
  if (status) params.append('status', status);
  if (cursor) params.append('cursor', cursor);
  
  const response = await fetch(`${API_URL}/api/events/${eventTicker}/markets?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch markets for event: ${response.statusText}`);
  }
  return response.json();
}

export async function searchSeries(query: string, limit: number = 50): Promise<SeriesResponse> {
  // Use the existing series endpoint - we'll filter client-side for now
  // Could add server-side search later
  const params = new URLSearchParams({ limit: limit.toString() });
  const response = await fetch(`${API_URL}/api/series?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to search series: ${response.statusText}`);
  }
  return response.json();
}

export async function addSeriesTicker(ticker: string): Promise<{ success: boolean; message: string; tickers: string[] }> {
  const response = await fetch(`${API_URL}/api/config/series-tickers`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ ticker }),
  });
  if (!response.ok) {
    throw new Error(`Failed to add series ticker: ${response.statusText}`);
  }
  return response.json();
}

export interface Balance {
  balance: number;
  payout?: number;
}

export interface Position {
  ticker: string;
  market_exposure: number;
  position: number;
  resting_orders_count: number;
  total_traded: number;
  realized_pnl: number;
  fees_paid: number;
}

export interface PositionsResponse {
  market_positions: Position[];
  cursor?: string;
}

export async function fetchBalance(): Promise<Balance> {
  const response = await fetch(`${API_URL}/api/trading/balance`);
  if (!response.ok) {
    throw new Error(`Failed to fetch balance: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchPositions(
  limit: number = 100,
  cursor?: string,
  settlementStatus?: string
): Promise<PositionsResponse> {
  const params = new URLSearchParams({ limit: limit.toString() });
  if (cursor) params.append('cursor', cursor);
  if (settlementStatus) params.append('settlement_status', settlementStatus);
  
  const response = await fetch(`${API_URL}/api/trading/positions?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch positions: ${response.statusText}`);
  }
  return response.json();
}

// =============================================================================
// Trading - Orders
// =============================================================================

export interface RestingOrder {
  order_id: string;
  ticker: string;
  side: 'yes' | 'no';
  action: 'buy' | 'sell';
  type: 'limit' | 'market';
  status: string;
  yes_price: number;
  no_price: number;
  remaining_count: number;
  initial_count: number;
  created_time: string;
}

export interface OrdersResponse {
  orders: RestingOrder[];
  cursor?: string;
}

export async function fetchOrders(ticker?: string, limit: number = 100): Promise<OrdersResponse> {
  const params = new URLSearchParams({ limit: limit.toString() });
  if (ticker) params.append('ticker', ticker);
  
  const response = await fetch(`${API_URL}/api/trading/orders?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch orders: ${response.statusText}`);
  }
  return response.json();
}

export async function cancelMarketOrders(ticker: string): Promise<{ success: boolean; deleted_group?: string }> {
  const response = await fetch(`${API_URL}/api/trading/orders/market/${ticker}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to cancel orders: ${error}`);
  }
  return response.json();
}

export async function initOrderGroup(ticker: string): Promise<{ success: boolean; order_group_id: string }> {
  const response = await fetch(`${API_URL}/api/trading/orders/init-group/${ticker}`, {
    method: 'POST',
  });
  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to init order group: ${error}`);
  }
  return response.json();
}

// =============================================================================
// Trading - Order Placement
// =============================================================================

export interface LimitOrderRequest {
  ticker: string;
  count: number;
  price: number;
  reduce_only?: boolean;
  time_in_force?: 'fill_or_kill' | 'good_till_canceled' | 'immediate_or_cancel';
}

export interface MarketOrderRequest {
  ticker: string;
  count: number;
  reduce_only?: boolean;
}

export interface OrderResponse {
  order: {
    order_id: string;
    ticker: string;
    status: string;
    side: string;
    action: string;
    type: string;
    count: number;
    yes_price?: number;
    created_time: string;
  };
}

export async function placeBuyLimitOrder(req: LimitOrderRequest): Promise<OrderResponse> {
  const response = await fetch(`${API_URL}/api/trading/orders/buy/limit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to place buy limit order: ${error}`);
  }
  return response.json();
}

export async function placeSellLimitOrder(req: LimitOrderRequest): Promise<OrderResponse> {
  const response = await fetch(`${API_URL}/api/trading/orders/sell/limit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to place sell limit order: ${error}`);
  }
  return response.json();
}

export async function placeBuyMarketOrder(req: MarketOrderRequest): Promise<OrderResponse> {
  const response = await fetch(`${API_URL}/api/trading/orders/buy/market`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to place buy market order: ${error}`);
  }
  return response.json();
}

export async function placeSellMarketOrder(req: MarketOrderRequest): Promise<OrderResponse> {
  const response = await fetch(`${API_URL}/api/trading/orders/sell/market`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to place sell market order: ${error}`);
  }
  return response.json();
}

// =============================================================================
// Trading - Fills
// =============================================================================

export interface Fill {
  trade_id: string;
  order_id: string;
  ticker: string;
  side: 'yes' | 'no';
  action: 'buy' | 'sell';
  count: number;
  yes_price: number;
  no_price: number;
  is_taker: boolean;
  created_time: string;
}

export interface FillsResponse {
  fills: Fill[];
  cursor?: string;
}

export async function fetchFills(ticker?: string, limit: number = 100): Promise<FillsResponse> {
  const params = new URLSearchParams({ limit: limit.toString() });
  if (ticker) params.append('ticker', ticker);
  
  const response = await fetch(`${API_URL}/api/trading/fills?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch fills: ${response.statusText}`);
  }
  return response.json();
}
