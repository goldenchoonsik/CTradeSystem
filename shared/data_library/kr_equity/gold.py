"""한국 일봉 골드 — 머신러닝과 룰 기반이 함께 쓰는 종목×일자 지표 표 (실버 prices/ext/fundamentals_q 위).

  derived/gold_1d.parquet         code·date + 지표 — 모두 '그날 종가 시점에 알 수 있는 값'만 (date 이하 자료로만 계산)
  derived/gold_labels_1d.parquet  code·date + 미래 수익 라벨 — 지표와 분리해 둔다(특징 행렬에 섞이는 사고 방지)

규약
- 지표 t = t 종가까지의 정보. 체결은 t+1 종가를 가정하므로 라벨은 log(close[t+1+h] / close[t+1]) (h = 1·5·20 봉).
- 수정 종가(원천 무수정 × 원장 계수)로 수익률을 만든다. 가격 '수준'(close_unadj)은 호가·필터용으로만 따로 둔다.
- 거래정지로 20거래일 넘게 봉이 없다가 재개한 날은 수익률 계산에서 제외(체결 불가한 점프) — resumed=1 로 표시, 그 봉이 낀 창은 NaN.
- 재무는 접수일(rcept_dt) 이후에만 결합(연결 우선, 없으면 별도). 4개 분기가 연속일 때만 TTM.
- 시총 근사값(상폐 종목, mcap_approx=1)은 그대로 표시해 둔다 — 규모·가치 지표는 근사일 수 있다.
"""
import os
from typing import Optional

import numpy as np
import pandas as pd

from . import panel
from .store import log

GAP_CAL_DAYS = 28                  # 봉 사이가 달력일로 이만큼 넘으면 거래정지 후 재개로 본다 (거래일 20일 이상 공백에 해당)
RET_WINDOWS = (1, 5, 20, 60, 120, 250)
LABEL_H = (1, 5, 20)


def gold_path() -> str:
    return panel.derived("gold_1d.parquet")


def labels_path() -> str:
    return panel.derived("gold_labels_1d.parquet")


def _ttm(f: pd.DataFrame) -> pd.DataFrame:
    """분기 재무 → 종목별 (rcept_dt 기준 알려진) TTM·잔액. 연결 우선."""
    f = f.copy()
    f["code"] = "A" + f.stock_code.astype(str)
    f["cfs"] = (f.fs_div == "CFS").astype(int)
    f = f.sort_values(["code", "period_end", "cfs", "rcept_dt"]).drop_duplicates(["code", "period_end"], keep="last")
    f = f.sort_values(["code", "period_end"])
    q = pd.PeriodIndex(f.period_end, freq="Q")
    f["_q"] = q.year * 4 + q.quarter
    g = f.groupby("code")
    consecutive = (g["_q"].diff(1).eq(1) & g["_q"].diff(2).eq(2) & g["_q"].diff(3).eq(3))
    for src, dst in (("sales", "sales_ttm"), ("op_income", "op_ttm"), ("net_income", "ni_ttm")):
        f[dst] = g[src].transform(lambda s: s.rolling(4, min_periods=4).sum()).where(consecutive)
    out = f[["code", "rcept_dt", "sales_ttm", "op_ttm", "ni_ttm", "assets", "equity", "liabilities"]]
    return out.dropna(subset=["rcept_dt"]).sort_values("rcept_dt")


