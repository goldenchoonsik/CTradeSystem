"""PER 밴드 매매(저PER 매수·고PER 매도) — 코스피/코스닥 시총 상위 20 탐색 시험 (10-14, 사용자 질문).

PER = 시총 / 직전 4분기 순이익 합(접수일 다음 날부터 사용). 적자 = PER 없음 → 보유 중이면 매도, 신규 매수 금지.
신호 t 종가 → t+1 종가 체결 → t+2 수익부터 반영. 왕복 비용 0.30%(매수 0.05%, 매도 0.25%). 현금 0%.
유니버스 A: 오늘 시총 상위 20(사후 선택 편향). B: 매년 첫 거래일 시점 상위 20(상폐 포함, 해마다 교체).
"""
import sys
import numpy as np
import pandas as pd

ROOT = "/home/kuzimo/projects/CTradeSystem/DataVault/staging/kr_equity"
OUT = "/home/kuzimo/projects/CTradeSystem/docs/research/20261014_per_band"
START, END = pd.Timestamp("2016-06-01"), pd.Timestamp("2026-09-11")  # 09-14~ 종가 시간외 문제로 제외
BUY_COST, SELL_COST = 0.0005, 0.0025

# ---------- 종목 메타 ----------
lst = pd.read_csv(f"{ROOT}/reference/listed_snapshot.csv", dtype=str)
lst = lst[(lst.section == "STOCK") & (lst.is_preferred == "0") & (lst.is_spac == "0")]
dl = pd.read_csv(f"{ROOT}/reference/delisted.csv", dtype=str)
dl = dl[dl.market.isin(["KOSPI", "KOSDAQ"]) & dl.code.str[-1].eq("0") & ~dl.name.str.contains("스팩|기업인수목적")]
meta = pd.concat([lst[["code", "name", "market"]],
                  dl.assign(code="A" + dl.code)[["code", "name", "market"]]]).drop_duplicates("code")
meta = meta[~meta.code.str[1].eq("9")]  # 외국기업(재무 통화 다름) 제외
MKT = meta.set_index("code").market
NAME = meta.set_index("code").name

# ---------- 시총 / 가격 ----------
ext = pd.read_parquet(f"{ROOT}/derived/daily_ext.parquet", columns=["code", "date", "market_cap", "mcap_approx"])
ext["code"] = ext.code.astype(str)
ext = ext[ext.code.isin(meta.code) & ext.date.between(START - pd.Timedelta(days=10), END)]
mcap = ext.pivot_table(index="date", columns="code", values="market_cap")
approx = ext.pivot_table(index="date", columns="code", values="mcap_approx")

px = pd.read_parquet(f"{ROOT}/derived/prices_1d.parquet", columns=["code", "date", "close"])
px["code"] = px.code.astype(str)
px = px[px.code.isin(mcap.columns) & px.date.between(START - pd.Timedelta(days=10), END)]
close = px.pivot_table(index="date", columns="code", values="close")
cal = close.index[(close.index >= START) & (close.index <= END)]
close = close.reindex(close.index[close.index <= END])
ret = close.pct_change(fill_method=None).reindex(cal)
ret = ret.where(ret.abs() < 0.35)  # 상하한 30% 밖 = 거래정지 후 재개 점프 등 → 제외
mcap = mcap.reindex(cal).ffill(limit=5)
approx = approx.reindex(cal).ffill(limit=5).fillna(0).astype(bool)

# ---------- TTM 순이익 (접수일 기준 as-of) ----------
f = pd.read_parquet(f"{ROOT}/derived/fundamentals_q.parquet",
                    columns=["stock_code", "period_end", "rcept_dt", "net_income"])
f["code"] = "A" + f.stock_code
f = f[f.code.isin(mcap.columns)].sort_values(["code", "period_end"])
g = f.groupby("code")
f["ttm"] = g.net_income.transform(lambda s: s.rolling(4).sum())
f["span"] = (f.period_end - g.period_end.shift(3)).dt.days  # 연속 4분기 = 약 273일
f["avail"] = g.rcept_dt.cummax()  # 정정본으로 늦어진 과거 분기도 반영(보수적)
f = f[f.span.between(260, 290) & f.ttm.notna()]
ttm = pd.DataFrame(index=cal, columns=mcap.columns, dtype=float)
for c, d in f.groupby("code"):
    d = d.sort_values("avail").drop_duplicates("avail", keep="last")
    s = pd.Series(d.ttm.values, index=d.avail.values)
    pe = pd.Series(d.period_end.values, index=d.avail.values)
    a = s.reindex(cal.union(s.index)).ffill().shift(1).reindex(cal)       # 접수 다음 날부터
    p = pe.reindex(cal.union(pe.index)).ffill().shift(1).reindex(cal)
    stale = (cal.to_series().values - pd.to_datetime(p.values)) > np.timedelta64(200, "D")  # 최신 분기 너무 오래됨
    ttm[c] = np.where(stale, np.nan, a.values)
# 근사 시총(상폐, DART 주식수×네이버 수정종가) 결함: 감자 이력 종목은 수십~수백 배 부풀려짐 → PBR>15 이거나 자본 미상이면 무효

