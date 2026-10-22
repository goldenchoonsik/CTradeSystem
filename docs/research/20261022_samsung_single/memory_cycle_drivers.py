"""메모리 사이클은 무엇을 따라가나 — D램 수출물가 전년비 기준 선후 상관·전환점 (2026-10-22 탐색, 사전등록 아님).

사이클 = D램 수출물가(달러기준) 전년비(로그). 경제적 시점(데이터월·분기말) 기준이며 발표 시차는 넣지 않음(원인 구조 보기용).
후보: 공급(마이크론 설비투자), 수요(빅테크 4사 설비투자), 재고(한국 반도체 출하·재고), 금리(미 10년물·3개월물),
     주가(SOX·삼성), 환율.
  .venv/bin/python -I docs/research/20261022_samsung_single/memory_cycle_drivers.py
"""
import json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from shared.data_library.paths import data_vault_path  # noqa: E402
from analysis_ABD import turning_points  # noqa: E402

SC = data_vault_path("staging", "samsung_cycle")
CF = data_vault_path("staging", "us_fundamentals_sec", "raw", "companyfacts")
M = pd.read_parquet(os.path.join(SC, "derived", "monthly.parquet"))


def monthly(name):
    d = pd.read_csv(os.path.join(SC, name), dtype={"ym": str})
    d.index = pd.PeriodIndex(pd.to_datetime(d.pop("ym"), format="%Y%m"), freq="M")
    return d


def capex_quarterly(files, tags):
    """현금흐름표 누적(YTD) → 분기값. 같은 (시작,끝)은 최신 접수본(재작성 반영, 경제 시점 분석용)."""
    rows = []
    for f in files:
        g = json.load(open(os.path.join(CF, f)))["facts"]["us-gaap"]
        for t in tags:
            for x in g.get(t, {}).get("units", {}).get("USD", []):
                if "start" in x:
                    rows.append(dict(start=pd.Timestamp(x["start"]), end=pd.Timestamp(x["end"]), val=x["val"], filed=x["filed"]))
    f = pd.DataFrame(rows).sort_values("filed").groupby(["start", "end"]).last().reset_index()
    f["days"] = (f.end - f.start).dt.days
    q = {}
    for s, g in f[f.days.between(80, 380)].groupby("start"):
        g = g.sort_values("end")
        prev = 0.0
        prev_end = s - pd.Timedelta(days=1)
        for _, r in g.iterrows():
            if 80 <= (r.end - prev_end).days <= 100:
                q[r.end] = r.val - prev
            prev, prev_end = r.val, r.end
    s = pd.Series(q).sort_index()
    s = s[~s.index.duplicated()]
    return s


def ttm_yoy_monthly(q: pd.Series):
    ttm = q.rolling(4).sum()
    ttm.index = pd.PeriodIndex(ttm.index, freq="M")
    ttm = ttm[~ttm.index.duplicated(keep="last")]
    full = ttm.reindex(pd.period_range(ttm.index.min(), ttm.index.max(), freq="M")).interpolate(limit=3)
    return np.log(full / full.shift(12))


ec = monthly("ecos_monthly.csv")
ks = monthly("kosis_semis_monthly.csv")
yoy = lambda x: np.log(x / x.shift(12))
cyc = yoy(ec.xpi_dram_usd).rolling(3).mean().rename("dram")

mu_cx = capex_quarterly(["MU.json"], ["PaymentsToAcquirePropertyPlantAndEquipment"])
hyp = None
for f, tags in [(["MSFT.json"], ["PaymentsToAcquirePropertyPlantAndEquipment"]),
                (["GOOGL.json", "GOOGL__pred1288776.json"], ["PaymentsToAcquirePropertyPlantAndEquipment"]),
                (["AMZN.json"], ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]),
                (["META.json"], ["PaymentsToAcquirePropertyPlantAndEquipment"])]:
    q = capex_quarterly(f, tags)
    q.index = pd.PeriodIndex(q.index, freq="Q")          # 회계 분기말이 달라도 같은 달력 분기로 합산
    q = q[~q.index.duplicated(keep="last")]
    hyp = q if hyp is None else hyp.add(q, fill_value=np.nan)
hyp = hyp.dropna()
hyp.index = hyp.index.to_timestamp(how="end").normalize()

X = pd.DataFrame({
    "D램 사이클(기준)": cyc,
    "반도체 재고 전년비": yoy(ks.inv_raw).rolling(3).mean(),
    "반도체 출하 전년비": yoy(ks.ship_raw).rolling(3).mean(),
    "출하−재고 갭": (yoy(ks.ship_raw) - yoy(ks.inv_raw)).rolling(3).mean(),
    "마이크론 설비투자 전년비(TTM)": ttm_yoy_monthly(mu_cx),
    "빅테크4 설비투자 전년비(TTM)": ttm_yoy_monthly(hyp),
    "미 10년물 12개월 변화": M.us10y - M.us10y.shift(12),
    "미 10년물 수준": M.us10y,
    "미 3개월물 12개월 변화": M.us3m - M.us3m.shift(12),
    "SOX 전년비": np.log(M.sox / M.sox.shift(12)),
    "삼성 12개월 수익": np.log(M.sam / M.sam.shift(12)),
    "원/달러 전년비": np.log(M.usdkrw / M.usdkrw.shift(12)),
}).loc["1995-01":"2026-08"]


