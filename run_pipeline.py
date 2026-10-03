"""Build the database from the project root:

    python run_pipeline.py                 # live NHL API
    python run_pipeline.py --source demo   # offline synthetic season (no network)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from nhl_pipeline.pipeline import main  # noqa: E402

if __name__ == "__main__":
    main()
