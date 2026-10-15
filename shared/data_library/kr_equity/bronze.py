"""Creon 일봉 착륙 구역(staging/kr_equity/raw/creon) → DataVault/raw/creon 원천 (미국 일봉 raw/yahoo 와 같은 구조).

  raw/creon/candle_1d_unadj/<A코드>_1d.csv   무수정 일봉(공통 절차 raw_day_store.ingest_file — 날짜 원장·봉인·격리), 열 = timestamp,open,high,low,close,volume,value
  raw/creon/corporate_actions/<A코드>.json   기업행위 원장(corporate_actions: 수정/무수정 종가 계수 변화, 추가만)

착륙 구역(Windows 수집기가 쓰는 곳)의 열 이름은 옛 upbit_* 이다 — 원천으로 올리면서 표준 열 이름으로 바꾼다.
수정 일봉은 원천에 두지 않는다: 무수정 + 원장 계수로 읽을 때 계산하고(corporate_actions.adjust_prices), 착륙 구역의 수정 일봉은 원장 검증 근거로만 쓴다.
"""
import os
from typing import Callable, Dict, List, Optional

import pandas as pd

from shared.data_library import corporate_actions, raw_ingest
from shared.data_library.paths import data_vault_path

PROVIDER = "creon"
MARKET = "KRX"
LANDING = ("staging", "kr_equity", "raw", "creon")
RENAME = {"upbit_open": "open", "upbit_high": "high", "upbit_low": "low", "upbit_close": "close",
          "upbit_volume": "volume", "upbit_trade_price": "value"}
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "value"]


def unadj_path(code: str) -> str:
    return data_vault_path("raw", PROVIDER, "candle_1d_unadj", f"{code}_1d.csv")


def actions_root() -> str:
    return data_vault_path("raw", PROVIDER, "corporate_actions")


def landing(kind: str, code: str) -> str:
    return data_vault_path(*LANDING, kind, f"{code}_1d.csv")


def landing_codes() -> List[str]:
    d = data_vault_path(*LANDING, "candle_1d_unadj")
    return sorted(f[:-len("_1d.csv")] for f in os.listdir(d) if f.endswith("_1d.csv")) if os.path.isdir(d) else []


def _read(fp: str) -> Optional[pd.DataFrame]:
    return pd.read_csv(fp) if os.path.exists(fp) else None


def update_actions(code: str, adj: Optional[pd.DataFrame], unadj: Optional[pd.DataFrame]) -> int:
    """수정·무수정 일봉(같은 Creon 출처)의 종가 계수 변화 → 기업행위 원장에 추가. 반환: 새 이벤트 수.
    두 파일의 마지막 날이 다를 수 있으므로 겹치는 날 범위만 관측 기간으로 기록한다."""
    if adj is None or unadj is None or adj.empty or unadj.empty:
        return 0
    both = sorted(set(adj["timestamp"]) & set(unadj["timestamp"]))
    if len(both) < 2:
        return 0
    ev = corporate_actions.detect(adj, unadj)
    return len(corporate_actions.update(actions_root(), code, ev, (both[0][:10], both[-1][:10]))["added"])


def operating_adjusted(code: str, unadj: Optional[pd.DataFrame] = None) -> Optional[pd.DataFrame]:
    """운용 종목: raw/creon/candle_1d(수정, 원장 근거)와 raw/creon/candle_1d_unadj(원천)로 원장을 갱신하고,
    무수정 × 원장 계수로 만든 수정 일봉(표준 열 + close_unadj·factor)을 돌려준다. 무수정 원천이 없으면 None."""
    fp = unadj_path(code)
    if unadj is None:
        if not os.path.exists(fp):
            return None
        unadj = pd.read_csv(fp)
    adj = _read(data_vault_path("raw", PROVIDER, "candle_1d", f"{code}_1d.csv"))
    update_actions(code, adj, unadj)
    d = unadj.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    f = corporate_actions.cumulative_factor(corporate_actions.events(actions_root(), code), pd.to_datetime(d["timestamp"]).dt.normalize()).to_numpy()
    out = d[["timestamp"]].copy()
    for c in ("open", "high", "low", "close"):
        out[c] = d[c].astype(float).to_numpy() * f
    out["volume"] = d["volume"].astype(float).to_numpy() / f
    out["value"] = d["value"].astype(float).to_numpy()
    out["close_unadj"] = d["close"].astype(float).to_numpy()
    out["factor"] = f
    return out


def ingest_code(code: str, log: Callable[[str], None] = print) -> Optional[Dict]:
    """한 종목: 무수정 일봉을 원천에 반영하고 기업행위 원장을 갱신. 착륙 구역에 없으면 None."""
    raw = _read(landing("candle_1d_unadj", code))
    if raw is None or raw.empty:
        return None
    df = raw.rename(columns=RENAME)[COLUMNS].drop_duplicates("timestamp").sort_values("timestamp")
    target = unadj_path(code)
    before = raw_ingest.last_timestamp(target)
    rep = raw_ingest.ingest_dataframe(df, target, provider=PROVIDER, market=MARKET, interval="1d", requested_after=before,
                                      source_exhausted=True, volume_col="volume", log=log)
    if rep is None:
        return None
    adj = _read(landing("candle_1d", code))
    n_events = update_actions(code, adj, raw)
    return {"added_rows": rep.get("added_rows", 0), "conflicts": rep.get("conflicts", 0), "events_added": n_events}


def run(limit: Optional[int] = None, log: Callable[[str], None] = print) -> Dict[str, int]:
    tot = {"codes": 0, "added_rows": 0, "conflicts": 0, "events_added": 0, "failed": 0}
    for code in landing_codes()[:limit]:
        try:
            r = ingest_code(code, log=lambda *_: None)
        except Exception as e:
            log(f"[creon_bronze] {code} 실패: {e}")
            tot["failed"] += 1
            continue
        if r is None:
            tot["failed"] += 1
            continue
        tot["codes"] += 1
        for k in ("added_rows", "conflicts", "events_added"):
            tot[k] += r[k]
        if tot["codes"] % 250 == 0:
            log(f"[creon_bronze] {tot['codes']}종목 처리 — {tot}")
    log(f"[creon_bronze] 완료 {tot}")
    return tot
