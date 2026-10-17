"""SEC companyfacts → 분기 희석 EPS·희석 주식 수 (최초 공시본, 분할은 현재 주식 기준으로 환산) → PER·시총 패널. 가격은 Yahoo 무수정 종가."""
import json, os
import numpy as np, pandas as pd
ROOT = "/home/kuzimo/projects/CTradeSystem/DataVault"
SEC = f"{ROOT}/staging/us_fundamentals_sec/raw/companyfacts"
EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]
SH_TAGS = ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasicAndDiluted"]

def splits(t):
    fp = f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv"
    if not os.path.exists(fp): return pd.Series(dtype=float)       # 파일 없음 = 분할 없음
    d = pd.read_csv(fp, parse_dates=["date"])
    d = d[d.type == "split"].drop_duplicates("date")
    return pd.Series((d.numerator / d.denominator).values, index=pd.DatetimeIndex(d.date)).sort_index()

def S(sp, a, b):   # a < ex <= b 사이 분할 배수
    if sp.empty: return 1.0
    m = (sp.index > a) & (sp.index <= b)
    return float(sp[m].prod()) if m.any() else 1.0

def facts(j, tags, unit):
    best = None
    for tg in tags:
        it = j.get("facts", {}).get("us-gaap", {}).get(tg, {}).get("units", {}).get(unit)
        if it:
            df = pd.DataFrame(it)
            if best is None or df["end"].max() > best["end"].max(): best = df   # 최신까지 이어지는 태그
    return best

def quarters(df):
    """[start,end,val,filed] → 분기 값. 3개월짜리는 그대로, 연간은 Q4 = 연간 − (Q1~Q3 합). 최초 공시본 우선."""
    df = df.copy(); df["start"] = pd.to_datetime(df.start); df["end"] = pd.to_datetime(df.end); df["filed"] = pd.to_datetime(df.filed)
    df["dur"] = (df.end - df.start).dt.days
    df = df.sort_values("filed").drop_duplicates(["start", "end"], keep="first")
    q = df[df.dur.between(80, 100)][["end", "val", "filed"]].rename(columns={"val": "v"})
    out = {r.end: (r.v, r.filed) for r in q.itertuples()}
    for r in df[df.dur.between(350, 380)].itertuples():
        inner = [out[e] for e in out if r.start < e <= r.end and e != r.end]
        if len(inner) == 3 and r.end not in out:
            out[r.end] = (r.val - sum(v for v, _ in inner), r.filed)
    return pd.DataFrame([(e, v, f) for e, (v, f) in out.items()], columns=["end", "v", "filed"]).sort_values("end").reset_index(drop=True)

def build(t, cal):
    j = json.load(open(f"{SEC}/{t}.json"))
    e = facts(j, EPS_TAGS, "USD/shares"); s = facts(j, ["NetIncomeLoss"], "USD")
    if e is None or s is None: return None
    sp = splits(t)
    END = pd.Timestamp("2100-01-01")
    eq = quarters(e)
    ni = quarters(s)                                                   # 분기 순이익(연간−3분기 포함, 가산 가능)
    eq["c"] = [v / S(sp, f, END) for v, f in zip(eq.v, eq.filed)]   # 현재 주식 기준 EPS
    m = eq.merge(ni.rename(columns={"v": "ni", "filed": "f2"}), on="end")
    m = m[m.v.abs() > 0.02]
    m["c"] = m.ni / m.eq_c if False else m.ni / (m.v / np.array([S(sp, f, END) for f in m.filed]))   # 현재 주식 기준 희석 주식 수
    m["c"] = m.c.where(m.c > 0)
    sq = m[["end", "c", "filed"]].sort_values("end"); sq["c"] = sq.c.rolling(4, min_periods=1).median()
    # 연속 4분기 TTM (분기 간격 ~91일)
    eq["ttm"] = eq.c.rolling(4).sum(); eq["span"] = (eq.end - eq.end.shift(3)).dt.days
    eq = eq[eq.span.between(250, 290) & eq.ttm.notna()]
    eq["avail"] = eq.filed.cummax()
    ttm = pd.Series(eq.ttm.values, index=eq.avail.values).groupby(level=0).last()
    ttm = ttm.reindex(cal.union(ttm.index)).ffill().shift(1).reindex(cal)       # 공시 다음 날부터
    last_end = pd.Series(eq.end.values, index=eq.avail.values).groupby(level=0).last()
    last_end = last_end.reindex(cal.union(last_end.index)).ffill().shift(1).reindex(cal)
    stale = (cal.to_series().values - pd.to_datetime(last_end.values)) > np.timedelta64(200, "D")
    ttm = ttm.where(~stale)
    sq = sq.sort_values("filed"); sh = pd.Series(sq.c.values, index=sq.filed.values).groupby(level=0).last()
    sh = sh.reindex(cal.union(sh.index)).ffill().shift(1).reindex(cal)
    px = pd.read_csv(f"{ROOT}/raw/yahoo/candle_1d/{t}_1d.csv", parse_dates=["timestamp"]).set_index("timestamp").close
    px = px[~px.index.duplicated()]
    cum = pd.Series([S(sp, d, END) for d in px.index], index=px.index)           # 해당일 이후 분할 누적
    pc = (px / cum).reindex(cal)                                                  # 현재 주식 기준 가격(분할 연속)
    return pd.DataFrame({"price": pc, "ttm_eps": ttm, "shares": sh, "close_raw": px.reindex(cal)})

def load_all():
    cal = pd.bdate_range("2016-01-01", "2026-10-14")
    pool = json.load(open(f"{ROOT}/staging/us_fundamentals_sec/reference/pool_cik.json"))
    P, E, SHR, RAW = {}, {}, {}, {}
    bad = []
    for t in pool:
        try:
            d = build(t, cal)
        except Exception as ex:
            bad.append((t, repr(ex)[:80])); continue
        if d is None or d.ttm_eps.notna().sum() < 500: bad.append((t, "재무 부족")); continue
        P[t], E[t], SHR[t], RAW[t] = d.price, d.ttm_eps, d.shares, d.close_raw
    return cal, pd.DataFrame(P), pd.DataFrame(E), pd.DataFrame(SHR), pd.DataFrame(RAW), bad

if __name__ == "__main__":
    cal, P, E, SHR, RAW, bad = load_all()
    print("제외:", bad, "| 사용", P.shape[1])
    M = P * SHR / 1e9
    for d in ["2016-06-01", "2019-01-02", "2021-01-04", "2023-01-03", "2026-01-02"]:
        r = M.loc[d].dropna().sort_values(ascending=False).head(20)
        print(d, " ".join(f"{k}:{v:.0f}" for k, v in r.items()))
    raise SystemExit
