"""격리(QUARANTINED)된 1분봉 날을 무수정 일봉 기준으로 다시 봉인한다 (staging 전용).

수집기 봉인(_candle_day_seal)은 1분봉(무수정) 거래량을 수정 일봉 거래량과 비교해, 액면병합·분할 이전 날을 전부
'1분봉 거래량이 일봉보다 큼'으로 격리한다(예: KR모터스 5:1 병합 → 이전 날 1분봉 = 수정 일봉의 5배).
seal_closed_days 는 OPEN/QUARANTINED 날을 다시 검사하므로, 같은 정의의 무수정 일봉을 기준으로 넘겨 재판정한다.

  .venv/bin/python docs/research/20261011_vol_spike/reseal_unadjusted.py
"""
import glob
import json
import os
import sys
from collections import Counter

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
STAGE = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe")
sys.path.insert(0, os.path.join(REPO, "CAiModelBaker", "core"))
import candle_day_store as cds  # noqa: E402

ROOT = os.path.join(STAGE, "raw", "creon", "candle_1m")
UNADJ = os.path.join(STAGE, "raw", "creon", "candle_1d_unadj")


def quarantined_codes():
    out = []
    for code in sorted(os.listdir(ROOT)):
        for p in glob.glob(os.path.join(ROOT, code, "*.meta.json")):
            with open(p, encoding="utf-8") as f:
                if json.load(f).get("status") == "QUARANTINED":
                    out.append(code)
                    break
    return out


def main():
    codes = quarantined_codes()
    tot = Counter()
    still = Counter()
    for code in codes:
        daily = os.path.join(UNADJ, f"{code}_1d.csv")
        if not os.path.exists(daily):
            print(f"{code}: 무수정 일봉 없음 — 건너뜀")
            continue
        rep = cds.seal_closed_days(ROOT, code, daily_csv=daily, tick_root=None, log=lambda *_: None)
        tot["sealed"] += len(rep["sealed"])
        tot["quarantined"] += len(rep["quarantined"])
        for d in rep["quarantined"]:
            with open(os.path.join(ROOT, code, f"{d}.meta.json"), encoding="utf-8") as f:
                for reason in json.load(f).get("failures", []):
                    still[reason.split(" ")[0] + " " + reason.split(" ")[1] if " " in reason else reason] += 1
    print(f"재판정 종목 {len(codes)}: VALID 로 {tot['sealed']} 일, 여전히 격리 {tot['quarantined']} 일")
    if still:
        print("남은 격리 사유:", dict(still.most_common(10)))


if __name__ == "__main__":
    main()