eqf = pd.read_parquet(f"{ROOT}/derived/fundamentals_q.parquet", columns=["stock_code", "rcept_dt", "equity"]).dropna()
eqf["code"] = "A" + eqf.stock_code
equity = pd.DataFrame(index=cal, columns=mcap.columns, dtype=float)
for c, d in eqf[eqf.code.isin(mcap.columns)].groupby("code"):
    d = d.sort_values("rcept_dt").drop_duplicates("rcept_dt", keep="last")
    sr = pd.Series(d.equity.values, index=d.rcept_dt.values)
    equity[c] = sr.reindex(cal.union(sr.index)).ffill().shift(1).reindex(cal).values
bad = approx & ~((equity > 0) & (mcap / equity <= 15))
# 감자(주식 수 30% 초과 감소) 이력이 있는 근사 시총 종목은 전 기간 무효 — 수정종가×당시 주식수 불일치의 근원
sh = pd.read_parquet(f"{ROOT}/derived/daily_ext.parquet", columns=["code", "date", "shares_listed", "mcap_approx"])
sh = sh[sh.mcap_approx == 1]; sh["code"] = sh.code.astype(str)
cut = sh.sort_values("date").groupby("code").shares_listed.apply(lambda x: (x / x.cummax()).min() < 0.7)
bad |= approx & pd.DataFrame(np.broadcast_to(bad.columns.isin(cut[cut].index), bad.shape), index=bad.index, columns=bad.columns)
print("근사 시총 무효 처리 종목:", sorted(NAME.get(c, c) for c in bad.columns[bad.any()])[:60], file=sys.stderr)
mcap = mcap.mask(bad)
PER = mcap / ttm.where(ttm > 0)          # 흑자일 때만
LOSS = ttm <= 0                           # 적자 확인됨

# ---------- 유니버스 ----------
def top20_now(m):
    last = mcap.ffill(limit=5).iloc[-1]
    last = last[last.index.isin(lst.code)]  # 현재 상장 종목만
    c = last[last.index.map(MKT).values == m].nlargest(20).index
    print(m, "오늘상위20", ",".join(NAME.get(k, k) for k in c), file=sys.stderr)
    return pd.DataFrame(True, index=cal, columns=c)

def top20_pit(m):
    elig = pd.DataFrame(False, index=cal, columns=mcap.columns)
    for y in sorted(set(cal.year)):
        days = cal[cal.year == y]
        first = mcap.loc[days[0]]
        c = first[first.index.map(MKT).values == m].dropna().nlargest(20).index
        elig.loc[days, c] = True
        print(m, y, ",".join(NAME.get(k, k) for k in c), file=sys.stderr)
    return elig.loc[:, elig.any()]

# ---------- 신호·성과 ----------
def positions(sig_buy, sig_sell, elig):
    """상태기계: 매수 신호 → 1, 매도 신호/적자/유니버스 이탈 → 0. 결측은 상태 유지."""
    pos = np.zeros(sig_buy.shape)
    b, s, e = sig_buy.values, sig_sell.values, elig.values
    st = np.zeros(b.shape[1])
    for i in range(b.shape[0]):
        st = np.where(~e[i], 0, np.where(s[i], 0, np.where(b[i], 1, st)))
        pos[i] = st
    return pd.DataFrame(pos, index=sig_buy.index, columns=sig_buy.columns)

def run(buy, sell, elig):
    cols = elig.columns
    r = ret[cols]
    pos = positions(buy[cols].fillna(False), sell[cols].fillna(False), elig)
    exe = pos.shift(1).fillna(0)          # t 신호 → t+1 종가 체결 상태
    hold = exe.shift(1).fillna(0)         # t+2 수익 노출
    dpos = exe.diff().fillna(exe)
    cost = (dpos.clip(lower=0) * BUY_COST + (-dpos).clip(lower=0) * SELL_COST)
    live = elig.shift(1).fillna(False) & r.notna()
    n = elig.sum(axis=1).clip(lower=1)    # 슬롯 = 유니버스 크기, 빈 슬롯은 현금
    strat = ((hold * r.fillna(0) - cost) * elig).sum(axis=1) / n
    bh = (r.where(live)).mean(axis=1).fillna(0)
    expo = (hold * elig).sum(axis=1) / n
    trades = (dpos.abs() > 0).sum().sum() / 2
    return strat, bh, expo, trades, hold, r

def stats(x):
    eq = (1 + x).cumprod()
    yrs = len(x) / 252
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    vol = x.std() * np.sqrt(252)
    mdd = (eq / eq.cummax() - 1).min()
    return dict(CAGR=cagr, Vol=vol, Sharpe=x.mean() / x.std() * np.sqrt(252), MDD=mdd, Total=eq.iloc[-1] - 1)

# 상대 기준: 자기 PER 과거 3년 분위(오늘 값 포함 안 함)
def pct_rank(P, win=756, minp=250):
    hist = P.shift(1)
    out = pd.DataFrame(index=P.index, columns=P.columns, dtype=float)
    for c in P.columns:
        h = hist[c].values; v = P[c].values
        res = np.full(len(v), np.nan)
        for i in range(len(v)):
            if np.isnan(v[i]): continue
            w = h[max(0, i - win):i]; w = w[~np.isnan(w)]
            if len(w) >= minp: res[i] = (w < v[i]).mean()
        out[c] = res
    return out

