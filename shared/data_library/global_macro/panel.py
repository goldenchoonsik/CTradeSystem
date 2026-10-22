"""series/*.csv → derived 패널. 핵심 규칙: 월말 t 의 값 = '공개일(기간 끝 + lag_days) ≤ t' 인 마지막 관측(미래참조 차단).

  derived/daily.parquet         일봉 시계열(D) 원값 — 휴일 NaN 그대로
  derived/weekly.parquet        금요일 기준 주말 마지막 값(D)
  derived/monthly_pit.parquet   월말(달력) 시점에 알 수 있던 값 — 전 시계열(정답 라벨 lag=None 은 제외)
  derived/monthly_age.parquet   그 값의 기간 끝이 월말보다 며칠 전인지(신선도)
  quality/panel_quality.json    시계열별 범위·최신일·신선도 경고
"""
import json
import os
from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd

from . import catalog, store

STALE_DAYS = {"D": 10, "W": 21}          # 최신 관측이 이보다 오래되면 경고(월은 lag_days + 45)


def load(key: str) -> Optional[pd.Series]:
    fp = store.path("series", f"{key}.csv")
    if not os.path.exists(fp):
        return None
    d = pd.read_csv(fp, parse_dates=["date"])
    s = d.set_index("date")["value"].astype(float).sort_index()
    return s[~s.index.duplicated(keep="last")].rename(key)


def load_all() -> Dict[str, pd.Series]:
    return {s["key"]: x for s in catalog.SERIES if (x := load(s["key"])) is not None}


def point_in_time(s: pd.Series, lag_days: int, at: pd.DatetimeIndex):
    """각 시점 at 에서 공개돼 있던 마지막 값과 그 값의 기간 끝 날짜."""
    s = s.dropna().sort_index()
    avail = s.index + pd.Timedelta(days=int(lag_days))
    pos = avail.searchsorted(at, side="right") - 1
    ok = pos >= 0
    val = np.full(len(at), np.nan)
    per = np.full(len(at), np.datetime64("NaT"), dtype="datetime64[ns]")
    val[ok] = s.values[pos[ok]]
    per[ok] = s.index.values[pos[ok]]
    return pd.Series(val, index=at), pd.Series(per, index=at)


def build(start: str = "1960-01-31") -> dict:
    data = load_all()
    os.makedirs(store.path("derived"), exist_ok=True)
    daily_keys = [s["key"] for s in catalog.SERIES if s["freq"] == "D" and s["key"] in data]
    daily = pd.DataFrame({k: data[k] for k in daily_keys}).sort_index()
    daily.to_parquet(store.path("derived", "daily.parquet"))
    weekly = daily.groupby(daily.index.to_period("W-FRI")).last()
    weekly.index = weekly.index.to_timestamp(how="end").normalize()
    weekly.to_parquet(store.path("derived", "weekly.parquet"))

    today = pd.Timestamp(datetime.now().date())
    month_ends = pd.date_range(start, today, freq="ME")
    pit, age = {}, {}
    for s in catalog.SERIES:
        k = s["key"]
        if k not in data or s["lag_days"] is None:
            continue
        v, per = point_in_time(data[k], s["lag_days"], month_ends)
        pit[k] = v
        age[k] = (month_ends.to_series(index=month_ends) - per).dt.days
    pd.DataFrame(pit).to_parquet(store.path("derived", "monthly_pit.parquet"))
    pd.DataFrame(age).to_parquet(store.path("derived", "monthly_age.parquet"))
    labels = {s["key"]: data[s["key"]] for s in catalog.SERIES if s["lag_days"] is None and s["key"] in data}
    if labels:
        pd.DataFrame(labels).to_parquet(store.path("derived", "labels.parquet"))

    q = {"built_at": datetime.now().isoformat(timespec="seconds"), "series": {}}
    for s in catalog.SERIES:
        k = s["key"]
        if k not in data:
            q["series"][k] = {"missing": True}
            continue
        x = data[k].dropna()
        last = x.index[-1]
        limit = STALE_DAYS.get(s["freq"]) or ((s["lag_days"] or 0) + 45)
        q["series"][k] = {"first": str(x.index[0].date()), "last": str(last.date()), "n": int(len(x)),
                          "days_old": int((today - last).days), "stale": bool((today - last).days > limit)}
    os.makedirs(store.path("quality"), exist_ok=True)
    with open(store.path("quality", "panel_quality.json"), "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=1)
    store.log(f"[panel] 일봉 {daily.shape}, 주봉 {weekly.shape}, 월 PIT {len(month_ends)}×{len(pit)}, "
              f"오래된 시계열 {[k for k, v in q['series'].items() if v.get('stale') or v.get('missing')]}")
    return q
