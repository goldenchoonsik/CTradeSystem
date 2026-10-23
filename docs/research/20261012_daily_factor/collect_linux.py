"""(호환 래퍼) 수집 코드는 shared/data_library/kr_equity/ 로 옮겼다 — 대시보드 '연구 데이터셋' 패널에서 단계별 실행.

  .venv/bin/python -m shared.data_library.kr_equity.cli <step>
  step: kind | naver | dart_corpcode | dart_fnltt | dart_events | dart_list | dart_shares | krx_marcap | panel
  (예전 이름: dart → dart_fnltt, universe → kind)
"""
import sys

from shared.data_library.kr_equity import cli

ALIAS = {"dart": "dart_fnltt", "universe": "kind"}

if __name__ == "__main__":
    cli.main([ALIAS.get(a, a) for a in sys.argv[1:]])