rows, yearly, per_stock = [], [], []
for m in ["KOSPI", "KOSDAQ"]:
    for uname, elig in [("A_오늘상위20", top20_now(m)), ("B_시점상위20", top20_pit(m))]:
        cols = elig.columns
        P, L = PER[cols], LOSS[cols].fillna(False)
        R = pct_rank(P)
        rules = []
        for lo, hi in [(8, 15), (10, 20), (12, 25), (15, 30), (20, 40), (30, 60)]:
            rules.append((f"절대 PER≤{lo} 매수 / ≥{hi} 매도", P <= lo, (P >= hi) | L))
        for lo, hi in [(0.2, 0.8), (0.3, 0.7)]:
            rules.append((f"자기3년분위 ≤{int(lo*100)}% 매수 / ≥{int(hi*100)}% 매도", R <= lo, (R >= hi) | L))
        first = True
        for name, b, s in rules:
            strat, bh, expo, ntr, hold, r = run(b, s, elig)
            if first:
                rows.append(dict(market=m, universe=uname, rule="단순보유(동일가중)", **stats(bh), exposure=1.0, trades=0))
                yb = bh.groupby(bh.index.year).apply(lambda x: (1 + x).prod() - 1)
                first = False
            rows.append(dict(market=m, universe=uname, rule=name, **stats(strat), exposure=expo.mean(), trades=ntr))
            ys = strat.groupby(strat.index.year).apply(lambda x: (1 + x).prod() - 1)
            for y in ys.index:
                yearly.append(dict(market=m, universe=uname, rule=name, year=y, strat=ys[y], bh=yb[y]))
            if uname.startswith("A") and name.startswith("자기3년분위 ≤20%") or (uname.startswith("A") and name.startswith("절대 PER≤10 ")):
                for c in cols:
                    rc = r[c].fillna(0)
                    per_stock.append(dict(market=m, rule=name, code=c, name=NAME.get(c, c),
                                          bh=(1 + rc).prod() - 1, strat=(1 + hold[c] * rc).prod() - 1,
                                          in_mkt=hold[c].mean(), per_now=P[c].dropna().iloc[-1] if P[c].notna().any() else np.nan,
                                          loss_share=L[c].mean(), per_median=P[c].median()))
        print(m, uname, "done", file=sys.stderr)

res = pd.DataFrame(rows); res.to_csv(f"{OUT}/summary.csv", index=False)
pd.DataFrame(yearly).to_csv(f"{OUT}/yearly.csv", index=False)
pd.DataFrame(per_stock).to_csv(f"{OUT}/per_stock.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 40)
print(res.round(3).to_string())

# ---------- 점검: 연도별 / 지수 대조 / 보유·미보유 시 수익 차이 ----------
idx = pd.read_parquet(f"{ROOT}/derived/index_1d.parquet")
print(idx.columns.tolist(), idx.code.astype(str).unique()[:10] if "code" in idx else "")
yd = pd.DataFrame(yearly)
for (m, u), d in yd[yd.rule.str.startswith("절대 PER≤10 ")].groupby(["market", "universe"]):
    print(m, u); print(d.set_index("year")[["bh", "strat"]].round(3).T.to_string())

print("\n보유일 vs 미보유일 평균 일수익(같은 유니버스·같은 종목, 연율 %)  — 차이 t값")
for m in ["KOSPI", "KOSDAQ"]:
    for uname, elig in [("A", top20_now(m)), ("B", top20_pit(m))]:
        cols = elig.columns; P, L = PER[cols], LOSS[cols].fillna(False); R = pct_rank(P)
        for name, b, s in [("PER10/20", P <= 10, (P >= 20) | L), ("PER15/30", P <= 15, (P >= 30) | L),
                           ("분위20/80", R <= 0.2, (R >= 0.8) | L)]:
            _, _, _, _, hold, r = run(b, s, elig)
            e = elig.shift(1).fillna(False) & r.notna()
            # 일별 횡단면: 보유 종목 평균 - 미보유 종목 평균 (둘 다 있는 날)
            hin = r.where(e & (hold == 1)).mean(axis=1); hout = r.where(e & (hold == 0)).mean(axis=1)
            dd = (hin - hout).dropna()
            t = dd.mean() / dd.std() * np.sqrt(len(dd))
            for a, z in [("2016", "2020"), ("2021", "2023"), ("2024", "2026")]:
                q = dd[a:z]; print(f"   {a}~{z}: 차이 {q.mean()*252*100:6.1f}%  t={q.mean()/q.std()*np.sqrt(len(q)):5.2f}")
            print(f"{m} {uname} {name}: 보유 {hin.mean()*252*100:6.1f}%  미보유 {hout.mean()*252*100:6.1f}%  차이 {dd.mean()*252*100:6.1f}%  t={t:5.2f}  (n일 {len(dd)})")
