from pathlib import Path
import nfl_data_py as nfl

import sys

def _export_nfl_schedules(start_year: int, end_year: int, out_dir: str = "nfl/data/schedules") -> str:
    """
    Imports NFL schedules for years in [start_year, end_year] (inclusive) and writes them to a CSV named:
      nfl_schedule_<start>_<end>.csv

    Returns the output filepath as a string.
    """
    if not isinstance(start_year, int) or not isinstance(end_year, int):
        raise TypeError("start_year and end_year must be integers.")
    if start_year > end_year:
        raise ValueError("start_year must be <= end_year.")

    years = list(range(start_year, end_year + 1))
    sched = nfl.import_schedules(years)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    filepath = out_path / f"nfl_schedule_{start_year}_{end_year}.csv"
    sched.to_csv(filepath, index=False)

    return str(filepath)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python schedule_handler.py <start_year> <end_year>")
        sys.exit(1)
    start_year = int(sys.argv[1])
    end_year = int(sys.argv[2])
    _export_nfl_schedules(start_year, end_year)