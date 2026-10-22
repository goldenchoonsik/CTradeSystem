"""삼성전자 월별·주별 패널 — 사전등록(PREREGISTRATION.md) 발표 시차 규칙 그대로.

출력: DataVault/staging/samsung_cycle/derived/{monthly,weekly,daily}.parquet, micron_quarterly.csv
  .venv/bin/python docs/research/20261022_samsung_single/build_panel.py
"""
import json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
from shared.data_library.paths import data_vault_path  # noqa: E402

SC = data_vault_path("staging", "samsung_cycle")
KR = data_vault_path("staging", "kr_equity", "raw", "creon")
OUT = os.path.join(SC, "derived")
LAST_CLEAN = pd.Timestamp("2026-09-11")  # 2026-09-14~ 개별주 종가 = NXT 20:00 통합


def creon_close(code):
    d = pd.read_csv(os.path.join(KR, "candle_1d", f"A{code}_1d.csv"), parse_dates=["timestamp"])
    return d.set_index("timestamp")["upbit_close"].astype(float).rename(code)


def index_close(code):
    d = pd.read_csv(os.path.join(KR, "index_1d", f"{code}_1d.csv"), dtype={"date": str})
    d.index = pd.to_datetime(d["date"])
    return d["close"].astype(float).rename(code)


def yahoo(name, col="adjclose"):
    d = pd.read_csv(os.path.join(SC, f"yahoo_{name}.csv"), parse_dates=["date"])
    return d.set_index("date")[col].astype(float)


