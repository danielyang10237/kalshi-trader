'use client';

import { useState, useEffect } from 'react';
import {
  fetchSeries,
  fetchMarkets,
  fetchConfiguredSeriesTickers,
  fetchTradingEvents,
  fetchEvents,
  fetchMarketsByEvent,
  fetchSeriesTags,
  addSeriesTicker,
  clearCachedFills,
  deployNbaModels,
  Series,
  Market,
  Event
} from '@/lib/api';

export default function Home() {
  // By Event state
  const [tradingEvents, setTradingEvents] = useState<Record<string, string[]>>({});
  const [selectedCategory, setSelectedCategory] = useState<string>('');
  const [eventSeriesTicker, setEventSeriesTicker] = useState<string>('');
  const [events, setEvents] = useState<Event[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<string>('');
  const [eventMarkets, setEventMarkets] = useState<Market[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventMarketsLoading, setEventMarketsLoading] = useState(false);

  // All Active state
  const [allActiveMarkets, setAllActiveMarkets] = useState<Market[]>([]);
  const [selectedActiveMarket, setSelectedActiveMarket] = useState<string>('');
  const [allActiveLoading, setAllActiveLoading] = useState(false);
  const [activeSearchTerm, setActiveSearchTerm] = useState('');

  // By Series state
  const [series, setSeries] = useState<Series[]>([]);
  const [selectedSeries, setSelectedSeries] = useState<string>('');
  const [seriesMarkets, setSeriesMarkets] = useState<Market[]>([]);
  const [selectedSeriesMarket, setSelectedSeriesMarket] = useState<string>('');
  const [seriesMarketsLoading, setSeriesMarketsLoading] = useState(false);
  const [availableTags, setAvailableTags] = useState<string[]>([]);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [seriesLoading, setSeriesLoading] = useState(false);

  // Series search state
  const [seriesSearchTerm, setSeriesSearchTerm] = useState('');
  const [addingTicker, setAddingTicker] = useState(false);

  // NBA deploy state
  const [deployingModels, setDeployingModels] = useState(false);
  const [deployResult, setDeployResult] = useState<{ copied: string[]; errors: string[] } | null>(null);

  // Shared state
  const [configuredSeriesTickers, setConfiguredSeriesTickers] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load initial data
  useEffect(() => {
    const loadInitialData = async () => {
      try {
        setLoading(true);
        const [seriesData, configData, tradingEventsData, tagsData] = await Promise.all([
          fetchSeries(200),
          fetchConfiguredSeriesTickers(),
          fetchTradingEvents(),
          fetchSeriesTags()
        ]);
        setSeries(seriesData.series || []);
        setConfiguredSeriesTickers(configData.series_tickers || []);
        setTradingEvents(tradingEventsData.trading_events || {});
        setAvailableTags(tagsData || []);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load initial data');
      } finally {
        setLoading(false);
      }
    };
    loadInitialData();
  }, []);

  // Refetch series when tags change
  useEffect(() => {
    const loadSeriesWithTags = async () => {
      try {
        setSeriesLoading(true);
        const seriesData = await fetchSeries(200, undefined, selectedTags.length > 0 ? selectedTags : undefined);
        setSeries(seriesData.series || []);
        setSelectedSeries('');
        setSeriesMarkets([]);
      } catch (err) {
        console.error('Failed to load series with tags:', err);
      } finally {
        setSeriesLoading(false);
      }
    };
    if (selectedTags.length > 0) {
      loadSeriesWithTags();
    }
  }, [selectedTags]);

  // Load all active markets on mount
  useEffect(() => {
    const loadAllActive = async () => {
      try {
        setAllActiveLoading(true);
        const data = await fetchMarkets(undefined, 200, undefined, 'open');
        setAllActiveMarkets(data.markets || []);
      } catch (err) {
        console.error('Failed to load active markets:', err);
      } finally {
        setAllActiveLoading(false);
      }
    };
    loadAllActive();
  }, []);

  // Load events when series ticker selected
  useEffect(() => {
    if (!eventSeriesTicker) {
      setEvents([]);
      return;
    }
    const loadEvents = async () => {
      try {
        setEventsLoading(true);
        const data = await fetchEvents(eventSeriesTicker, 'open', 200);
        setEvents(data.events || []);
      } catch (err) {
        console.error('Failed to load events:', err);
      } finally {
        setEventsLoading(false);
      }
    };
    loadEvents();
  }, [eventSeriesTicker]);

  // Load markets for selected event
  useEffect(() => {
    if (!selectedEvent) {
      setEventMarkets([]);
      return;
    }
    const loadMarkets = async () => {
      try {
        setEventMarketsLoading(true);
        const data = await fetchMarketsByEvent(selectedEvent, 'open', 200);
        setEventMarkets(data.markets || []);
      } catch (err) {
        console.error('Failed to load event markets:', err);
      } finally {
        setEventMarketsLoading(false);
      }
    };
    loadMarkets();
  }, [selectedEvent]);

  // Load markets for selected series
  useEffect(() => {
    if (!selectedSeries) {
      setSeriesMarkets([]);
      return;
    }
    const loadMarkets = async () => {
      try {
        setSeriesMarketsLoading(true);
        const data = await fetchMarkets(selectedSeries, 200, undefined, 'open');
        setSeriesMarkets(data.markets || []);
      } catch (err) {
        console.error('Failed to load series markets:', err);
      } finally {
        setSeriesMarketsLoading(false);
      }
    };
    loadMarkets();
  }, [selectedSeries]);

  // Filter active markets by search
  const filteredActiveMarkets = allActiveMarkets.filter((market) => {
    if (!activeSearchTerm) return true;
    const term = activeSearchTerm.toLowerCase();
    return market.title.toLowerCase().includes(term) ||
           market.ticker.toLowerCase().includes(term) ||
           market.subtitle?.toLowerCase().includes(term);
  });

  // Filter series by search term (searches within tag-filtered series)
  const filteredSeriesForSearch = series.filter((s) => {
    if (!seriesSearchTerm) return false; // Don't show anything if no search term
    const term = seriesSearchTerm.toLowerCase();
    return s.title.toLowerCase().includes(term) ||
           s.ticker.toLowerCase().includes(term);
  });

  // Handler to add series ticker to the list
  const handleAddSeriesTicker = async (ticker: string) => {
    try {
      setAddingTicker(true);
      const result = await addSeriesTicker(ticker);
      if (result.success) {
        setConfiguredSeriesTickers(result.tickers);
        setSeriesSearchTerm(''); // Clear search after adding
      }
    } catch (err) {
      console.error('Failed to add series ticker:', err);
      alert(err instanceof Error ? err.message : 'Failed to add series ticker');
    } finally {
      setAddingTicker(false);
    }
  };

  const navigateToOrderbook = (ticker: string, seriesTicker?: string, eventTicker?: string) => {
    let url = `/orderbook?ticker=${ticker}`;
    if (seriesTicker) url += `&series=${seriesTicker}`;
    if (eventTicker) url += `&event=${eventTicker}`;
    window.open(url, '_blank');
  };

  const MarketCard = ({ market, seriesTicker, eventTicker }: { market: Market; seriesTicker?: string; eventTicker?: string }) => (
    <div className="p-3 bg-gray-50 dark:bg-gray-700 rounded-lg">
      <div className="font-medium text-gray-900 dark:text-white text-sm mb-1 line-clamp-2">
        {market.title}
      </div>
      <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">{market.ticker}</div>
      <div className="flex justify-between items-center mb-2">
        <div className="text-xs">
          {market.last_price !== undefined && (
            <span className="text-green-600 dark:text-green-400 font-medium">
              {(market.last_price * 100).toFixed(0)}¢
            </span>
          )}
        </div>
        {market.liquidity !== undefined && (
          <div className="text-xs text-blue-600 dark:text-blue-400">
            💧 {market.liquidity.toLocaleString()}
            {market.liquidity_dollars && (
              <span className="text-gray-500 dark:text-gray-400 ml-1">
                (${parseFloat(market.liquidity_dollars).toFixed(0)})
              </span>
            )}
          </div>
        )}
      </div>
      <button
        onClick={() => navigateToOrderbook(market.ticker, seriesTicker || market.series_ticker, eventTicker || market.event_ticker)}
        className="w-full px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs rounded font-medium transition-colors"
      >
        Trade →
      </button>
    </div>
  );

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-100 dark:bg-gray-900">
        <div className="text-gray-600 dark:text-gray-400">Loading...</div>
      </div>
    );
  }

  return (
    <div className="h-screen overflow-hidden bg-gray-100 dark:bg-gray-900 p-4 flex flex-col">
      {error && (
        <div className="mb-4 p-3 bg-red-100 border border-red-400 text-red-700 rounded text-sm">
          {error}
        </div>
      )}

      <div className="flex justify-end mb-2">
        <button
          onClick={async () => {
            if (!confirm('Clear all cached fills?')) return;
            try {
              await clearCachedFills();
            } catch (err) {
              alert(err instanceof Error ? err.message : 'Failed to clear fills');
            }
          }}
          className="px-3 py-1.5 bg-red-600 hover:bg-red-700 text-white text-xs rounded font-medium transition-colors"
        >
          Clear Fills Cache
        </button>
      </div>

      <div className="grid grid-cols-3 gap-4 flex-1 min-h-0">
        {/* Column 1: By Event */}
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow overflow-hidden flex flex-col">
          <div className="p-4 border-b border-gray-200 dark:border-gray-700 bg-blue-50 dark:bg-blue-900/20">
            <h2 className="text-lg font-bold text-gray-900 dark:text-white">By Event</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Event → Series → Market</p>
          </div>

          <div className="p-4 space-y-4 overflow-y-auto flex-1">
            {/* Event Category Select */}
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                1. Event
              </label>
              <select
                value={selectedCategory}
                onChange={(e) => {
                  setSelectedCategory(e.target.value);
                  setEventSeriesTicker('');
                  setSelectedEvent('');
                  setEvents([]);
                  setEventMarkets([]);
                }}
                className="w-full p-2 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              >
                <option value="">-- Select event --</option>
                {Object.keys(tradingEvents).map((category) => (
                  <option key={category} value={category}>{category.toUpperCase()}</option>
                ))}
              </select>
            </div>

            {/* Event Panel */}
            {selectedCategory && tradingEvents[selectedCategory] && (
              <div className="border border-gray-200 dark:border-gray-600 rounded-lg p-3 space-y-3">
                <h3 className="text-sm font-bold text-gray-900 dark:text-white">{selectedCategory.toUpperCase()}</h3>

                {/* NBA-specific: Deploy Models */}
                {selectedCategory === 'nba' && (
                  <div>
                    <button
                      onClick={async () => {
                        setDeployingModels(true);
                        setDeployResult(null);
                        try {
                          const result = await deployNbaModels();
                          setDeployResult({ copied: result.copied, errors: result.errors });
                        } catch (err) {
                          setDeployResult({ copied: [], errors: [err instanceof Error ? err.message : 'Deploy failed'] });
                        } finally {
                          setDeployingModels(false);
                        }
                      }}
                      disabled={deployingModels}
                      className="w-full px-3 py-2 bg-orange-600 hover:bg-orange-700 disabled:bg-orange-400 text-white text-xs rounded font-medium transition-colors"
                    >
                      {deployingModels ? 'Deploying...' : 'Deploy Latest Models'}
                    </button>
                    {deployResult && (
                      <div className={`mt-1 text-xs ${deployResult.errors.length > 0 && deployResult.copied.length === 0 ? 'text-red-500' : 'text-green-600 dark:text-green-400'}`}>
                        {deployResult.copied.length > 0 && <div>Deployed {deployResult.copied.length} files</div>}
                        {deployResult.errors.map((e, i) => <div key={i} className="text-red-500">{e}</div>)}
                      </div>
                    )}
                  </div>
                )}

                {/* Series Ticker Select */}
                <div>
                  <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                    Series Ticker
                  </label>
                  <select
                    value={eventSeriesTicker}
                    onChange={(e) => {
                      setEventSeriesTicker(e.target.value);
                      setSelectedEvent('');
                      setEventMarkets([]);
                    }}
                    className="w-full p-2 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                  >
                    <option value="">-- Select series --</option>
                    {tradingEvents[selectedCategory].map((ticker) => (
                      <option key={ticker} value={ticker}>{ticker}</option>
                    ))}
                  </select>
                </div>

                {/* Event Select */}
                {eventSeriesTicker && (
                  <div>
                    <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                      Game / Event
                    </label>
                    {eventsLoading ? (
                      <div className="text-sm text-gray-500">Loading...</div>
                    ) : events.length === 0 ? (
                      <div className="text-sm text-gray-500">No events found</div>
                    ) : (
                      <select
                        value={selectedEvent}
                        onChange={(e) => {
                          setSelectedEvent(e.target.value);
                        }}
                        className="w-full p-2 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                      >
                        <option value="">-- Select --</option>
                        {events.map((event) => (
                          <option key={event.event_ticker} value={event.event_ticker}>
                            {event.title} {event.sub_title && `- ${event.sub_title}`}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                )}

                {/* Trade Button */}
                {selectedEvent && (
                  <div>
                    {eventMarketsLoading ? (
                      <div className="text-sm text-gray-500">Loading markets...</div>
                    ) : eventMarkets.length === 0 ? (
                      <div className="text-sm text-gray-500">No markets found</div>
                    ) : (
                      <button
                        onClick={() => navigateToOrderbook(eventMarkets[0].ticker, eventSeriesTicker, selectedEvent)}
                        className="w-full px-4 py-3 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg font-medium transition-colors"
                      >
                        Trade ({eventMarkets.length} contracts) →
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Column 2: All Active Markets */}
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow overflow-hidden flex flex-col">
          <div className="p-4 border-b border-gray-200 dark:border-gray-700 bg-green-50 dark:bg-green-900/20">
            <h2 className="text-lg font-bold text-gray-900 dark:text-white">All Active Markets</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Browse all open markets</p>
          </div>
          
          <div className="p-4 space-y-4 overflow-y-auto flex-1">
            {/* Search */}
            <input
              type="text"
              placeholder="Search markets..."
              value={activeSearchTerm}
              onChange={(e) => setActiveSearchTerm(e.target.value)}
              className="w-full p-2 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            />

            {/* Markets List */}
            {allActiveLoading ? (
              <div className="text-sm text-gray-500">Loading...</div>
            ) : (
              <>
                <div className="text-xs text-gray-500">
                  {filteredActiveMarkets.length} of {allActiveMarkets.length} markets
                </div>
                <div className="space-y-2">
                  {filteredActiveMarkets.slice(0, 50).map((market) => (
                    <MarketCard key={market.ticker} market={market} />
                  ))}
                  {filteredActiveMarkets.length > 50 && (
                    <div className="text-xs text-gray-500 text-center py-2">
                      Showing first 50 results...
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>

        {/* Column 3: By Series */}
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow overflow-hidden flex flex-col">
          <div className="p-4 border-b border-gray-200 dark:border-gray-700 bg-purple-50 dark:bg-purple-900/20">
            <h2 className="text-lg font-bold text-gray-900 dark:text-white">By Series</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Tags → Series → Markets</p>
          </div>
          
          <div className="p-4 space-y-4 overflow-y-auto flex-1">
            {/* Tags Filter */}
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                1. Filter by Tags
              </label>
              <div className="flex flex-wrap gap-1 mb-2 max-h-24 overflow-y-auto">
                {selectedTags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex items-center gap-1 px-2 py-0.5 bg-purple-100 dark:bg-purple-900/50 text-purple-700 dark:text-purple-300 text-xs rounded-full"
                  >
                    {tag}
                    <button
                      onClick={() => setSelectedTags(selectedTags.filter(t => t !== tag))}
                      className="hover:text-purple-900 dark:hover:text-purple-100"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
              <select
                value=""
                onChange={(e) => {
                  if (e.target.value && !selectedTags.includes(e.target.value)) {
                    setSelectedTags([...selectedTags, e.target.value]);
                  }
                }}
                className="w-full p-2 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              >
                <option value="">-- Add a tag --</option>
                {availableTags.filter(t => !selectedTags.includes(t)).map((tag) => (
                  <option key={tag} value={tag}>{tag}</option>
                ))}
              </select>
              {selectedTags.length > 0 && (
                <button
                  onClick={() => setSelectedTags([])}
                  className="mt-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                >
                  Clear all tags
                </button>
              )}
            </div>

            {/* Series Search & Add */}
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                2. Search & Add Series
              </label>
              <input
                type="text"
                placeholder={selectedTags.length > 0 ? "Search within filtered series..." : "Search all series..."}
                value={seriesSearchTerm}
                onChange={(e) => setSeriesSearchTerm(e.target.value)}
                className="w-full p-2 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              />
              {seriesSearchTerm && filteredSeriesForSearch.length > 0 && (
                <div className="mt-2 max-h-48 overflow-y-auto border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700">
                  {filteredSeriesForSearch.slice(0, 20).map((s) => (
                    <div
                      key={s.ticker}
                      className="flex items-center justify-between p-2 hover:bg-gray-100 dark:hover:bg-gray-600 border-b border-gray-200 dark:border-gray-600 last:border-b-0"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-gray-900 dark:text-white truncate">
                          {s.title}
                        </div>
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                          {s.ticker}
                        </div>
                      </div>
                      <button
                        onClick={() => handleAddSeriesTicker(s.ticker)}
                        disabled={addingTicker || configuredSeriesTickers.includes(s.ticker)}
                        className={`ml-2 px-2 py-1 text-xs rounded font-medium transition-colors ${
                          configuredSeriesTickers.includes(s.ticker)
                            ? 'bg-gray-300 dark:bg-gray-600 text-gray-500 dark:text-gray-400 cursor-not-allowed'
                            : 'bg-purple-600 hover:bg-purple-700 text-white'
                        }`}
                      >
                        {configuredSeriesTickers.includes(s.ticker) ? 'Added' : 'Add'}
                      </button>
                    </div>
                  ))}
                  {filteredSeriesForSearch.length > 20 && (
                    <div className="p-2 text-xs text-gray-500 text-center">
                      {filteredSeriesForSearch.length - 20} more results...
                    </div>
                  )}
                </div>
              )}
              {seriesSearchTerm && filteredSeriesForSearch.length === 0 && (
                <div className="mt-2 p-2 text-sm text-gray-500 dark:text-gray-400">
                  No series found matching "{seriesSearchTerm}"
                </div>
              )}
            </div>

            {/* Series Select */}
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                3. Select Series {seriesLoading && <span className="text-gray-500">(loading...)</span>}
              </label>
              <select
                value={selectedSeries}
                onChange={(e) => {
                  setSelectedSeries(e.target.value);
                  setSelectedSeriesMarket('');
                }}
                disabled={seriesLoading}
                className="w-full p-2 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white disabled:opacity-50"
              >
                <option value="">-- Select series ({series.length} available) --</option>
                {series.map((s) => (
                  <option key={s.ticker} value={s.ticker}>
                    {s.title} ({s.ticker})
                  </option>
                ))}
              </select>
            </div>

            {/* Series Info */}
            {selectedSeries && (
              <div className="text-xs text-gray-500 dark:text-gray-400">
                {series.find(s => s.ticker === selectedSeries)?.category && (
                  <span>Category: {series.find(s => s.ticker === selectedSeries)?.category}</span>
                )}
              </div>
            )}

            {/* Markets List */}
            {selectedSeries && (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  4. Markets ({seriesMarkets.length})
                </label>
                {seriesMarketsLoading ? (
                  <div className="text-sm text-gray-500">Loading...</div>
                ) : seriesMarkets.length === 0 ? (
                  <div className="text-sm text-gray-500">No markets found</div>
                ) : (
                  <div className="space-y-2">
                    {seriesMarkets.map((market) => (
                      <MarketCard 
                        key={market.ticker} 
                        market={market}
                        seriesTicker={selectedSeries}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
