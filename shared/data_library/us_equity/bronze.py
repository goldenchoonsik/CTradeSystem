"""Yahoo 응답 원본 → DataVault/raw/yahoo 원천(무수정 일봉·기업행위). 모든 원천과 같은 공통 절차(raw_ingest)를 쓴다."""
import gzip
import os
from datetime import datetime
from typing import Callable, Dict, Optional

import pandas as pd

from shared.data_library import raw_ingest
from shared.data_library.paths import data_vault_path
from shared.data_library.us_equity.yahoo import ACTION_COLUMNS, parse_chart

PROVIDER = "yahoo"
MARKET = "US"


def responses_dir(day: str) -> str:
    return data_vault_path("raw", PROVIDER, "responses", "chart_1d", day)


def candle_path(sym: str) -> str:
    return data_vault_path("raw", PROVIDER, "candle_1d", f"{sym}_1d.csv")


def actions_path(sym: str) -> str:
    return data_vault_path("raw", PROVIDER, "corporate_actions", f"{sym}.csv")


def save_response(sym: str, raw: bytes, day: str) -> str:
    """응답 원본 보존(덮어쓰기 없음, 읽기 전용)."""
    fp = os.path.join(responses_dir(day), f"{sym}.json.gz")
    if not os.path.exists(fp):
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        tmp = fp + ".tmp"
        with open(tmp, "wb") as f:
            f.write(gzip.compress(raw))
        os.replace(tmp, fp)
        os.chmod(fp, 0o444)
    return fp


def merge_actions(sym: str, new: pd.DataFrame, log: Callable[[str], None] = print) -> Dict[str, int]:
    """기업행위는 (날짜, 종류) 단위로 더하기만 한다. 이미 있는 행과 값이 다르면 기존을 두고 충돌로 남긴다."""
    fp = actions_path(sym)
    old = pd.read_csv(fp, dtype={"date": str, "type": str}) if os.path.exists(fp) else pd.DataFrame(columns=ACTION_COLUMNS)
    key = lambda df: df["date"].astype(str) + "|" + df["type"].astype(str)
    have = dict(zip(key(old), old.to_dict("records")))
    add, conflicts = [], 0
    for rec in new.to_dict("records"):
        k = f"{rec['date']}|{rec['type']}"
        if k not in have:
            add.append(rec)
            continue
        o = have[k]
        same = all(_eq(o.get(c), rec.get(c)) for c in ("numerator", "denominator", "amount"))
        if not same:
            conflicts += 1
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(os.path.join(os.path.dirname(fp), "_conflicts.log"), "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {sym} {k} 기존={o} 신규={rec}\n")
    if add:
        out = pd.concat([old, pd.DataFrame(add, columns=ACTION_COLUMNS)], ignore_index=True)
        out = out.sort_values(["date", "type"]).reset_index(drop=True)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        tmp = fp + ".tmp"
        out.to_csv(tmp, index=False)
        os.replace(tmp, fp)
    if conflicts:
        log(f"   ⚠️ [Yahoo] {sym}: 기업행위 {conflicts}건이 기존 기록과 다릅니다 — 기존 유지, _conflicts.log 확인")
    return {"added": len(add), "conflicts": conflicts}


def _eq(a, b) -> bool:
    na, nb = pd.isna(a) if not isinstance(a, str) else False, pd.isna(b) if not isinstance(b, str) else False
    if na or nb:
        return na and nb
    try:
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
    except (TypeError, ValueError):
        return str(a) == str(b)


def ingest_response(sym: str, raw: bytes, *, full_history: bool, log: Callable[[str], None] = print) -> Optional[Dict]:
    """응답 하나를 원천 일봉·기업행위에 반영. full_history=True 는 상장 이후 전체를 받은 응답(앞쪽 '원천에 없음' 기록)."""
    bars, actions, info = parse_chart(raw)
    target = candle_path(sym)
    before = raw_ingest.last_timestamp(target)
    need_head = any(k in ("full", "head") for _, _, k in raw_ingest.fetch_ranges(target))
    rep = raw_ingest.ingest_dataframe(bars, target, provider=PROVIDER, market=MARKET, interval="1d",
                                      requested_after=before, source_exhausted=full_history, volume_col="volume",
                                      timezone_name="America/New_York", log=log)
    if rep is None:
        return None
    if need_head and full_history and len(bars):
        raw_ingest.note_head_floor(target, raw_ingest._kst(str(bars["timestamp"].iloc[0])), log=log)
    act = merge_actions(sym, actions, log=log)
    return {**info, "added_rows": rep.get("added_rows", 0), "conflicts": rep.get("conflicts", 0),
            "actions_added": act["added"], "action_conflicts": act["conflicts"]}