def asof_before(s: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """각 한국 날짜 D 에 대해, D 보다 앞선(엄격히 <) 마지막 값 — 미국 종가는 한국 D 장 마감 뒤에 나옴."""
    s = s.dropna().sort_index()
    idx = s.index.searchsorted(dates, side="left") - 1
    v = np.where(idx >= 0, s.values[np.clip(idx, 0, None)], np.nan)
    return pd.Series(v, index=dates)


def asof_at(s: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    s = s.dropna().sort_index()
    idx = s.index.searchsorted(dates, side="right") - 1
    v = np.where(idx >= 0, s.values[np.clip(idx, 0, None)], np.nan)
    return pd.Series(v, index=dates)


def lagged(monthly: pd.Series, months: pd.PeriodIndex, lag: int) -> pd.Series:
    """월말 t 에서 쓸 수 있는 값 = 월(t) − lag 의 값."""
    return pd.Series(monthly.reindex(months - lag).values, index=months)


def micron_quarterly():
    d = json.load(open(data_vault_path("staging", "us_fundamentals_sec", "raw", "companyfacts", "MU.json")))
    g = d["facts"]["us-gaap"]

    def facts(tags):
        rows = []
        for t in tags:
            for x in g.get(t, {}).get("units", {}).get("USD", []):
                if "start" in x:
                    rows.append(dict(start=x["start"], end=x["end"], val=x["val"], filed=x["filed"], tag=t))
        f = pd.DataFrame(rows)
        f["days"] = (pd.to_datetime(f.end) - pd.to_datetime(f.start)).dt.days
        return f

    out = {}
    for name, tags in {"rev": ["SalesRevenueNet", "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
                       "gp": ["GrossProfit"]}.items():
        f = facts(tags)
        q = f[f.days.between(80, 100)].sort_values("filed").groupby("end").first()   # 최초 접수본
        fy = f[f.days.between(350, 380)].sort_values("filed").groupby("end").first()
        q = q[["start", "val", "filed"]].copy()
        # 3개월 값이 이듬해 비교 열로만 올라온 분기(2010 전후) → 같은 회계연도 누적(YTD) 차이로 제때 복원
        ytd = f.sort_values("filed").groupby(["start", "end"]).first().reset_index()
        for _, r in ytd[ytd.days.between(170, 280)].iterrows():
            prev = ytd[(ytd.start == r.start) & (ytd.end < r.end)].sort_values("end")
            if prev.empty:
                continue
            p = prev.iloc[-1]
            if not 80 <= (pd.Timestamp(r.end) - pd.Timestamp(p.end)).days <= 100:
                continue
            if r.end not in q.index or q.loc[r.end, "filed"] > r.filed:
                q.loc[r.end] = dict(start=p.end, val=r.val - p.val, filed=max(r.filed, p.filed))
        # 4분기 = 연간 − 같은 회계연도 앞 3분기 (10-K 접수일)
        for end, r in fy.iterrows():
            if end in q.index:
                continue
            inside = q[(q.index > r.start) & (q.index < end)]
            if len(inside) == 3:
                q.loc[end] = dict(start=None, val=r.val - inside.val.sum(), filed=r.filed)
        out[name] = q.sort_index()
    m = out["rev"][["val", "filed"]].rename(columns={"val": "rev"}).join(
        out["gp"][["val", "filed"]].rename(columns={"val": "gp", "filed": "filed_gp"}), how="inner")
    m["filed"] = pd.to_datetime(m[["filed", "filed_gp"]].max(axis=1))
    m["gm"] = m.gp / m.rev
    m.index = pd.to_datetime(m.index)
    m = m.drop(columns="filed_gp")
    m.to_csv(os.path.join(OUT, "micron_quarterly.csv"), index_label="period_end")
    return m


def main():
    os.makedirs(OUT, exist_ok=True)
    # ---- 일별
    s = creon_close("005930")
    p = creon_close("005935")
    k = index_close("U001")
    ext = pd.read_csv(os.path.join(KR, "daily_ext", "A005930_1d_ext.csv"), dtype={"date": str})
    ext.index = pd.to_datetime(ext.pop("date"))
    days = s.index
    daily = pd.DataFrame({"sam": s, "pref": p.reindex(days), "kospi": k.reindex(days)}, index=days)
    daily["mcap"] = ext["market_cap"].reindex(days).replace(0, np.nan)
    daily["foreign_pct"] = ext["foreign_hold_pct"].reindex(days).replace(0, np.nan)
    daily["foreign_pct_prev"] = daily["foreign_pct"].shift(1)   # t-1 거래일 값(보수적)
    daily["inst_net_qty"] = ext["inst_net_qty"].reindex(days)
    daily["shares"] = ext["shares_listed"].reindex(days).replace(0, np.nan)
    fx = pd.read_csv(os.path.join(SC, "usdkrw_ecos_daily.csv"), parse_dates=["date"]).set_index("date")["usdkrw"]
    daily["usdkrw"] = asof_at(fx, days)
    for nm, col in [("IDX_SOX", "sox"), ("MU", "mu"), ("SMH", "smh"), ("IDX_GSPC", "spx")]:
        daily[col] = asof_before(yahoo(nm), days)
    daily["us10y"] = asof_before(yahoo("IDX_TNX", "close"), days)
    daily["us3m"] = asof_before(yahoo("IDX_IRX", "close"), days)
    daily["fx_hi252"] = daily["usdkrw"].rolling(252, min_periods=200).max()
    daily = daily[daily.index >= "1980-01-01"]
    daily.to_parquet(os.path.join(OUT, "daily.parquet"))

    # ---- 월말 격자 (2026-09-11 까지)
    dd = daily[daily.index <= LAST_CLEAN]
    me = dd.groupby(dd.index.to_period("M")).tail(1).copy()
    me["date"] = me.index
    me.index = me.index.to_period("M")
    mo = me.index

    for h in (3, 6, 12):
        me[f"r{h}"] = np.log(me.sam.shift(-h) / me.sam)
        me[f"rk{h}"] = np.log(me.kospi.shift(-h) / me.kospi)
        me[f"x{h}"] = me[f"r{h}"] - me[f"rk{h}"]
        me[f"rp{h}"] = np.log(me.pref.shift(-h) / me.pref) - me[f"r{h}"]   # 우선주 − 보통주
    me["r1"] = np.log(me.sam.shift(-1) / me.sam)
    me["mom12_1"] = np.log(me.sam.shift(1) / me.sam.shift(12))

    # ---- ECOS / KOSIS / 관세청
    ec = pd.read_csv(os.path.join(SC, "ecos_monthly.csv"), dtype={"ym": str})
    ec.index = pd.PeriodIndex(pd.to_datetime(ec.pop("ym"), format="%Y%m"), freq="M")
    ks = pd.read_csv(os.path.join(SC, "kosis_semis_monthly.csv"), dtype={"ym": str})
    ks.index = pd.PeriodIndex(pd.to_datetime(ks.pop("ym"), format="%Y%m"), freq="M")
    cu = pd.read_csv(os.path.join(SC, "customs_854232_monthly.csv"), dtype={"ym": str})
    cu.index = pd.PeriodIndex(pd.to_datetime(cu.pop("ym"), format="%Y%m"), freq="M")

    def yoy(x):
        return np.log(x / x.shift(12))

    # H1, H1b: m* = t−1
    for nm, col in [("H1", "xpi_dram_usd"), ("H1b", "xpi_flash_usd")]:
        y = yoy(ec[col])
        me[nm] = lagged(y - y.shift(3), mo, 1)
        me[f"lvl_{col}_yoy"] = lagged(y, mo, 1)
    # H2: m* = t−2
    gap = (yoy(ks.ship_raw) - yoy(ks.inv_raw)).rolling(3).mean()
    me["H2"] = lagged(gap, mo, 2)
    me["lvl_ship_yoy"] = lagged(yoy(ks.ship_raw), mo, 2)
    me["lvl_inv_yoy"] = lagged(yoy(ks.inv_raw), mo, 2)
    # H3: m* = t−1, 2007-01 이전 없음
    e = cu.exp_usd_k.astype(float)
    s3 = e.rolling(3).sum()
    g = np.log(s3 / s3.shift(12))
    me["H3"] = lagged(g - g.shift(3), mo, 1)
    me["lvl_mem_export_yoy"] = lagged(g, mo, 1)
    # H4: 마이크론 GM 변화 — 접수일 다음 거래일 이후 첫 월말
    mu = micron_quarterly()
    # 2010 전후 분기는 XBRL 에 이듬해 비교 열로만 있음(접수 404일 뒤) → 제때 알 수 없던 값이라 버림.
    # 변화량은 바로 앞 분기(약 91일 전)와만, 두 분기 모두 접수된 뒤에 사용(미래참조 차단).
    mu = mu[(mu.filed - mu.index).dt.days <= 100].sort_index()
    gap = mu.index.to_series().diff().dt.days
    mu["dgm"] = np.where(gap.between(80, 100), mu.gm.diff(), np.nan)
    mu["filed"] = pd.concat([mu.filed, mu.filed.shift(1)], axis=1).max(axis=1)
    mu = mu.sort_values("filed")
    avail = pd.Series(mu.dgm.values, index=mu.filed + pd.Timedelta(days=1))
    me["H4"] = asof_at(avail, pd.DatetimeIndex(me.date)).values
    me["lvl_mu_gm"] = asof_at(pd.Series(mu.gm.values, index=mu.filed + pd.Timedelta(days=1)), pd.DatetimeIndex(me.date)).values
    # H5
    rel = np.log(me.sam) - np.log(me.sox * me.usdkrw)
    me["rel_sox"] = rel
    me["H5"] = -(rel - rel.shift(6))
    # H6
    me["H6"] = np.log(me.usdkrw / me.fx_hi252)
    # H7, H7b
    me["H7"] = me.us10y - me.us10y.shift(6)
    kr10 = ec.kr_tb10y
    me["H7b"] = lagged(kr10 - kr10.shift(6), mo, 1)
    # H8, H8b: DART 접수일 기준
    f = pd.read_parquet(data_vault_path("staging", "kr_equity", "derived", "fundamentals_q.parquet"))
    f = f[f.stock_code == "005930"].copy()
    f["rcept_dt"] = pd.to_datetime(f.rcept_dt.astype(str))
    f = f.sort_values("period_end")
    f["ni_ttm"] = f.net_income.rolling(4).sum()
    f["op_ttm"] = f.op_income.rolling(4).sum()
    f = f.sort_values("rcept_dt")
    dts = pd.DatetimeIndex(me.date)
    eq = asof_at(pd.Series(f.equity.values, index=f.rcept_dt), dts).values
    ni = asof_at(pd.Series(f.ni_ttm.values, index=f.rcept_dt), dts).values
    me["equity"] = eq
    me["ni_ttm"] = ni
    me["H8"] = me.mcap / me.equity
    me["H8b"] = np.where(me.ni_ttm > 0, me.mcap / me.ni_ttm, np.nan)
    # H9: 외국인 보유율 직전 60개월 백분위 (현재 포함)
    fp = me.foreign_pct_prev
    me["H9"] = fp.rolling(60, min_periods=60).apply(lambda a: (a <= a[-1]).mean(), raw=True)
    # H10: 우선주 할인율 z
    disc = 1 - me.pref / me.sam
    me["pref_disc"] = disc
    mu36 = disc.shift(1).rolling(36, min_periods=36).mean()
    sd36 = disc.shift(1).rolling(36, min_periods=36).std()
    me["H10"] = (disc - mu36) / sd36

    me.to_parquet(os.path.join(OUT, "monthly.parquet"))

    # ---- 주별 (금요일 기준 마지막 거래일) — A 동행 분해용
    wk = dd.groupby(dd.index.to_period("W-FRI")).tail(1)
    wk.to_parquet(os.path.join(OUT, "weekly.parquet"))

    cov = {c: (str(me[c].first_valid_index()), str(me[c].last_valid_index()), int(me[c].notna().sum()))
           for c in ["sam", "r6", "H1", "H1b", "H2", "H3", "H4", "H5", "H6", "H7", "H7b", "H8", "H8b", "H9", "H10"]}
    for c, v in cov.items():
        print(f"{c:5s} {v}")
    json.dump(cov, open(os.path.join(OUT, "coverage.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
