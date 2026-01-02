import json
import time
import requests
from datetime import datetime, timezone
from .authentification import get_authentification_headers, build_msg_string

EVENTS_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2/events"
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

class EventsFilter:
    def __init__(self, with_nested_markets: bool = False, status: str | None = None, series_ticker: str | None = None, min_close_ts: datetime | None = None):
        self.min_close_ts = min_close_ts
        self.with_nested_markets = with_nested_markets
        self.status = status
        self.series_ticker = series_ticker

    def to_dict(self):
        filter_packager = {}
        if self.with_nested_markets:
            filter_packager["with_nested_markets"] = "true"
        if self.status:
            filter_packager["status"] = self.status
        if self.series_ticker:
            filter_packager["series_ticker"] = self.series_ticker
        if self.min_close_ts:
            filter_packager["min_close_ts"] = int(self.min_close_ts.timestamp())
        return filter_packager

class EventCandlesticksFilter:
    def __init__(self, start_ts: datetime, end_ts: datetime, period_interval: int = 1):
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.period_interval = period_interval

    def to_dict(self):
        filter_packager = {}
        filter_packager["start_ts"] = int(self.start_ts.timestamp())
        filter_packager["end_ts"] = int(self.end_ts.timestamp())

        if self.period_interval is not None:
            filter_packager["period_interval"] = self.period_interval
        return filter_packager

class EventPercentilesFilter:
    def __init__(self, start_ts: datetime, end_ts: datetime, percentiles: list[int], period_interval: int = 1):
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.percentiles = percentiles
        self.period_interval = period_interval

    def to_dict(self):
        filter_packager = {}
        filter_packager["start_ts"] = int(self.start_ts.timestamp())
        filter_packager["end_ts"] = int(self.end_ts.timestamp())
        filter_packager["percentiles"] = self.percentiles
        filter_packager["period_interval"] = self.period_interval
        return filter_packager

def fetch_events_from_start(filter: EventsFilter, limit=200, sleep_seconds=0.25):
    cursor = None

    while True:
        params = filter.to_dict()   
        params["limit"] = limit

        if cursor:
            params["cursor"] = cursor
        try:
            r = requests.get(EVENTS_BASE_URL, params=params, timeout=10)
            r.raise_for_status()
        except requests.exceptions.HTTPError:
            if r.status_code == 429:
                print("Rate limited; sleeping...")
                time.sleep(2)
                continue
            raise
        except requests.exceptions.RequestException as e:

            print(f"Network error: {e}; retrying...")
            time.sleep(2)
            continue

        data = r.json()
        events = data.get("events", [])
        yield from events

        cursor = data.get("cursor")
        if not cursor:
            break

        time.sleep(sleep_seconds)

def retrieve_events_write_to_file(output_file: str, with_nested_markets: bool = False, status: str | None = None, series_ticker: str | None = None, min_close_ts: datetime | None = None):
    filter = EventsFilter(with_nested_markets=with_nested_markets, status=status, series_ticker=series_ticker, min_close_ts=min_close_ts)
    count = 0

    with open(output_file, "w", encoding="utf-8") as f:
        for event in fetch_events_from_start(filter):
            f.write(json.dumps(event) + "\n")
            count += 1

    print(f"Wrote {count} events to {output_file}")


def retrieve_event_candlesticks_write_to_file(
    output_file: str,
    event_ticker: str,
    series_ticker: str,
    start_ts: datetime,
    end_ts: datetime,
    period_interval: int = 1
):
    filter = EventCandlesticksFilter(
        start_ts=start_ts,
        end_ts=end_ts,
        period_interval=period_interval
    )

    url = f"{BASE_URL}/series/{series_ticker}/events/{event_ticker}/candlesticks"
    params = filter.to_dict()

    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()

    data = r.json()

    rows_written = 0
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(data))

    print(f"Wrote candlesticks for {rows_written} markets to {output_file}")

def retrieve_event_forecast_percentile_history_write_to_file(
    private_key_file: str,
    output_file: str,
    series_ticker: str,
    event_ticker: str,
    start_ts: datetime,
    end_ts: datetime,
    percentiles: list[int],
    period_interval: int = 1,
):
    path = f"/trade-api/v2/series/{series_ticker}/events/{event_ticker}/forecast_percentile_history"
    url = f"https://api.elections.kalshi.com{path}"

    headers = get_authentification_headers(
        method="GET",
        path=path,
        private_key_file="kalshi_key.pem"
    )

    params = {
        "start_ts": int(start_ts.timestamp()),
        "end_ts": int(end_ts.timestamp()),
        "period_interval": period_interval,
        "percentiles": percentiles,  # requests will encode repeated percentiles
    }

    r = requests.get(url, params=params, headers=headers, timeout=10)

    if r.status_code == 404 and "UNSUPPORTED_FORECAST" in r.text:
        print(f"No forecast history for {series_ticker}/{event_ticker} (UNSUPPORTED_FORECAST).")
        # write an empty payload so your pipeline doesn't break
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({"forecast_history": []}, f)
        return

    if r.status_code >= 400:
        raise Exception(f"{r.status_code} {r.reason}: {r.text}")
    data = r.json()

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(data))

    print(f"Wrote forecast percentile history for {len(data)} events to {output_file}")


if __name__ == "__main__":
    # retrieve_events_write_to_file("kalshi/data/sports_events_test.jsonl", 
    #     with_nested_markets=False, 
    #     status="settled", 
    #     series_ticker="KXNFLPREPACKSGP", 
    #     min_close_ts=datetime(2025, 12, 20, tzinfo=timezone.utc))

    # retrieve_event_candlesticks_write_to_file(
    #     "kalshi/data/sports_event_candlesticks_test.jsonl", 
    #     "KXNFLPREPACKSGP-25DEC20PHIWAS", 
    #     "KXNFLPREPACKSGP",
    #     start_ts=datetime(2025, 12, 20, tzinfo=timezone.utc),
    #     end_ts=datetime(2025, 12, 26, tzinfo=timezone.utc),
    #     period_interval=1)

    retrieve_event_forecast_percentile_history_write_to_file(
        private_key_file="kalshi_key.pem",
        output_file="kalshi/data/sports_event_forecast_percentile_history_test.jsonl", 
        series_ticker="KXNFLPREPACKSGP", 
        event_ticker="KXNFLPREPACKSGP-25DEC20PHIWAS", 
        start_ts=datetime(2025, 12, 20, tzinfo=timezone.utc),
        end_ts=datetime(2025, 12, 26, tzinfo=timezone.utc),
        percentiles=[1000,2500,5000,7500,9000],
        period_interval=1440)