def build_features(prices: pd.DataFrame, ext: Optional[pd.DataFrame] = None, fin: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    p = prices.copy()
    p["code"] = p.code.astype(str)
    p = p.sort_values(["code", "date"]).reset_index(drop=True)
    g = p.groupby("code", sort=False)
    gap = g["date"].diff().dt.days
    p["resumed"] = (gap > GAP_CAL_DAYS).astype("int8")
    lc = np.log(p.close.astype("float64"))
    r1 = lc.groupby(p.code, sort=False).diff()
    r1 = r1.where(p.resumed == 0)                                   # 재개일 점프 제외
    p["ret_1"] = r1
    # 창 수익률: 창 안에 재개일이 끼면 NaN (누적 재개 횟수가 같을 때만 유효)
    epoch = p.resumed.groupby(p.code, sort=False).cumsum()
    for w in RET_WINDOWS[1:]:
        past = lc.groupby(p.code, sort=False).shift(w)
        same = epoch.groupby(p.code, sort=False).shift(w) == epoch
        p[f"ret_{w}"] = (lc - past).where(same)
    p["mom_250_20"] = (p["ret_250"] - p["ret_20"])
    for w in (20, 60):
        p[f"vol_{w}"] = r1.groupby(p.code, sort=False).rolling(w, min_periods=int(w * 0.8)).std().reset_index(level=0, drop=True)
    p["max5_20"] = r1.groupby(p.code, sort=False).rolling(20, min_periods=16).apply(lambda x: np.sort(x)[-5:].mean(), raw=True).reset_index(level=0, drop=True)
    hi = p.close.groupby(p.code, sort=False).rolling(250, min_periods=120).max().reset_index(level=0, drop=True)
    p["hi_250_ratio"] = p.close / hi
    v = p.volume.astype("float64")
    ma = v.groupby(p.code, sort=False).rolling(20, min_periods=16).mean().reset_index(level=0, drop=True)
    p["vol_ratio_20"] = (v / ma.groupby(p.code, sort=False).shift(1)).replace([np.inf, -np.inf], np.nan)   # 전날까지의 20일 평균 대비
    p["adv_20"] = p.value.astype("float64").groupby(p.code, sort=False).rolling(20, min_periods=16).mean().reset_index(level=0, drop=True)
    if ext is not None:
        e = ext.copy()
        e["code"] = e.code.astype(str)
        e = e.sort_values(["code", "date"]).drop_duplicates(["code", "date"])
        e["d_foreign_pct_20"] = e.groupby("code", sort=False)["foreign_hold_pct"].diff(20)
        p = p.merge(e[["code", "date", "market_cap", "shares_listed", "foreign_hold_pct", "d_foreign_pct_20", "mcap_approx"]], on=["code", "date"], how="left")
        p["log_mcap"] = np.log(p.market_cap.where(p.market_cap > 0))
        p["turnover_20"] = (v.groupby(p.code, sort=False).rolling(20, min_periods=16).mean().reset_index(level=0, drop=True) / p.shares_listed)
    if fin is not None and ext is not None:
        t = _ttm(fin)
        p = p.sort_values("date")
        p = pd.merge_asof(p, t.rename(columns={"rcept_dt": "date"}).sort_values("date"), on="date", by="code", direction="backward")
        mc = p.market_cap.where(p.market_cap > 0)
        p["bm"] = p.equity / mc
        p["ep"] = p.ni_ttm / mc
        p["opa"] = p.op_ttm / p.assets
        p["sales_to_mcap"] = p.sales_ttm / mc
        p = p.sort_values(["code", "date"]).reset_index(drop=True)
    keep = ["code", "date", "reg_fix", "resumed", "close_unadj"] + [c for c in p.columns if c.startswith(("ret_", "vol_", "mom_", "max5", "hi_", "adv_", "turnover", "log_mcap", "foreign", "d_foreign", "bm", "ep", "opa", "sales_to", "mcap_approx", "market_cap"))]
    out = p[keep].copy()
    for c in out.columns:
        if out[c].dtype == "float64":
            out[c] = out[c].astype("float32")
    out["code"] = out.code.astype("category")
    return out


def build_labels(prices: pd.DataFrame) -> pd.DataFrame:
    """라벨 = log(close[t+1+h] / close[t+1]). 그 사이에 거래정지 재개가 끼면 NaN."""
    p = prices[["code", "date", "close"]].copy()
    p["code"] = p.code.astype(str)
    p = p.sort_values(["code", "date"]).reset_index(drop=True)
    g = p.groupby("code", sort=False)
    resumed = (g["date"].diff().dt.days > GAP_CAL_DAYS).astype(int)
    epoch = resumed.groupby(p.code, sort=False).cumsum()
    lc = np.log(p.close.astype("float64"))
    entry = lc.groupby(p.code, sort=False).shift(-1)
    e_epoch = epoch.groupby(p.code, sort=False).shift(-1)
    out = p[["code", "date"]].copy()
    for h in LABEL_H:
        fut = lc.groupby(p.code, sort=False).shift(-(1 + h))
        same = epoch.groupby(p.code, sort=False).shift(-(1 + h)) == e_epoch
        out[f"lab_ret_{h}"] = (fut - entry).where(same).astype("float32")
    out["code"] = out.code.astype("category")
    return out


CHUNK = 300                                      # 한 번에 처리하는 종목 수 (전체를 한꺼번에 굴리면 WSL 메모리 한도를 넘는다)


def build_all() -> dict:
    prices_fp, ext_fp = panel.derived("prices_1d.parquet"), panel.derived("daily_ext.parquet")
    fin = pd.read_parquet(panel.derived("fundamentals_q.parquet"))
    codes = sorted(pd.read_parquet(prices_fp, columns=["code"]).code.astype(str).unique())
    feats, labs = [], []
    for i in range(0, len(codes), CHUNK):
        part = codes[i:i + CHUNK]
        pr = pd.read_parquet(prices_fp, filters=[("code", "in", part)])
        ex = pd.read_parquet(ext_fp, filters=[("code", "in", part)])
        feats.append(build_features(pr, ex, fin))
        labs.append(build_labels(pr))
        log(f"[gold] {min(i + CHUNK, len(codes))}/{len(codes)} 종목", "build_panel.txt")
    g = pd.concat(feats, ignore_index=True)
    g["code"] = g.code.astype(str).astype("category")
    g.to_parquet(gold_path(), index=False)
    lab = pd.concat(labs, ignore_index=True)
    lab["code"] = lab.code.astype(str).astype("category")
    lab.to_parquet(labels_path(), index=False)
    log(f"[gold] 지표 {len(g):,}행 {g.shape[1]}열, 라벨 {len(lab):,}행", "build_panel.txt")
    return {"rows": len(g), "cols": list(g.columns)}