def xcorr(a, b, k):
    """corr(a_t, b_{t-k}); k>0 이면 b 가 k개월 먼저."""
    return a.corr(b.shift(k))


L = ["# 메모리 사이클은 무엇을 따라가나 (탐색)", "",
     "사이클 = D램 수출물가 전년비(3개월 평균). 값 = 상관이 가장 큰 시차(개월, **+면 후보가 먼저**)와 그때 상관. "
     "전년비끼리라 상관이 부풀려지기 쉬움 — 두 시기에서 시차·부호가 같은지가 핵심. 경제 시점 기준(발표 시차 미반영).", "",
     "| 후보 | 기간 | 같은 달 상관 | 최대 상관 시차(−24~+24) | 그때 상관 | 1995~2010 시차/상관 | 2011~2026 시차/상관 |", "|---|---|---|---|---|---|---|"]
a = X["D램 사이클(기준)"]
for c in X.columns[1:]:
    b = X[c]
    ok = pd.concat([a, b], axis=1).dropna()
    if len(ok) < 60:
        continue
    span = f"{ok.index[0]}~{ok.index[-1]}"
    best = max(range(-24, 25), key=lambda k: abs(xcorr(a, b, k)) if not np.isnan(xcorr(a, b, k)) else -1)
    sub = []
    for p, q in [("1995-01", "2010-12"), ("2011-01", "2026-08")]:
        aa, bb = a.loc[p:q], b.loc[p:q]
        if bb.notna().sum() < 48:
            sub.append("–")
            continue
        k = max(range(-24, 25), key=lambda k: abs(aa.corr(bb.shift(k))) if not np.isnan(aa.corr(bb.shift(k))) else -1)
        sub.append(f"{k:+d} / {aa.corr(bb.shift(k)):+.2f}")
    L.append(f"| {c} | {span} | {xcorr(a, b, 0):+.2f} | {best:+d} | {xcorr(a, b, best):+.2f} | {sub[0]} | {sub[1]} |")

# 전환점: D램 사이클 고점·저점 대비 후보 전환점 선후
tp = turning_points(a.dropna(), 12)
# 양 끝 12개월 안 극값은 미확정(창이 잘림) → 제외
tp = [x for x in tp if a.dropna().index[0] + 12 <= x[0] <= a.dropna().index[-1] - 12]
L += ["", "## D램 사이클 전환점 대비 (음수 = 후보가 먼저 꺾임)", "",
      "| D램 | 날짜 | " + " | ".join(X.columns[1:]) + " |", "|" + "---|" * (2 + len(X.columns) - 1)]
stat = {c: [] for c in X.columns[1:]}
tps = {c: turning_points(X[c].dropna(), 12) for c in X.columns[1:]}
for date, kind, _ in tp:
    cells = []
    for c in X.columns[1:]:
        s = X[c].dropna()
        # 재고·금리 수준처럼 사이클과 반대로 움직일 수 있는 것은 같은 달 상관 부호로 방향을 맞춤
        sign = np.sign(xcorr(a, X[c], 0)) or 1
        want = kind if sign > 0 else ("T" if kind == "P" else "P")
        cand = [(d, t) for d, t, _ in tps[c] if t == want and abs((d - date).n) <= 18]
        if not cand or s.index.min() > date - 18:
            cells.append("–")
            continue
        d = min(cand, key=lambda x: abs((x[0] - date).n))[0]
        stat[c].append((d - date).n)
        cells.append(f"{(d - date).n:+d}")
    L.append(f"| {'고점' if kind == 'P' else '저점'} | {date} | " + " | ".join(cells) + " |")
L += ["", "| 후보 | 대응 수 | 먼저 꺾임 | 중앙 시차 | 방향 |", "|---|---|---|---|---|"]
for c, v in stat.items():
    if v:
        v = np.array(v)
        L.append(f"| {c} | {len(v)} | {(v < 0).sum()}/{len(v)} | {np.median(v):+.0f} | {'같은 방향' if xcorr(a, X[c], 0) > 0 else '반대 방향'} |")
L += ["", f"D램 사이클 고점·저점 수: {len(tp)} (1996~2025-08 확정분, 앞뒤 12개월 극값)", "",
      "## 선후 상관 단면 (k>0 = 후보가 k개월 먼저)", "", "| 후보 | k=−12 | −6 | 0 | +6 | +12 | +18 | +24 |", "|---|---|---|---|---|---|---|---|"]
for c in X.columns[1:]:
    L.append(f"| {c} | " + " | ".join(f"{xcorr(a, X[c], k):+.2f}" for k in (-12, -6, 0, 6, 12, 18, 24)) + " |")
L += ["", "## 최신값", "", " · ".join(f"{c} {X[c].dropna().iloc[-1]:+.3f} ({X[c].dropna().index[-1]})" for c in X.columns)]
out = os.path.join(HERE, "results_memory_cycle.md")
open(out, "w").write("\n".join(L) + "\n")
print("\n".join(L))
