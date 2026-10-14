"""래퍼 — 본체는 shared/data_library/us_equity/collect.py (응답 원본 raw/yahoo/responses, 원천 raw/yahoo/candle_1d).

  .venv/bin/python docs/research/20261014_us_equity/collect_yahoo.py chart [--day YYYYMMDD] [--limit N]
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from shared.data_library.us_equity.collect import main  # noqa: E402

if __name__ == "__main__":
    main()
