"""(호환 래퍼) 패널 빌더는 shared/data_library/kr_equity/panel.py 로 옮겼다.

  .venv/bin/python docs/research/20261012_daily_factor/build_panel.py [prices|ext|fin|index|quality|all]
"""
import os
import sys

from shared.data_library.kr_equity import panel

if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    os.makedirs(panel.path("derived"), exist_ok=True)
    {"prices": panel.build_prices, "ext": panel.build_ext, "fin": panel.build_fin, "index": panel.build_index,
     "quality": panel.quality, "all": panel.build_all}[step]()
