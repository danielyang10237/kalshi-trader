'use client';

import { useState, useEffect, useCallback, useMemo, useRef, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import OrderbookLadder from '@/components/OrderbookLadder';
import CandlestickChart from '@/components/CandlestickChart';
import TradesFeed from '@/components/TradesFeed';
import LiveGameScore from '@/components/LiveGameScore';
import MarketBias from '@/components/MarketBias';
import {
  fetchBalance,
  fetchPositions,
  Balance,
  Position,
  placeBuyLimitOrder,
  placeSellLimitOrder,
  placeBuyMarketOrder,
  placeSellMarketOrder,
  fetchOrders,
  RestingOrder,
  cancelMarketOrders,
  initOrderGroup,
  fetchFills,
  fetchCachedFills,
  Fill as ApiFill,
  fetchMarketsByEvent,
  startEngine,
  stopEngine,
  fetchEngineStatus,
  EngineStatus,
  TradingParams,
  TraderState,
  updateTradingParams,
  enableTrading,
  disableTrading,
  setTradingMarkets,
  pushTradingPrices,
} from '@/lib/api';
import Link from 'next/link';

interface UserOrder {
  price: number;
  size: number;
  action: 'buy' | 'sell';
}

interface Fill {
  trade_id: string;
  order_id: string;
  market_ticker: string;
  side: string;
  action: string;
  yes_price: number;
  count: number;
  ts: number;
  is_taker: boolean;
}

interface LimitOrderPanel {
  id: string;
  ticker: string;
  action: 'buy' | 'sell';
  price: number | null;
  quantity: number;
  reduceOnly: boolean;
}

function OrderbookPageContent() {
  const searchParams = useSearchParams();
  const tickerParam = searchParams.get('ticker');
  const seriesParam = searchParams.get('series');
  const eventParam = searchParams.get('event');

  const [marketTicker, setMarketTicker] = useState<string>(tickerParam || '');
  const [seriesTicker, setSeriesTicker] = useState<string>(seriesParam || '');
  const [eventTicker, setEventTicker] = useState<string>(eventParam || '');
  const [activeMarket, setActiveMarket] = useState<string>(tickerParam || '');

  // Dual contract state
  const [secondaryMarket, setSecondaryMarket] = useState<string | null>(null);
  const [siblingMarkets, setSiblingMarkets] = useState<string[]>([]);

  // Balance and positions state
  const [balance, setBalance] = useState<Balance | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [balanceError, setBalanceError] = useState<string | null>(null);

  // Order form state
  const [marketQuantity, setMarketQuantity] = useState<number>(1);
  const [marketReduceOnly, setMarketReduceOnly] = useState<boolean>(false);
  const [marketOrderTicker, setMarketOrderTicker] = useState<string>('');
  const [orderLoading, setOrderLoading] = useState<string | null>(null);
  const [orderMessage, setOrderMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Multiple limit order panels state
  const [limitPanels, setLimitPanels] = useState<LimitOrderPanel[]>([]);
  const [draggedPanelId, setDraggedPanelId] = useState<string | null>(null);

  // User's resting orders and recent fills
  const [primaryUserOrders, setPrimaryUserOrders] = useState<UserOrder[]>([]);
  const [secondaryUserOrders, setSecondaryUserOrders] = useState<UserOrder[]>([]);
  const [recentFills, setRecentFills] = useState<Fill[]>([]);

  // Trade history for position panel
  const [primaryFills, setPrimaryFills] = useState<ApiFill[]>([]);
  const [secondaryFills, setSecondaryFills] = useState<ApiFill[]>([]);
  const [fillsLoading, setFillsLoading] = useState(false);

  // Trading engine state
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null);
  const [engineLoading, setEngineLoading] = useState(false);

  // Trading params state
  const [tradingEnabled, setTradingEnabled] = useState(false);
  const [tradingParams, setTradingParams] = useState<Partial<TradingParams>>({
    min_size: 5,
    max_size: 50,
    max_position: 200,
    max_exposure: 50000,
    fee_rate: 0.07,
    delta_scale: 0.6,
    min_delta: 0.03,
    delta_full_scale: 0.08,
    aggression: 0,
    exit_offset: 0,
  });

  // Best prices from orderbook ladders: {yesBid, yesAsk} per market
  const [primaryBestPrice, setPrimaryBestPrice] = useState<{yesBid: number | null; yesAsk: number | null}>({yesBid: null, yesAsk: null});
  const [secondaryBestPrice, setSecondaryBestPrice] = useState<{yesBid: number | null; yesAsk: number | null}>({yesBid: null, yesAsk: null});

  // Ref to share latest prices with the poll effect
  const latestPricesRef = useRef<{homeBid: number|null; homeAsk: number|null; awayBid: number|null; awayAsk: number|null} | null>(null);

  // Poll engine status when live
  useEffect(() => {
    if (!engineStatus?.is_live || !eventTicker) return;
    const interval = setInterval(async () => {
      try {
        // Push latest market prices to backend
        if (latestPricesRef.current) {
          pushTradingPrices(eventTicker, latestPricesRef.current);
        }
        const status = await fetchEngineStatus(eventTicker) as EngineStatus;
        setEngineStatus(status);
        if (status.trader) {
          setTradingEnabled(status.trader.params.enabled);
        }
      } catch {}
    }, 2000);
    return () => clearInterval(interval);
  }, [engineStatus?.is_live, eventTicker]);

  // Derive home/away best bid & ask from the two orderbook ladders
  // Each market ticker ends with the team abbrev (e.g. -GSW or -CHI)
  // YES on that market = that team wins
  const teamBestPrices = useMemo(() => {
    if (!engineStatus?.is_live) return null;
    const home = engineStatus.home_team;
    const away = engineStatus.away_team;
    if (!home || !away) return null;

    // Figure out which ladder is the home market vs away market
    const primaryIsHome = activeMarket.endsWith(`-${home}`);
    const primaryIsAway = activeMarket.endsWith(`-${away}`);

    const homeBp = primaryIsHome ? primaryBestPrice : primaryIsAway ? secondaryBestPrice : null;
    const awayBp = primaryIsAway ? primaryBestPrice : primaryIsHome ? secondaryBestPrice : null;

    return {
      homeBid: homeBp?.yesBid ?? null,
      homeAsk: homeBp?.yesAsk ?? null,
      awayBid: awayBp?.yesBid ?? null,
      awayAsk: awayBp?.yesAsk ?? null,
    };
  }, [engineStatus, activeMarket, primaryBestPrice, secondaryBestPrice]);

  // Keep ref in sync for the poll effect
  useEffect(() => {
    latestPricesRef.current = teamBestPrices;
  }, [teamBestPrices]);

  const isDualMode = !!secondaryMarket;
  // All active tickers for dropdowns
  const activeTickers = isDualMode ? [activeMarket, secondaryMarket!] : [activeMarket];

  // Contract tint colors for dual mode — primary = blue, secondary = amber
  const PRIMARY_TINT = {
    bg: 'bg-blue-900/20',
    border: 'border-blue-700/40',
    label: 'text-blue-400',
    dot: 'bg-blue-500',
  };
  const SECONDARY_TINT = {
    bg: 'bg-amber-900/20',
    border: 'border-amber-700/40',
    label: 'text-amber-400',
    dot: 'bg-amber-500',
  };
  const tintFor = (ticker: string) =>
    ticker === activeMarket ? PRIMARY_TINT : SECONDARY_TINT;

  // Coalesce fills: group by (action, price), sum count, sort by price desc
  const coalesceFills = (fills: Fill[]) => {
    const map = new Map<string, { action: string; yes_price: number; count: number }>();
    for (const f of fills) {
      const key = `${f.action}-${f.yes_price}`;
      const existing = map.get(key);
      if (existing) {
        existing.count += f.count;
      } else {
        map.set(key, { action: f.action, yes_price: f.yes_price, count: f.count });
      }
    }
    return Array.from(map.values()).sort((a, b) => b.yes_price - a.yes_price);
  };

  useEffect(() => {
    if (tickerParam) {
      setMarketTicker(tickerParam);
      setActiveMarket(tickerParam);

      if (!seriesParam || !eventParam) {
        const parts = tickerParam.split('-');
        if (parts.length >= 2) {
          const extractedSeries = parts[0];
          const extractedEvent = parts.slice(0, 2).join('-');
          if (!seriesParam) setSeriesTicker(extractedSeries);
          if (!eventParam) setEventTicker(extractedEvent);
        }
      }
    }
    if (seriesParam) setSeriesTicker(seriesParam);
    if (eventParam) setEventTicker(eventParam);
  }, [tickerParam, seriesParam, eventParam]);

  // Set default market order ticker when active market changes
  useEffect(() => {
    if (activeMarket) setMarketOrderTicker(activeMarket);
  }, [activeMarket]);

  // Fetch sibling markets when event ticker is available, auto-add secondary
  useEffect(() => {
    if (!eventTicker) return;
    fetchMarketsByEvent(eventTicker)
      .then(data => {
        const tickers = (data.markets || []).map((m: any) => m.ticker as string);
        setSiblingMarkets(tickers);
        // Auto-set secondary market if there's a sibling and none set yet
        if (!secondaryMarket && activeMarket && tickers.length > 1) {
          const sibling = tickers.find(t => t !== activeMarket);
          if (sibling) setSecondaryMarket(sibling);
        }
      })
      .catch(err => console.error('Failed to fetch sibling markets:', err));
  }, [eventTicker]);

  // Pre-create order groups
  useEffect(() => {
    if (!activeMarket) return;
    initOrderGroup(activeMarket).catch(() => {});
  }, [activeMarket]);

  useEffect(() => {
    if (!secondaryMarket) return;
    initOrderGroup(secondaryMarket).catch(() => {});
  }, [secondaryMarket]);

  const refreshBalanceAndPositions = async () => {
    try {
      setBalanceLoading(true);
      setBalanceError(null);
      const [balanceData, positionsData] = await Promise.all([
        fetchBalance(),
        fetchPositions(100, undefined, 'unsettled')
      ]);
      setBalance(balanceData);
      setPositions(positionsData.market_positions || []);
    } catch (err) {
      console.error('Failed to load balance/positions:', err);
      setBalanceError(err instanceof Error ? err.message : 'Failed to load');
    } finally {
      setBalanceLoading(false);
    }
  };

  useEffect(() => {
    refreshBalanceAndPositions();
    const interval = setInterval(refreshBalanceAndPositions, 30000);
    return () => clearInterval(interval);
  }, []);

  // Parse resting orders from API response
  const parseUserOrders = (ordersData: { orders: RestingOrder[] }): UserOrder[] => {
    return (ordersData.orders || []).map((o: RestingOrder) => ({
      price: o.yes_price > 0 ? o.yes_price : (100 - o.no_price),
      size: o.remaining_count,
      action: o.action as 'buy' | 'sell',
    }));
  };

  const refreshUserOrders = async () => {
    if (!activeMarket) {
      setPrimaryUserOrders([]);
      setSecondaryUserOrders([]);
      return;
    }
    try {
      const primaryData = await fetchOrders(activeMarket);
      setPrimaryUserOrders(parseUserOrders(primaryData));

      if (secondaryMarket) {
        const secondaryData = await fetchOrders(secondaryMarket);
        setSecondaryUserOrders(parseUserOrders(secondaryData));
      }
    } catch (err) {
      console.error('Failed to fetch user orders:', err);
    }
  };

  const refreshMarketFills = async () => {
    if (!activeMarket) {
      setPrimaryFills([]);
      setSecondaryFills([]);
      return;
    }
    try {
      setFillsLoading(true);
      const primaryData = await fetchFills(activeMarket, 500);
      setPrimaryFills(primaryData.fills || []);

      if (secondaryMarket) {
        const secondaryData = await fetchFills(secondaryMarket, 500);
        setSecondaryFills(secondaryData.fills || []);
      }
    } catch (err) {
      console.error('Failed to fetch market fills:', err);
    } finally {
      setFillsLoading(false);
    }
  };

  useEffect(() => {
    refreshUserOrders();
    refreshMarketFills();
    const interval = setInterval(refreshUserOrders, 10000);
    return () => clearInterval(interval);
  }, [activeMarket, secondaryMarket]);

  // Load cached fills for active contract(s)
  useEffect(() => {
    if (!activeMarket) {
      setRecentFills([]);
      return;
    }

    const mapFill = (f: any): Fill => ({
      trade_id: f.trade_id,
      order_id: f.order_id,
      market_ticker: f.market_ticker || f.ticker || '',
      side: f.side,
      action: f.action,
      yes_price: f.yes_price,
      count: f.count,
      ts: f.ts || (f.created_time ? new Date(f.created_time).getTime() / 1000 : 0),
      is_taker: f.is_taker,
    });

    const promises = [fetchCachedFills(activeMarket, 50)];
    if (secondaryMarket) promises.push(fetchCachedFills(secondaryMarket, 50));

    Promise.all(promises)
      .then(results => {
        const allFills = results.flatMap(r => (r.fills || []).map(mapFill));
        allFills.sort((a, b) => (b.ts || 0) - (a.ts || 0));
        setRecentFills(allFills.slice(0, 50));
      })
      .catch(err => console.error('Failed to load cached fills:', err));
  }, [activeMarket, secondaryMarket]);

  // Fills WebSocket — only show fills for current contract(s)
  useEffect(() => {
    const wsUrl = 'ws://localhost:8000/ws/fills';
    const ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const parsed = typeof data === 'string' ? JSON.parse(data) : data;
        if (parsed.type === 'fill' && parsed.msg) {
          const fill = parsed.msg as Fill;
          const fillTicker = fill.market_ticker || (fill as any).ticker || '';
          const relevant = fillTicker === activeMarket || fillTicker === secondaryMarket;
          if (relevant) {
            setRecentFills(prev => [fill, ...prev].slice(0, 50));
          }
          refreshUserOrders();
          refreshBalanceAndPositions();
          refreshMarketFills();
        }
      } catch (err) {
        console.error('[Fills WS] Error:', err);
      }
    };

    return () => {
      if (ws.readyState === WebSocket.OPEN) ws.close();
    };
  }, [activeMarket, secondaryMarket]);

  const handleConnect = () => {
    if (marketTicker.trim()) {
      setActiveMarket(marketTicker.trim().toUpperCase());
    }
  };

  const clearMessage = () => setOrderMessage(null);
  const generatePanelId = () => `panel-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

  const addLimitPanel = useCallback((action: 'buy' | 'sell', ticker: string, price: number | null = null) => {
    const newPanel: LimitOrderPanel = {
      id: generatePanelId(),
      ticker,
      action,
      price,
      quantity: 1,
      reduceOnly: false,
    };
    setLimitPanels(prev => [...prev, newPanel]);
  }, []);

  const removeLimitPanel = useCallback((panelId: string) => {
    setLimitPanels(prev => prev.filter(p => p.id !== panelId));
  }, []);

  const updateLimitPanel = useCallback((panelId: string, updates: Partial<LimitOrderPanel>) => {
    setLimitPanels(prev => prev.map(p =>
      p.id === panelId ? { ...p, ...updates } : p
    ));
  }, []);

  // Ladder click handlers — each passes the correct ticker
  const handlePrimaryLadderClick = (price: number, side: 'yes' | 'no') => {
    const action = side === 'yes' ? 'buy' : 'sell';
    addLimitPanel(action, activeMarket, price);
  };

  const handleSecondaryLadderClick = (price: number, side: 'yes' | 'no') => {
    if (!secondaryMarket) return;
    const action = side === 'yes' ? 'buy' : 'sell';
    addLimitPanel(action, secondaryMarket, price);
  };

  // Drag and drop
  const handleDragStart = (panelId: string) => setDraggedPanelId(panelId);
  const handleDragOver = (e: React.DragEvent, targetPanelId: string) => {
    e.preventDefault();
    if (!draggedPanelId || draggedPanelId === targetPanelId) return;
    setLimitPanels(prev => {
      const draggedIdx = prev.findIndex(p => p.id === draggedPanelId);
      const targetIdx = prev.findIndex(p => p.id === targetPanelId);
      if (draggedIdx === -1 || targetIdx === -1) return prev;
      const newPanels = [...prev];
      const [draggedPanel] = newPanels.splice(draggedIdx, 1);
      newPanels.splice(targetIdx, 0, draggedPanel);
      return newPanels;
    });
  };
  const handleDragEnd = () => setDraggedPanelId(null);

  const handleLimitPanelExecute = async (panel: LimitOrderPanel) => {
    if (!panel.ticker || panel.quantity < 1 || !panel.price || panel.price < 1 || panel.price > 99) return;
    setOrderLoading(`limit-${panel.id}`);
    setOrderMessage(null);
    try {
      const request = { ticker: panel.ticker, count: panel.quantity, price: panel.price, reduce_only: panel.reduceOnly };
      const result = panel.action === 'buy'
        ? await placeBuyLimitOrder(request)
        : await placeSellLimitOrder(request);
      setOrderMessage({ type: 'success', text: `${panel.action.toUpperCase()} limit placed on ${panel.ticker.split('-').pop()}! ID: ${result.order.order_id.slice(0, 8)}...` });
      setTimeout(clearMessage, 5000);
      refreshBalanceAndPositions();
      refreshUserOrders();
      refreshMarketFills();
    } catch (err) {
      setOrderMessage({ type: 'error', text: err instanceof Error ? err.message : 'Order failed' });
    } finally {
      setOrderLoading(null);
    }
  };

  const handleMarketOrder = async (action: 'buy' | 'sell') => {
    if (!marketOrderTicker || marketQuantity < 1) return;
    setOrderLoading(`market-${action}`);
    setOrderMessage(null);
    try {
      const request = { ticker: marketOrderTicker, count: marketQuantity, reduce_only: marketReduceOnly };
      const result = action === 'buy'
        ? await placeBuyMarketOrder(request)
        : await placeSellMarketOrder(request);
      setOrderMessage({ type: 'success', text: `${action.toUpperCase()} market filled on ${marketOrderTicker.split('-').pop()}!` });
      setTimeout(clearMessage, 5000);
      refreshBalanceAndPositions();
      refreshMarketFills();
    } catch (err) {
      setOrderMessage({ type: 'error', text: err instanceof Error ? err.message : 'Order failed' });
    } finally {
      setOrderLoading(null);
    }
  };

  const handleCancelOrders = async (ticker: string) => {
    if (!ticker) return;
    setOrderLoading('cancel-orders');
    setOrderMessage(null);
    try {
      await cancelMarketOrders(ticker);
      setOrderMessage({ type: 'success', text: `All orders for ${ticker.split('-').pop()} cancelled!` });
      setTimeout(clearMessage, 5000);
      refreshBalanceAndPositions();
      refreshUserOrders();
    } catch (err) {
      setOrderMessage({ type: 'error', text: err instanceof Error ? err.message : 'Cancel failed' });
    } finally {
      setOrderLoading(null);
    }
  };

  // Short label helper
  const shortLabel = (ticker: string) => ticker.split('-').pop() || ticker;

  // Compute position stats from fills
  const computePositionStats = (fills: ApiFill[], bestBid?: number | null) => {
    let netPosition = 0;
    let cashFlowCents = 0;
    fills.forEach(fill => {
      if (fill.action === 'buy') {
        netPosition += fill.count;
        cashFlowCents -= fill.yes_price * fill.count;
      } else {
        netPosition -= fill.count;
        cashFlowCents += fill.yes_price * fill.count;
      }
    });
    // Mark-to-market: if we liquidated at best bid, what's the P&L?
    // Long position (net > 0): sell at bid → cashFlow + net * bid
    // Short position (net < 0): buy back at ask (not bid) — approximate with bid for now
    let unrealizedCents: number | null = null;
    if (bestBid != null && netPosition !== 0) {
      unrealizedCents = cashFlowCents + (netPosition * bestBid);
    }
    return {
      netPosition,
      gainIfYes: cashFlowCents + (netPosition * 100),
      gainIfNo: cashFlowCents,
      costBasis: -cashFlowCents, // total spent (positive = money out)
      unrealizedCents,
    };
  };

  // Portfolio value = cash + unrealized value of all positions
  const portfolioValue = useMemo(() => {
    if (!balance) return null;
    const cashCents = balance.balance;
    const primaryStats = primaryFills.length > 0
      ? computePositionStats(primaryFills, primaryBestPrice.yesBid)
      : null;
    const secondaryStats = secondaryFills.length > 0
      ? computePositionStats(secondaryFills, secondaryBestPrice.yesBid)
      : null;

    // Sum unrealized: if we have a bid, mark-to-market; otherwise use cost basis (0 unrealized)
    let positionValueCents = 0;
    if (primaryStats) {
      if (primaryStats.unrealizedCents != null) {
        // unrealizedCents = cashFlow + net*bid = what we'd get if we liquidated
        // But cashFlow already accounts for money spent, so unrealizedCents IS the P&L
        // Position market value = net * bid (for long)
        positionValueCents += primaryStats.netPosition > 0
          ? primaryStats.netPosition * (primaryBestPrice.yesBid ?? 0)
          : 0; // short positions are more complex, skip for now
      }
    }
    if (secondaryStats) {
      if (secondaryStats.unrealizedCents != null) {
        positionValueCents += secondaryStats.netPosition > 0
          ? secondaryStats.netPosition * (secondaryBestPrice.yesBid ?? 0)
          : 0;
      }
    }

    return {
      cashCents,
      positionValueCents,
      totalCents: cashCents + positionValueCents,
    };
  }, [balance, primaryFills, secondaryFills, primaryBestPrice.yesBid, secondaryBestPrice.yesBid]);

  const handleEngineToggle = async () => {
    if (!eventTicker) return;
    setEngineLoading(true);
    try {
      if (engineStatus?.is_live) {
        await stopEngine(eventTicker);
        setEngineStatus(null);
      } else {
        const status = await startEngine(eventTicker, activeMarket);
        setEngineStatus(status);
        // Auto-set market tickers for trading
        if (status.is_live && isDualMode && secondaryMarket) {
          const homeTeam = status.home_team;
          const primaryIsHome = activeMarket.endsWith(`-${homeTeam}`);
          const homeTicker = primaryIsHome ? activeMarket : secondaryMarket;
          const awayTicker = primaryIsHome ? secondaryMarket : activeMarket;
          setTradingMarkets(eventTicker, homeTicker, awayTicker).catch(console.error);
        }
      }
    } catch (e) {
      console.error('Engine toggle failed:', e);
    } finally {
      setEngineLoading(false);
    }
  };

  const handleTradingToggle = async () => {
    if (!eventTicker || !engineStatus?.is_live) return;
    try {
      if (tradingEnabled) {
        await disableTrading(eventTicker);
        setTradingEnabled(false);
      } else {
        await enableTrading(eventTicker);
        setTradingEnabled(true);
      }
    } catch (e) {
      console.error('Trading toggle failed:', e);
    }
  };

  const handleParamChange = async (key: keyof TradingParams, value: number | null) => {
    if (!eventTicker || !engineStatus?.is_live) return;
    const updated = { ...tradingParams, [key]: value };
    setTradingParams(updated);
    try {
      await updateTradingParams(eventTicker, { [key]: value });
    } catch (e) {
      console.error('Param update failed:', e);
    }
  };

  const renderEnginePanel = () => (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      <div className="p-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${engineStatus?.is_live ? 'bg-green-400 animate-pulse' : 'bg-gray-600'}`} />
          <span className="text-xs font-semibold">Trading Engine</span>
          {engineStatus?.is_live && (
            <span className="text-[10px] text-gray-400">
              {engineStatus.away_team}@{engineStatus.home_team} | {engineStatus.players_tracked} players | Q: {engineStatus.roster_quality_diff > 0 ? '+' : ''}{engineStatus.roster_quality_diff.toFixed(3)}
            </span>
          )}
        </div>
        <button
          onClick={handleEngineToggle}
          disabled={engineLoading || !eventTicker}
          className={`px-3 py-1 rounded text-xs font-semibold transition-colors ${
            engineStatus?.is_live
              ? 'bg-red-600 hover:bg-red-700 text-white'
              : 'bg-green-600 hover:bg-green-700 text-white'
          } disabled:opacity-50`}
        >
          {engineLoading ? '...' : engineStatus?.is_live ? 'Stop' : 'Start'}
        </button>
      </div>
      {/* Enable Trading switch */}
      {engineStatus?.is_live && (
        <div className="px-2 pb-2 flex items-center justify-between border-t border-gray-700 pt-2">
          <span className="text-[10px] font-semibold text-yellow-400">Enable Trading</span>
          <button
            onClick={handleTradingToggle}
            className={`w-9 h-4 rounded-full transition-colors relative ${tradingEnabled ? 'bg-yellow-500' : 'bg-gray-600'}`}
          >
            <div className={`w-3 h-3 bg-white rounded-full absolute top-0.5 transition-all ${tradingEnabled ? 'left-5' : 'left-0.5'}`} />
          </button>
        </div>
      )}
      {/* Delta-based trading display */}
      {engineStatus?.is_live && engineStatus.trader && (() => {
        const t = engineStatus.trader;
        const mid = (t.home_best_bid != null && t.home_best_ask != null)
          ? ((t.home_best_bid + t.home_best_ask) / 2).toFixed(1)
          : null;
        const delta = t.last_model_delta;
        const expMove = t.last_expected_move;
        const dir = t.last_direction;

        return (
          <div className="px-2 pb-2 border-t border-gray-700 pt-1.5 space-y-2">
            {/* Posteriors */}
            <div>
              <div className="text-[9px] text-gray-500 font-semibold uppercase mb-1">Posteriors</div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">XGBoost Prior</span>
                <span className="font-mono text-white">
                  {engineStatus.prior_home_wp != null ? `${(engineStatus.prior_home_wp * 100).toFixed(1)}¢` : '—'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">Kalshi Prior</span>
                <span className="font-mono text-white">
                  {engineStatus.kalshi_pregame_wp != null ? `${(engineStatus.kalshi_pregame_wp * 100).toFixed(1)}¢` : '—'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-blue-400">Kalshi Posterior</span>
                <span className="font-mono font-bold text-blue-300">
                  {t.last_p_kalshi != null ? `${(t.last_p_kalshi * 100).toFixed(1)}¢` : '—'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-purple-400">Computed Posterior</span>
                <span className="font-mono font-bold text-purple-300">
                  {t.last_p_computed != null ? `${(t.last_p_computed * 100).toFixed(1)}¢` : '—'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">Market Mid</span>
                <span className="font-mono text-gray-300">{mid ?? '—'}¢</span>
              </div>
            </div>

            {/* Delta Signal */}
            <div>
              <div className="text-[9px] text-gray-500 font-semibold uppercase mb-1">Delta Signal</div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">Model Delta</span>
                <span className={`font-mono font-bold ${delta != null && delta > 0 ? 'text-green-400' : delta != null && delta < 0 ? 'text-red-400' : 'text-gray-300'}`}>
                  {delta != null ? `${delta > 0 ? '+' : ''}${(delta * 100).toFixed(2)}¢` : '—'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">Expected Move</span>
                <span className={`font-mono ${expMove != null && expMove > 0 ? 'text-green-400' : expMove != null && expMove < 0 ? 'text-red-400' : 'text-gray-300'}`}>
                  {expMove != null ? `${expMove > 0 ? '+' : ''}${(expMove * 100).toFixed(2)}¢` : '—'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">Fair Value</span>
                <span className="font-mono text-white">
                  {t.last_fair != null ? `${t.last_fair.toFixed(1)}¢` : '—'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">Direction</span>
                <span className={`font-mono font-bold ${dir === 'BUY' ? 'text-green-400' : dir === 'SELL' ? 'text-red-400' : 'text-gray-500'}`}>
                  {dir ?? '—'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">Threshold</span>
                <span className="font-mono text-gray-300">
                  {delta != null ? `|${(Math.abs(delta) * 100).toFixed(2)}¢| ${Math.abs(delta) >= t.params.min_delta ? '>' : '<'} ${(t.params.min_delta * 100).toFixed(1)}¢` : '—'}
                  <span className={`ml-1 ${delta != null && Math.abs(delta) >= t.params.min_delta ? 'text-green-400' : 'text-yellow-400'}`}>
                    {delta != null ? (Math.abs(delta) >= t.params.min_delta ? 'TRADE' : 'SKIP') : ''}
                  </span>
                </span>
              </div>
            </div>

            {/* Order & Position */}
            <div>
              <div className="text-[9px] text-gray-500 font-semibold uppercase mb-1">Order & Position</div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">Entry</span>
                <span className="font-mono text-gray-300">
                  {t.last_order_price != null && t.last_size != null && t.last_size > 0
                    ? <>{dir === 'BUY' ? <span className="text-green-400">BUY</span> : <span className="text-red-400">SELL</span>} {t.last_size}@{t.last_order_price}¢</>
                    : '— (no order)'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">Exit</span>
                <span className="font-mono text-gray-300">
                  {t.last_exit_price != null && t.last_size != null && t.last_size > 0
                    ? <>{dir === 'BUY' ? <span className="text-red-400">SELL</span> : <span className="text-green-400">BUY</span>} {t.last_size}@{t.last_exit_price}¢ <span className="text-gray-500">(GTC)</span></>
                    : '—'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">Position</span>
                <span className={`font-mono ${t.home_position > 0 ? 'text-green-400' : t.home_position < 0 ? 'text-red-400' : 'text-gray-300'}`}>
                  {t.home_position > 0 ? '+' : ''}{t.home_position} contracts
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-400">Exposure</span>
                <span className="font-mono text-gray-300">${(t.total_exposure / 100).toFixed(2)}</span>
              </div>
            </div>

            {/* Last trade */}
            {t.recent_trades.length > 0 && (() => {
              const last = t.recent_trades[t.recent_trades.length - 1];
              return (
                <div className="text-[9px] text-yellow-400 pt-1 border-t border-gray-700">
                  Last: {last.direction} {last.size}@{last.order_price}¢ → exit@{last.exit_price ?? '?'}¢ | delta={last.model_delta > 0 ? '+' : ''}{(last.model_delta * 100).toFixed(2)}¢ {last.paper ? '[PAPER]' : ''}
                </div>
              );
            })()}
          </div>
        );
      })()}
    </div>
  );

  const renderTradingParamsEditor = () => {
    if (!engineStatus?.is_live) return null;
    const paramFields: { key: keyof TradingParams; label: string; suffix: string; step?: number }[] = [
      { key: 'min_size', label: 'Min Size', suffix: ' contracts' },
      { key: 'max_size', label: 'Max Size', suffix: ' contracts' },
      { key: 'max_position', label: 'Max Position', suffix: ' contracts' },
      { key: 'max_exposure', label: 'Max Exposure', suffix: '', step: 100 },
      { key: 'delta_scale', label: 'Delta Scale', suffix: '', step: 0.1 },
      { key: 'min_delta', label: 'Min Delta', suffix: '', step: 0.005 },
      { key: 'delta_full_scale', label: 'Full Scale', suffix: '', step: 0.01 },
      { key: 'aggression', label: 'Entry Aggression', suffix: '¢' },
      { key: 'exit_offset', label: 'Exit Offset', suffix: '¢' },
    ];
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
        <div className="p-2 border-b border-gray-700">
          <span className="text-[10px] font-semibold text-gray-300">Trading Parameters</span>
        </div>
        <div className="p-2 space-y-1.5">
          {paramFields.map(({ key, label, suffix, step }) => (
            <div key={key} className="flex items-center justify-between">
              <span className="text-[10px] text-gray-400">{label}</span>
              <div className="flex items-center gap-1">
                <input
                  type="number"
                  value={key === 'max_exposure' ? ((tradingParams[key] as number) ?? 50000) / 100 : (tradingParams[key] as number) ?? 0}
                  onChange={(e) => {
                    const v = parseFloat(e.target.value) || 0;
                    handleParamChange(key, key === 'max_exposure' ? Math.round(v * 100) : v);
                  }}
                  className="w-16 h-5 bg-gray-900 border border-gray-600 rounded text-center font-mono text-[10px] text-white"
                  step={step || 1}
                />
                <span className="text-[9px] text-gray-500">{key === 'max_exposure' ? '$' : suffix}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  };

  const renderPositionCard = (ticker: string, fills: ApiFill[], bestBid?: number | null) => {
    if (fills.length === 0) return null;
    const stats = computePositionStats(fills, bestBid);
    return (
      <div key={ticker} className="bg-gray-900 rounded p-1.5 text-[10px]">
        <div className="font-bold text-gray-300 mb-1">{shortLabel(ticker)}</div>
        <div className="grid grid-cols-4 gap-1">
          <div>
            <div className="text-gray-500">Net</div>
            <div className={`font-mono font-bold ${stats.netPosition >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {stats.netPosition >= 0 ? '+' : ''}{stats.netPosition}
            </div>
          </div>
          <div>
            <div className="text-gray-500">Unreal P&L</div>
            <div className={`font-mono font-bold ${
              stats.unrealizedCents == null ? 'text-gray-500' :
              stats.unrealizedCents >= 0 ? 'text-green-400' : 'text-red-400'
            }`}>
              {stats.unrealizedCents != null ? `$${(stats.unrealizedCents / 100).toFixed(2)}` : '—'}
            </div>
          </div>
          <div>
            <div className="text-gray-500">If YES</div>
            <div className={`font-mono font-bold ${stats.gainIfYes >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {(stats.gainIfYes / 100).toFixed(2)}
            </div>
          </div>
          <div>
            <div className="text-gray-500">If NO</div>
            <div className={`font-mono font-bold ${stats.gainIfNo >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {(stats.gainIfNo / 100).toFixed(2)}
            </div>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      {/* Navigation */}
      <nav className="border-b border-gray-700 px-4 py-2">
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs transition-colors"
            >
              &larr; Markets
            </Link>
            <h1 className="text-sm font-bold">
              {isDualMode && <span className={`inline-block w-2 h-2 rounded-full ${PRIMARY_TINT.dot} mr-1.5`} />}
              {activeMarket || 'Trading Panel'}
            </h1>
            {isDualMode && (
              <span className="text-sm text-gray-400">
                + <span className={`inline-block w-2 h-2 rounded-full ${SECONDARY_TINT.dot} mr-1`} />
                {shortLabel(secondaryMarket!)}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {isDualMode && (
              <button
                onClick={() => {
                  setSecondaryMarket(null);
                  setSecondaryUserOrders([]);
                  setSecondaryFills([]);
                }}
                className="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs transition-colors"
              >
                Remove {shortLabel(secondaryMarket!)}
              </button>
            )}
            {activeMarket && (
              <button
                onClick={() => {
                  setActiveMarket('');
                  setMarketTicker('');
                  setSeriesTicker('');
                  setEventTicker('');
                  setSecondaryMarket(null);
                  setSiblingMarkets([]);
                }}
                className="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs transition-colors"
              >
                Change Market
              </button>
            )}
          </div>
        </div>
      </nav>

      {!activeMarket ? (
        /* Market Ticker Input */
        <div className="max-w-2xl mx-auto mt-20 p-4">
          <div className="bg-gray-800 rounded-lg p-8 border border-gray-700">
            <h2 className="text-2xl font-bold mb-6">Connect to Market</h2>
            <div className="space-y-4">
              <div>
                <label htmlFor="ticker" className="block text-sm font-medium mb-2">
                  Market Ticker
                </label>
                <input
                  id="ticker"
                  type="text"
                  value={marketTicker}
                  onChange={(e) => setMarketTicker(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && handleConnect()}
                  placeholder="e.g., KXNBAGAME-26FEB19ATLPHI-ATL"
                  className="w-full p-3 bg-gray-700 border border-gray-600 rounded-lg text-white placeholder-gray-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
              </div>
              <button
                onClick={handleConnect}
                disabled={!marketTicker.trim()}
                className="w-full py-3 px-6 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg font-semibold transition-colors"
              >
                Connect
              </button>
            </div>
          </div>
        </div>
      ) : isDualMode ? (
        /* ===== DUAL CONTRACT: 4-Column Layout ===== */
        <div
          className="p-2 grid gap-2"
          style={{ gridTemplateColumns: '3fr 2fr 2fr 3fr' }}
        >
          {/* Col 1: Model State + Market Bias + Candlesticks for both contracts */}
          <div className="flex flex-col gap-2">
            {/* Model State */}
            {engineStatus?.model_features && (
              <div className="bg-gray-800 rounded-lg border border-gray-700 p-2">
                <div className="text-[10px] font-semibold text-cyan-400 mb-1.5">GAM Model State</div>
                <div className="grid grid-cols-4 gap-x-3 gap-y-0.5 text-[9px]">
                  {Object.entries(engineStatus.model_features).map(([key, val]) => {
                    const isHighlight = key === 'pending_ft_signed' || key === 'is_dead_ball';
                    const isNonZero = typeof val === 'number' && val !== 0;
                    return (
                      <div key={key} className="flex justify-between gap-1">
                        <span className="text-gray-500 truncate">{key.replace(/_/g, ' ')}</span>
                        <span className={`font-mono ${
                          isHighlight && isNonZero ? 'text-yellow-400 font-bold' :
                          typeof val === 'number' && val > 0 ? 'text-green-400' :
                          typeof val === 'number' && val < 0 ? 'text-red-400' :
                          'text-gray-300'
                        }`}>
                          {typeof val === 'number' ? (Number.isInteger(val) ? val : val.toFixed(3)) : String(val)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {/* Market Bias */}
            <div className="min-h-[35vh] rounded-lg border overflow-hidden bg-gray-800/30 border-gray-700/50">
              <MarketBias primaryTicker={activeMarket} secondaryTicker={secondaryMarket!} />
            </div>
            {/* Primary Candlestick */}
            <div className={`min-h-[35vh] rounded-lg border overflow-hidden ${PRIMARY_TINT.bg} ${PRIMARY_TINT.border}`}>
              {seriesTicker && eventTicker ? (
                <CandlestickChart
                  seriesTicker={seriesTicker}
                  eventTicker={eventTicker}
                  marketTicker={activeMarket}
                />
              ) : (
                <div className="flex items-center justify-center h-full text-gray-500 text-xs">
                  Candlestick unavailable
                </div>
              )}
            </div>
            {/* Secondary Candlestick */}
            <div className={`min-h-[35vh] rounded-lg border overflow-hidden ${SECONDARY_TINT.bg} ${SECONDARY_TINT.border}`}>
              {seriesTicker && eventTicker ? (
                <CandlestickChart
                  seriesTicker={seriesTicker}
                  eventTicker={eventTicker}
                  marketTicker={secondaryMarket!}
                />
              ) : (
                <div className="flex items-center justify-center h-full text-gray-500 text-xs">
                  Candlestick unavailable
                </div>
              )}
            </div>
          </div>

          {/* Col 2: Live Game Score + Trading Params */}
          <div className="flex flex-col gap-2">
            <div className="bg-gray-800/30 rounded-lg border border-gray-700/50 overflow-hidden">
              <LiveGameScore gameId={eventTicker} priorHomeWp={engineStatus?.prior_home_wp} homeBid={teamBestPrices?.homeBid} homeAsk={teamBestPrices?.homeAsk} awayBid={teamBestPrices?.awayBid} awayAsk={teamBestPrices?.awayAsk} />
            </div>
            {renderTradingParamsEditor()}
          </div>

          {/* Col 3: Two Orderbook Ladders side by side */}
          <div className="flex gap-1 overflow-hidden">
            <div className={`flex-1 rounded-lg border overflow-hidden ${PRIMARY_TINT.bg} ${PRIMARY_TINT.border}`}>
              <OrderbookLadder
                marketTicker={activeMarket}
                onPriceClick={handlePrimaryLadderClick}
                userOrders={primaryUserOrders}
                onBestPriceChange={setPrimaryBestPrice}
              />
            </div>
            <div className={`flex-1 rounded-lg border overflow-hidden ${SECONDARY_TINT.bg} ${SECONDARY_TINT.border}`}>
              <OrderbookLadder
                marketTicker={secondaryMarket!}
                onPriceClick={handleSecondaryLadderClick}
                userOrders={secondaryUserOrders}
                onBestPriceChange={setSecondaryBestPrice}
              />
            </div>
          </div>

          {/* Col 4: Engine + Trade Execution (shared) */}
          <div className="flex flex-col gap-2">
            {renderEnginePanel()}
            <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden flex flex-col flex-1">
            <div className="p-2 border-b border-gray-700">
              <h3 className="font-semibold text-xs">Trade Execution</h3>
            </div>

            {/* Balance + Portfolio Value */}
            <div className="p-2 border-b border-gray-700 bg-gray-900/50 space-y-1">
              {balance ? (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-gray-400">Cash</span>
                    <span className="text-xs font-mono font-bold text-green-400">
                      ${(balance.balance / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </span>
                  </div>
                  {portfolioValue && portfolioValue.positionValueCents > 0 && (
                    <>
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] text-gray-400">Positions</span>
                        <span className="text-xs font-mono text-blue-400">
                          ${(portfolioValue.positionValueCents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-t border-gray-700 pt-1">
                        <span className="text-[10px] text-gray-400">Portfolio</span>
                        <span className="text-sm font-mono font-bold text-white">
                          ${(portfolioValue.totalCents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </span>
                      </div>
                    </>
                  )}
                </>
              ) : (
                <div className="text-[10px] text-gray-500">{balanceLoading ? 'Loading...' : balanceError || 'No data'}</div>
              )}
            </div>

            {/* Positions for both contracts */}
            <div className="p-2 border-b border-gray-700 space-y-1">
              <div className="text-[10px] text-gray-400">Positions</div>
              <div className={`rounded p-0.5 ${PRIMARY_TINT.bg} border ${PRIMARY_TINT.border}`}>
                {renderPositionCard(activeMarket, primaryFills, primaryBestPrice.yesBid)}
              </div>
              <div className={`rounded p-0.5 ${SECONDARY_TINT.bg} border ${SECONDARY_TINT.border}`}>
                {renderPositionCard(secondaryMarket!, secondaryFills, secondaryBestPrice.yesBid)}
              </div>
              {primaryFills.length === 0 && secondaryFills.length === 0 && (
                <div className="text-[10px] text-gray-500 text-center py-1">No trades yet</div>
              )}
            </div>

            {/* Order Form */}
            <div className="flex-1 flex flex-col p-2 overflow-y-auto gap-2">
              {orderMessage && (
                <div className={`p-1.5 rounded text-[10px] ${
                  orderMessage.type === 'success'
                    ? 'bg-green-900/50 border border-green-700 text-green-300'
                    : 'bg-red-900/50 border border-red-700 text-red-300'
                }`}>
                  {orderMessage.text}
                </div>
              )}

              {/* Market Orders */}
              <div className={`p-2 rounded-lg border ${isDualMode ? `${tintFor(marketOrderTicker).bg} ${tintFor(marketOrderTicker).border}` : 'bg-gray-900/50 border-gray-700/50'}`}>
                <div className="text-[10px] font-medium text-yellow-500/80 mb-1.5">Market Orders</div>

                {/* Contract selector */}
                <div className="mb-1.5">
                  <select
                    value={marketOrderTicker}
                    onChange={(e) => setMarketOrderTicker(e.target.value)}
                    className="w-full h-6 bg-gray-800 border border-gray-600 rounded text-[10px] text-white px-1"
                  >
                    {activeTickers.map(t => (
                      <option key={t} value={t}>{shortLabel(t)}</option>
                    ))}
                  </select>
                </div>

                {/* Quantity */}
                <div className="mb-1.5">
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setMarketQuantity(Math.max(1, marketQuantity - 1))}
                      className="w-5 h-5 bg-gray-700 hover:bg-gray-600 rounded text-[10px] font-bold"
                    >&minus;</button>
                    <input
                      type="number"
                      value={marketQuantity}
                      onChange={(e) => setMarketQuantity(Math.max(1, parseInt(e.target.value) || 1))}
                      className="flex-1 h-5 bg-gray-800 border border-gray-600 rounded text-center font-mono text-[10px]"
                      min={1}
                    />
                    <button
                      onClick={() => setMarketQuantity(marketQuantity + 1)}
                      className="w-5 h-5 bg-gray-700 hover:bg-gray-600 rounded text-[10px] font-bold"
                    >+</button>
                  </div>
                </div>

                {/* Reduce Only */}
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-[9px] text-gray-400">Reduce Only</span>
                  <button
                    onClick={() => setMarketReduceOnly(!marketReduceOnly)}
                    className={`w-7 h-3.5 rounded-full transition-colors relative ${marketReduceOnly ? 'bg-yellow-600' : 'bg-gray-600'}`}
                  >
                    <div className={`w-2.5 h-2.5 bg-white rounded-full absolute top-0.5 transition-all ${marketReduceOnly ? 'left-3.5' : 'left-0.5'}`} />
                  </button>
                </div>

                {/* Buy/Sell buttons */}
                <div className="grid grid-cols-2 gap-1 mb-1.5">
                  <button
                    onClick={() => handleMarketOrder('buy')}
                    disabled={orderLoading !== null}
                    className="py-1 bg-green-800/60 hover:bg-green-700/70 disabled:bg-gray-700 disabled:text-gray-500 rounded text-[10px] font-medium transition-colors border border-green-700/30"
                  >
                    {orderLoading === 'market-buy' ? '...' : 'Buy Now'}
                  </button>
                  <button
                    onClick={() => handleMarketOrder('sell')}
                    disabled={orderLoading !== null}
                    className="py-1 bg-red-800/60 hover:bg-red-700/70 disabled:bg-gray-700 disabled:text-gray-500 rounded text-[10px] font-medium transition-colors border border-red-700/30"
                  >
                    {orderLoading === 'market-sell' ? '...' : 'Sell Now'}
                  </button>
                </div>

                {/* Add Limit buttons */}
                <div className="pt-1.5 border-t border-gray-700/50">
                  <div className="text-[9px] text-gray-500 mb-1 text-center">Add Limit Order</div>
                  <div className="grid grid-cols-2 gap-1">
                    <button
                      onClick={() => addLimitPanel('buy', marketOrderTicker)}
                      className="py-1 bg-green-900/40 hover:bg-green-800/60 rounded text-[10px] font-medium transition-colors border border-green-600/30 text-green-400"
                    >
                      + Buy Limit
                    </button>
                    <button
                      onClick={() => addLimitPanel('sell', marketOrderTicker)}
                      className="py-1 bg-red-900/40 hover:bg-red-800/60 rounded text-[10px] font-medium transition-colors border border-red-600/30 text-red-400"
                    >
                      + Sell Limit
                    </button>
                  </div>
                </div>
              </div>

              {/* Limit Order Panels */}
              {limitPanels.map((panel) => {
                const panelTint = isDualMode ? tintFor(panel.ticker) : null;
                return (
                <div
                  key={panel.id}
                  draggable
                  onDragStart={() => handleDragStart(panel.id)}
                  onDragOver={(e) => handleDragOver(e, panel.id)}
                  onDragEnd={handleDragEnd}
                  className={`p-2 rounded-lg border transition-all cursor-move ${
                    panelTint
                      ? `${panelTint.bg} ${panelTint.border}`
                      : panel.action === 'buy'
                        ? 'bg-green-900/30 border-green-600/50'
                        : 'bg-red-900/30 border-red-600/50'
                  } ${draggedPanelId === panel.id ? 'opacity-50 scale-95' : ''}`}
                >
                  {/* Panel Header */}
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="text-[10px] text-gray-500 cursor-grab">::</span>
                      {panelTint && <span className={`w-2 h-2 rounded-full ${panelTint.dot}`} />}
                      <span className={`text-[10px] font-bold ${
                        panel.action === 'buy' ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {panel.action === 'buy' ? 'BUY' : 'SELL'}
                      </span>
                      {/* Contract selector on each panel */}
                      <select
                        value={panel.ticker}
                        onChange={(e) => updateLimitPanel(panel.id, { ticker: e.target.value })}
                        className="h-4 bg-gray-800 border border-gray-600 rounded text-[9px] text-white px-0.5"
                      >
                        {activeTickers.map(t => (
                          <option key={t} value={t}>{shortLabel(t)}</option>
                        ))}
                      </select>
                    </div>
                    <button
                      onClick={() => removeLimitPanel(panel.id)}
                      className="w-4 h-4 flex items-center justify-center text-gray-500 hover:text-red-400 hover:bg-red-900/30 rounded transition-colors text-[10px]"
                    >
                      x
                    </button>
                  </div>

                  {/* Price */}
                  <div className="mb-1">
                    <div className="text-[9px] text-gray-500 mb-0.5">Price</div>
                    <input
                      type="number"
                      value={panel.price || ''}
                      onChange={(e) => updateLimitPanel(panel.id, { price: parseInt(e.target.value) || null })}
                      placeholder="Click ladder"
                      className="w-full h-6 bg-gray-800 border border-gray-600 rounded text-center font-mono text-[10px]"
                      min={1}
                      max={99}
                    />
                  </div>

                  {/* Quantity */}
                  <div className="mb-1">
                    <div className="text-[9px] text-gray-500 mb-0.5">Qty</div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => updateLimitPanel(panel.id, { quantity: Math.max(1, panel.quantity - 1) })}
                        className="w-5 h-5 bg-gray-700 hover:bg-gray-600 rounded text-[10px] font-bold"
                      >&minus;</button>
                      <input
                        type="number"
                        value={panel.quantity}
                        onChange={(e) => updateLimitPanel(panel.id, { quantity: Math.max(1, parseInt(e.target.value) || 1) })}
                        className="flex-1 h-5 bg-gray-800 border border-gray-600 rounded text-center font-mono text-[10px]"
                        min={1}
                      />
                      <button
                        onClick={() => updateLimitPanel(panel.id, { quantity: panel.quantity + 1 })}
                        className="w-5 h-5 bg-gray-700 hover:bg-gray-600 rounded text-[10px] font-bold"
                      >+</button>
                    </div>
                  </div>

                  {/* Reduce Only */}
                  <div className="mb-1.5 flex items-center justify-between">
                    <span className="text-[9px] text-gray-400">Reduce Only</span>
                    <button
                      onClick={() => updateLimitPanel(panel.id, { reduceOnly: !panel.reduceOnly })}
                      className={`w-7 h-3.5 rounded-full transition-colors relative ${panel.reduceOnly ? 'bg-yellow-600' : 'bg-gray-600'}`}
                    >
                      <div className={`w-2.5 h-2.5 bg-white rounded-full absolute top-0.5 transition-all ${panel.reduceOnly ? 'left-3.5' : 'left-0.5'}`} />
                    </button>
                  </div>

                  {/* Execute */}
                  <button
                    onClick={() => handleLimitPanelExecute(panel)}
                    disabled={orderLoading !== null || !panel.price}
                    className={`w-full py-1 rounded font-bold text-[10px] transition-colors ${
                      panel.price
                        ? panel.action === 'buy'
                          ? 'bg-green-600 hover:bg-green-500 text-white'
                          : 'bg-red-600 hover:bg-red-500 text-white'
                        : 'bg-gray-700 text-gray-500 cursor-not-allowed'
                    }`}
                  >
                    {orderLoading === `limit-${panel.id}` ? '...' : `EXECUTE ${panel.action.toUpperCase()}`}
                  </button>

                  {panel.price && (
                    <div className="mt-0.5 text-[9px] text-gray-500 text-center">
                      {shortLabel(panel.ticker)} {panel.action.toUpperCase()} {panel.quantity} @ {panel.price}¢
                    </div>
                  )}
                </div>
                );
              })}

              {/* Recent Fills — two columns, coalesced by price */}
              <div className="p-2 bg-gray-900/80 rounded-lg border border-purple-600/50">
                <div className="text-[10px] font-semibold text-purple-400 mb-1">Recent Fills</div>
                <div className="grid grid-cols-2 gap-1">
                  {/* Primary contract fills */}
                  <div className={`rounded p-1 ${PRIMARY_TINT.bg} border ${PRIMARY_TINT.border}`}>
                    <div className={`text-[9px] font-bold mb-0.5 ${PRIMARY_TINT.label}`}>{shortLabel(activeMarket)}</div>
                    {(() => {
                      const fills = coalesceFills(recentFills.filter(f => f.market_ticker === activeMarket));
                      if (fills.length === 0) return <div className="text-[9px] text-gray-500 text-center py-1">No fills</div>;
                      return (
                        <div className="space-y-0.5 max-h-60 overflow-y-auto">
                          {fills.map((f, idx) => (
                            <div key={idx} className={`text-[10px] p-0.5 rounded ${f.action === 'buy' ? 'bg-green-900/30' : 'bg-red-900/30'}`}>
                              <div className="flex justify-between items-center">
                                <span className={f.action === 'buy' ? 'text-green-400' : 'text-red-400'}>{f.action.toUpperCase()}</span>
                                <span className="font-mono text-gray-300">{f.count} @ {f.yes_price}¢</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      );
                    })()}
                  </div>
                  {/* Secondary contract fills */}
                  <div className={`rounded p-1 ${SECONDARY_TINT.bg} border ${SECONDARY_TINT.border}`}>
                    <div className={`text-[9px] font-bold mb-0.5 ${SECONDARY_TINT.label}`}>{shortLabel(secondaryMarket!)}</div>
                    {(() => {
                      const fills = coalesceFills(recentFills.filter(f => f.market_ticker === secondaryMarket));
                      if (fills.length === 0) return <div className="text-[9px] text-gray-500 text-center py-1">No fills</div>;
                      return (
                        <div className="space-y-0.5 max-h-60 overflow-y-auto">
                          {fills.map((f, idx) => (
                            <div key={idx} className={`text-[10px] p-0.5 rounded ${f.action === 'buy' ? 'bg-green-900/30' : 'bg-red-900/30'}`}>
                              <div className="flex justify-between items-center">
                                <span className={f.action === 'buy' ? 'text-green-400' : 'text-red-400'}>{f.action.toUpperCase()}</span>
                                <span className="font-mono text-gray-300">{f.count} @ {f.yes_price}¢</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      );
                    })()}
                  </div>
                </div>
              </div>

              {/* Cancel Orders */}
              {(primaryUserOrders.length > 0 || secondaryUserOrders.length > 0) && (
                <div className="space-y-1">
                  {primaryUserOrders.length > 0 && (
                    <button
                      onClick={() => handleCancelOrders(activeMarket)}
                      disabled={orderLoading !== null}
                      className="w-full py-1.5 bg-red-900/50 hover:bg-red-800/70 border border-red-600/50 rounded text-red-400 text-[10px] font-medium transition-colors disabled:opacity-50"
                    >
                      {orderLoading === 'cancel-orders' ? '...' : `Cancel ${shortLabel(activeMarket)} Orders (${primaryUserOrders.length})`}
                    </button>
                  )}
                  {secondaryUserOrders.length > 0 && (
                    <button
                      onClick={() => handleCancelOrders(secondaryMarket!)}
                      disabled={orderLoading !== null}
                      className="w-full py-1.5 bg-red-900/50 hover:bg-red-800/70 border border-red-600/50 rounded text-red-400 text-[10px] font-medium transition-colors disabled:opacity-50"
                    >
                      {orderLoading === 'cancel-orders' ? '...' : `Cancel ${shortLabel(secondaryMarket!)} Orders (${secondaryUserOrders.length})`}
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
          </div>
        </div>
      ) : (
        /* ===== SINGLE CONTRACT: 3-Column Layout ===== */
        <div className="p-2 grid grid-cols-3 gap-2">
          {/* Left Column: Orderbook Ladder */}
          <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
            <OrderbookLadder marketTicker={activeMarket} onPriceClick={handlePrimaryLadderClick} userOrders={primaryUserOrders} onBestPriceChange={setPrimaryBestPrice} />
          </div>

          {/* Middle Column: Live Score + Trades + Candlesticks */}
          <div className="flex flex-col gap-2">
            {/* Live Game Score */}
            {eventTicker && (
              <div className="bg-gray-800/30 rounded-lg border border-gray-700/50 overflow-hidden">
                <LiveGameScore gameId={eventTicker} priorHomeWp={engineStatus?.prior_home_wp} homeBid={teamBestPrices?.homeBid} homeAsk={teamBestPrices?.homeAsk} awayBid={teamBestPrices?.awayBid} awayAsk={teamBestPrices?.awayAsk} />
              </div>
            )}
            <div className="min-h-[70vh] bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
              <TradesFeed marketTicker={activeMarket} />
            </div>
            <div className="min-h-[70vh] bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
              {seriesTicker && eventTicker ? (
                <CandlestickChart
                  seriesTicker={seriesTicker}
                  eventTicker={eventTicker}
                  marketTicker={activeMarket}
                />
              ) : (
                <div className="flex items-center justify-center h-full text-gray-400">
                  <div className="text-center p-4">
                    <p className="text-sm">Candlestick chart unavailable</p>
                    <p className="text-xs mt-1 text-gray-500">
                      Missing: {!seriesTicker ? 'series ' : ''}{!eventTicker ? 'event' : ''} ticker
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Right Column: Engine + Trade Execution */}
          <div className="flex flex-col gap-2">
            {renderEnginePanel()}
            <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden flex flex-col flex-1">
            <div className="p-2 border-b border-gray-700">
              <h3 className="font-semibold text-xs">Trade Execution</h3>
            </div>

            {/* Balance + Portfolio */}
            <div className="p-2 border-b border-gray-700 bg-gray-900/50">
              {balance ? (
                <div className="space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <div className="bg-gray-800 rounded p-2">
                      <div className="text-[10px] text-gray-400">Cash</div>
                      <div className="text-base font-mono font-bold text-green-400">
                        ${(balance.balance / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </div>
                    </div>
                    {portfolioValue && portfolioValue.positionValueCents > 0 ? (
                      <div className="bg-gray-800 rounded p-2">
                        <div className="text-[10px] text-gray-400">Portfolio</div>
                        <div className="text-base font-mono font-bold text-white">
                          ${(portfolioValue.totalCents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </div>
                      </div>
                    ) : balance.payout !== undefined ? (
                      <div className="bg-gray-800 rounded p-2">
                        <div className="text-[10px] text-gray-400">Payout</div>
                        <div className="text-base font-mono font-bold text-blue-400">
                          ${(balance.payout / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : (
                <div className="text-xs text-gray-500">{balanceLoading ? 'Loading...' : balanceError || 'No data'}</div>
              )}
            </div>

            {/* Position */}
            <div className="p-2 border-b border-gray-700">
              <div className="text-[10px] text-gray-400 mb-1">Position: {shortLabel(activeMarket)}</div>
              {renderPositionCard(activeMarket, primaryFills, primaryBestPrice.yesBid) || (
                <div className="text-[10px] text-gray-500 text-center py-1">No trades yet</div>
              )}
            </div>

            {/* Order Form */}
            <div className="flex-1 flex flex-col p-2 overflow-y-auto gap-2">
              {orderMessage && (
                <div className={`p-1.5 rounded text-[10px] ${
                  orderMessage.type === 'success'
                    ? 'bg-green-900/50 border border-green-700 text-green-300'
                    : 'bg-red-900/50 border border-red-700 text-red-300'
                }`}>
                  {orderMessage.text}
                </div>
              )}

              {/* Market Orders */}
              <div className="p-2 bg-gray-900/50 rounded-lg border border-gray-700/50">
                <div className="text-[10px] font-medium text-yellow-500/80 mb-1.5">Market Orders</div>
                <div className="mb-1.5">
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setMarketQuantity(Math.max(1, marketQuantity - 1))}
                      className="w-6 h-6 bg-gray-700 hover:bg-gray-600 rounded text-sm font-bold"
                    >&minus;</button>
                    <input
                      type="number"
                      value={marketQuantity}
                      onChange={(e) => setMarketQuantity(Math.max(1, parseInt(e.target.value) || 1))}
                      className="flex-1 h-6 bg-gray-800 border border-gray-600 rounded text-center font-mono text-xs"
                      min={1}
                    />
                    <button
                      onClick={() => setMarketQuantity(marketQuantity + 1)}
                      className="w-6 h-6 bg-gray-700 hover:bg-gray-600 rounded text-sm font-bold"
                    >+</button>
                  </div>
                </div>
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-[9px] text-gray-400">Reduce Only</span>
                  <button
                    onClick={() => setMarketReduceOnly(!marketReduceOnly)}
                    className={`w-8 h-4 rounded-full transition-colors relative ${marketReduceOnly ? 'bg-yellow-600' : 'bg-gray-600'}`}
                  >
                    <div className={`w-3 h-3 bg-white rounded-full absolute top-0.5 transition-all ${marketReduceOnly ? 'left-4' : 'left-0.5'}`} />
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-1.5 mb-2">
                  <button
                    onClick={() => handleMarketOrder('buy')}
                    disabled={orderLoading !== null}
                    className="py-1.5 bg-green-800/60 hover:bg-green-700/70 disabled:bg-gray-700 disabled:text-gray-500 rounded text-xs font-medium transition-colors border border-green-700/30"
                  >
                    {orderLoading === 'market-buy' ? '...' : 'Buy Now'}
                  </button>
                  <button
                    onClick={() => handleMarketOrder('sell')}
                    disabled={orderLoading !== null}
                    className="py-1.5 bg-red-800/60 hover:bg-red-700/70 disabled:bg-gray-700 disabled:text-gray-500 rounded text-xs font-medium transition-colors border border-red-700/30"
                  >
                    {orderLoading === 'market-sell' ? '...' : 'Sell Now'}
                  </button>
                </div>
                <div className="pt-2 border-t border-gray-700/50">
                  <div className="text-[9px] text-gray-500 mb-1.5 text-center">Add Limit Order</div>
                  <div className="grid grid-cols-2 gap-1.5">
                    <button
                      onClick={() => addLimitPanel('buy', activeMarket)}
                      className="py-1.5 bg-green-900/40 hover:bg-green-800/60 rounded text-xs font-medium transition-colors border border-green-600/30 text-green-400"
                    >
                      + Buy Limit
                    </button>
                    <button
                      onClick={() => addLimitPanel('sell', activeMarket)}
                      className="py-1.5 bg-red-900/40 hover:bg-red-800/60 rounded text-xs font-medium transition-colors border border-red-600/30 text-red-400"
                    >
                      + Sell Limit
                    </button>
                  </div>
                </div>
              </div>

              {/* Limit Panels */}
              {limitPanels.map((panel) => (
                <div
                  key={panel.id}
                  draggable
                  onDragStart={() => handleDragStart(panel.id)}
                  onDragOver={(e) => handleDragOver(e, panel.id)}
                  onDragEnd={handleDragEnd}
                  className={`p-2 rounded-lg border transition-all cursor-move ${
                    panel.action === 'buy'
                      ? 'bg-green-900/30 border-green-600/50'
                      : 'bg-red-900/30 border-red-600/50'
                  } ${draggedPanelId === panel.id ? 'opacity-50 scale-95' : ''}`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-gray-500 cursor-grab">::</span>
                      <span className={`text-xs font-bold ${
                        panel.action === 'buy' ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {panel.action === 'buy' ? 'BUY LIMIT' : 'SELL LIMIT'}
                      </span>
                    </div>
                    <button
                      onClick={() => removeLimitPanel(panel.id)}
                      className="w-5 h-5 flex items-center justify-center text-gray-500 hover:text-red-400 hover:bg-red-900/30 rounded transition-colors"
                    >
                      x
                    </button>
                  </div>
                  <div className="mb-2">
                    <div className="text-[9px] text-gray-500 mb-0.5">Price (¢)</div>
                    <input
                      type="number"
                      value={panel.price || ''}
                      onChange={(e) => updateLimitPanel(panel.id, { price: parseInt(e.target.value) || null })}
                      placeholder="Click ladder"
                      className="w-full h-7 bg-gray-800 border border-gray-600 rounded text-center font-mono text-sm"
                      min={1}
                      max={99}
                    />
                  </div>
                  <div className="mb-2">
                    <div className="text-[9px] text-gray-500 mb-0.5">Quantity</div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => updateLimitPanel(panel.id, { quantity: Math.max(1, panel.quantity - 1) })}
                        className="w-6 h-6 bg-gray-700 hover:bg-gray-600 rounded text-sm font-bold"
                      >&minus;</button>
                      <input
                        type="number"
                        value={panel.quantity}
                        onChange={(e) => updateLimitPanel(panel.id, { quantity: Math.max(1, parseInt(e.target.value) || 1) })}
                        className="flex-1 h-6 bg-gray-800 border border-gray-600 rounded text-center font-mono text-xs"
                        min={1}
                      />
                      <button
                        onClick={() => updateLimitPanel(panel.id, { quantity: panel.quantity + 1 })}
                        className="w-6 h-6 bg-gray-700 hover:bg-gray-600 rounded text-sm font-bold"
                      >+</button>
                    </div>
                  </div>
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-[9px] text-gray-400">Reduce Only</span>
                    <button
                      onClick={() => updateLimitPanel(panel.id, { reduceOnly: !panel.reduceOnly })}
                      className={`w-8 h-4 rounded-full transition-colors relative ${panel.reduceOnly ? 'bg-yellow-600' : 'bg-gray-600'}`}
                    >
                      <div className={`w-3 h-3 bg-white rounded-full absolute top-0.5 transition-all ${panel.reduceOnly ? 'left-4' : 'left-0.5'}`} />
                    </button>
                  </div>
                  <button
                    onClick={() => handleLimitPanelExecute(panel)}
                    disabled={orderLoading !== null || !panel.price}
                    className={`w-full py-1.5 rounded font-bold text-xs transition-colors ${
                      panel.price
                        ? panel.action === 'buy'
                          ? 'bg-green-600 hover:bg-green-500 text-white'
                          : 'bg-red-600 hover:bg-red-500 text-white'
                        : 'bg-gray-700 text-gray-500 cursor-not-allowed'
                    }`}
                  >
                    {orderLoading === `limit-${panel.id}` ? '...' : `EXECUTE ${panel.action.toUpperCase()}`}
                  </button>
                  {panel.price && (
                    <div className="mt-1 text-[9px] text-gray-500 text-center">
                      {panel.action.toUpperCase()} {panel.quantity} @ {panel.price}¢
                    </div>
                  )}
                </div>
              ))}

              {/* Recent Fills — coalesced by price */}
              <div className="p-3 bg-gray-900/80 rounded-lg border border-purple-600/50">
                <div className="text-sm font-semibold text-purple-400 mb-2">Recent Fills</div>
                {recentFills.length === 0 ? (
                  <div className="text-xs text-gray-500 text-center py-2">No fills yet</div>
                ) : (
                  <div className="space-y-1 max-h-80 overflow-y-auto">
                    {coalesceFills(recentFills).map((fill, idx) => (
                      <div
                        key={idx}
                        className={`text-xs p-1.5 rounded ${
                          fill.action === 'buy' ? 'bg-green-900/30' : 'bg-red-900/30'
                        }`}
                      >
                        <div className="flex justify-between items-center">
                          <span className={fill.action === 'buy' ? 'text-green-400' : 'text-red-400'}>
                            {fill.action.toUpperCase()}
                          </span>
                          <span className="font-mono text-gray-300">
                            {fill.count} @ {fill.yes_price}¢
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Cancel Orders */}
              {primaryUserOrders.length > 0 && (
                <button
                  onClick={() => handleCancelOrders(activeMarket)}
                  disabled={orderLoading !== null}
                  className="w-full py-2 bg-red-900/50 hover:bg-red-800/70 border border-red-600/50 rounded-lg text-red-400 text-sm font-medium transition-colors disabled:opacity-50"
                >
                  {orderLoading === 'cancel-orders' ? 'Cancelling...' : `Cancel All Orders (${primaryUserOrders.length})`}
                </button>
              )}
            </div>
          </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function OrderbookPage() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <OrderbookPageContent />
    </Suspense>
  );
}
