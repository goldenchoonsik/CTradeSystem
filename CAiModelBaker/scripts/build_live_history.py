# -*- coding: utf-8 -*-
"""
실전 백필 이력 빌더 — scale_v2 모델의 장 시작 전 지표 이력
===========================================================

scale_v2 지표는 세션을 넘어 연속으로 계산되므로(param_policy.py), 실전도 장 시작 시점에 학습과 같은
과거 봉을 갖고 있어야 첫 봉부터 학습과 같은 지표 값을 낸다. 과거 봉은 학습 Gold 와 **같은 함수**
(autolab_bridge.compose_gold_frame — 같은 결합·같은 정합 감사)로 DataVault 에서 조립한다.

CTrade 는 별도 프로세스로 이 스크립트를 실행한다 (CTrade 와 CAiModelBaker 의 `core` 패키지 이름 충돌 회피).

    python CAiModelBaker/scripts/build_live_history.py --symbol 069500 --interval 15m --bars 300 --out <path.parquet>

출력: <out> (마지막 N 거래일 Gold 행) + <out>.meta.json ({rows, first, last, last_date, built_at})
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_BAKER_ROOT = os.path.dirname(_HERE)
_SYSTEM_ROOT = os.path.dirname(_BAKER_ROOT)
for _p in (_SYSTEM_ROOT, _BAKER_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def build(symbol: str, interval: str, bars: int, out: str, market_type: str = "KRX") -> dict:
    import pandas as pd
    from CAiModelBaker.core.autolab_bridge import compose_gold_frame
    from shared.features.param_policy import session_bars_per_day

    df, _, _ = compose_gold_frame(symbol, interval, lambda m: print(m, flush=True))
    if df is None or df.empty:
        raise RuntimeError(f"{symbol}({interval}) 이력을 조립하지 못했습니다")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    days = sorted(df["timestamp"].dt.date.unique())
    need_days = int(math.ceil(bars / session_bars_per_day(interval, market_type))) + 1
    keep = set(days[-need_days:])
    tail = df[df["timestamp"].dt.date.isin(keep)].reset_index(drop=True)

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out + ".tmp"
    tail.to_parquet(tmp, index=False)
    os.replace(tmp, out)
    meta = {
        "symbol": symbol, "interval": interval, "rows": int(len(tail)), "days": len(keep),
        "first": str(tail["timestamp"].min()), "last": str(tail["timestamp"].max()),
        "last_date": str(max(keep)), "requested_bars": int(bars),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(out + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--interval", required=True)
    ap.add_argument("--bars", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--market", default="KRX")
    a = ap.parse_args()
    m = build(a.symbol, a.interval, a.bars, a.out, a.market)
    print("LIVE_HISTORY_META " + json.dumps(m, ensure_ascii=False), flush=True)
