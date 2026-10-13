"""staging 데이터 품질 점검 — 종목별 1분봉 일자 상태·누락일(정지 vs 수집 누락)·거래량 대조·수정 계수.

  .venv/bin/python docs/research/20261011_vol_spike/quality_check.py
출력: DataVault/staging/vol_spike_universe/quality/quality_by_code.csv, quality_summary.json (연구 데이터셋 패널이 읽는다)
"""
import glob
import json
import os
from collections import Counter
from datetime import datetime
from multiprocessing import Pool

import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
STAGE = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe")
M1 = os.path.join(STAGE, "raw", "creon", "candle_1m")
D_ADJ = os.path.join(STAGE, "raw", "creon", "candle_1d")
D_RAW = os.path.join(STAGE, "raw", "creon", "candle_1d_unadj")
OUT = os.path.join(STAGE, "quality")
AFTER_HOURS_FROM = "2026-09-14"     # Creon 일봉 종가·거래량이 20:00 시간외까지 포함하기 시작한 날


def _daily(path):
    if not os.path.exists(path):
        return None
    return pd.read_csv(path, parse_dates=["timestamp"]).drop_duplicates("timestamp").set_index("timestamp").sort_index()


def check(code):
    r = {"code": code}
    metas = glob.glob(os.path.join(M1, code, "*.meta.json"))
    st = Counter()
    for p in metas:
        try:
            with open(p, encoding="utf-8") as f:
                st[json.load(f).get("status", "?")] += 1
        except Exception:
            st["unreadable"] += 1
    r.update({f"st_{k}": v for k, v in st.items()})
    days = {os.path.basename(p)[:10] for p in glob.glob(os.path.join(M1, code, "*.csv"))}
    r["m1_days"] = len(days)
    raw = _daily(os.path.join(D_RAW, f"{code}_1d.csv"))
    adj = _daily(os.path.join(D_ADJ, f"{code}_1d.csv"))
    if raw is None or adj is None or not days:
        r["issue"] = "daily_missing" if days else "m1_missing"
        return r
    first = min(days)
    cal = raw[raw.index >= first]
    traded_days = {d.strftime("%Y-%m-%d") for d in cal.index[cal["upbit_volume"] > 0]}
    halted_days = {d.strftime("%Y-%m-%d") for d in cal.index[cal["upbit_volume"] <= 0]}
    r["halted_days"] = len(halted_days)
    r["missing_traded_days"] = len(traded_days - days)              # 거래가 있었는데 1분봉이 없는 날 = 수집 누락
    r["m1_on_halted_days"] = len(days & halted_days)
    vol = {}
    for d in sorted(days)[-40:] + sorted(days)[:5]:                  # 최근 40일 + 처음 5일 표본
        x = pd.read_csv(os.path.join(M1, code, f"{d}.csv"))
        t = x["timestamp"].str[11:16]
        vol[d] = x.loc[(t >= "09:01") & (t <= "15:30"), "upbit_volume"].sum()
    v = pd.Series(vol)
    v.index = pd.to_datetime(v.index)
    dv = raw["upbit_volume"].reindex(v.index)
    pre = (v.index < AFTER_HOURS_FROM) & (dv > 0)
    ratio = (v[pre] / dv[pre])
    r["vol_ratio_med"] = round(float(ratio.median()), 4) if len(ratio) else None
    r["vol_ratio_over"] = int((ratio > 1.001).sum())                # 1분봉 정규장 합 > 일봉 → 이상
    f = (adj["upbit_close"] / raw["upbit_close"]).dropna()
    f = f[f.index >= first]
    r["factor_changes"] = int((f.pct_change().abs() > 0.001).sum())
    r["factor_min"], r["factor_max"] = round(float(f.min()), 4), round(float(f.max()), 4)
    issues = []
    if r["missing_traded_days"]:
        issues.append("missing_days")
    if r["vol_ratio_over"]:
        issues.append("vol_over_daily")
    if st.get("QUARANTINED") or st.get("LOST"):
        issues.append("quarantined")
    r["issue"] = ",".join(issues)
    return r


def main():
    codes = sorted(d for d in os.listdir(M1) if os.path.isdir(os.path.join(M1, d)))
    with Pool(4) as p:
        rows = p.map(check, codes, chunksize=8)
    q = pd.DataFrame(rows).fillna(0)
    os.makedirs(OUT, exist_ok=True)
    q.to_csv(os.path.join(OUT, "quality_by_code.csv"), index=False)
    issue_counts = Counter(i for s in q["issue"].astype(str) for i in s.split(",") if i and i != "0")
    status_cols = [c for c in q.columns if c.startswith("st_")]
    summary = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "codes": len(q),
        "day_status": {c[3:]: int(q[c].sum()) for c in status_cols},
        "issues": dict(issue_counts),
        "codes_with_issue": int((q["issue"].astype(str).str.len() > 1).sum()),
        "missing_traded_days_total": int(q["missing_traded_days"].sum()),
        "halted_days_total": int(q["halted_days"].sum()),
        "codes_with_adjustment": int((q["factor_changes"] > 0).sum()),
        "vol_ratio_median": float(pd.to_numeric(q["vol_ratio_med"], errors="coerce").median()),
    }
    with open(os.path.join(OUT, "quality_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    bad = q[q["issue"].astype(str).str.len() > 1]
    if len(bad):
        print(bad.sort_values("missing_traded_days", ascending=False).head(15).to_string(index=False))


if __name__ == "__main__":
    main()
