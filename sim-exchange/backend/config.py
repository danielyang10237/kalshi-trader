from dataclasses import dataclass, field


@dataclass
class SimConfig:
    port: int = 9000
    initial_balance: int = 100_000  # cents ($1000)
    home_ticker: str = ""
    away_ticker: str = ""
    # Replay defaults
    default_spread: int = 2  # cents on each side of midpoint
    default_levels: int = 5
