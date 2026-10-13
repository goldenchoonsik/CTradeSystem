"""KIS 예탁원정보(한국예탁결제원) 원본 축적 — DataVault/raw/kis/ksdinfo/<종류>/

  .venv/bin/python scripts/kis_ksdinfo_sync.py daily                 # 과거 90일 ~ 미래 180일 전 종목 (매일)
  .venv/bin/python scripts/kis_ksdinfo_sync.py backfill --from 2010-01-01 [--kinds dividend,rev_split]

캐시 토큰만 쓴다(새 토큰 발급 금지). 앱 키는 환경변수 KIS_APP_KEY/KIS_APP_SECRET (CTrade 와 동일).
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.data_library import kis_ksdinfo as kk  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["daily", "backfill"])
    ap.add_argument("--from", dest="start", default="2010-01-01")
    ap.add_argument("--to", dest="end", default=None)
    ap.add_argument("--kinds", default=None)
    a = ap.parse_args()
    kinds = a.kinds.split(",") if a.kinds else None
    try:
        if a.mode == "daily":
            out = kk.sync(kinds=kinds)
        else:
            end = datetime.fromisoformat(a.end) if a.end else datetime.now()
            out = kk.backfill(datetime.fromisoformat(a.start), end, kinds=kinds, months=1, skip_done=True)
    except kk.KisUnavailable as e:
        print(f"[KSD] 건너뜀: {e}")
        sys.exit(2)
    bad = [o for o in out if not o["complete"]]
    print(f"[KSD] 완료: 스냅샷 {len(out)}, 행 {sum(o['rows'] for o in out)}, 불완전 {len(bad)}")
    for o in bad[:20]:
        print(f"   불완전 {o['kind']} {o['f_dt']}~{o['t_dt']}: {o['error']}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
