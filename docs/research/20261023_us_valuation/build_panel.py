"""원천 → 월 패널 (PREREGISTRATION.md '시점 규약' 그대로).

출력 DataVault/staging/us_valuation/derived/panel_monthly.csv (월 = 그 달, 값은 그 달 말에 알려진 것만)
실행: .venv/bin/python -I build_panel.py [--pylib <xlrd 가 있는 폴더>]   (Shiller .xls 읽기에 xlrd 필요)
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
from shared.data_library.paths import data_vault_path  # noqa: E402

STAGE = data_vault_path("staging", "us_valuation")
RAW = os.path.join(STAGE, "raw")
OUT = os.path.join(STAGE, "derived")


def latest(pattern):
    fs = sorted(glob.glob(os.path.join(RAW, pattern)))
    if not fs:
        raise FileNotFoundError(pattern)
    return fs[-1]


def ym_index(years_frac):
    """Shiller 날짜 1871.01 / 2026.1(=10월) → Period[M]."""
    s = pd.Series(years_frac).map(lambda v: f"{v:.2f}")
    return pd.PeriodIndex([f"{x[:4]}-{x[5:7]}" for x in s], freq="M")


def read_shiller():
    fp = latest("shiller/ie_data_*.xls")
    df = pd.read_excel(fp, sheet_name="Data", header=None, engine="xlrd").iloc[8:]
    df = df[pd.to_numeric(df[0], errors="coerce").notna()]
    cols = {0: "date", 1: "P", 2: "D", 3: "E", 4: "CPI", 6: "GS10_sh", 7: "P_real", 8: "D_real",
            9: "TR_real", 10: "E_real", 12: "CAPE_sh", 16: "ECY_sh", 17: "bond_ret_m", 18: "bond_TR_real",
            19: "stk10_real_sh", 20: "bnd10_real_sh", 21: "xs10_real_sh"}
    df = df[list(cols)].rename(columns=cols)
    df.index = ym_index(df.pop("date").astype(float).values)
    return df.apply(pd.to_numeric, errors="coerce"), os.path.basename(fp)


def read_french():
    fp = latest("kenfrench/ff3_monthly_*.csv")
    rows = []
    with open(fp, encoding="utf-8", errors="replace") as f:
        for ln in f:
            p = [x.strip() for x in ln.split(",")]
            if len(p) == 5 and len(p[0]) == 6 and p[0].isdigit():
                rows.append((p[0], float(p[1]), float(p[4])))
            elif "Annual" in ln:
                break
    df = pd.DataFrame(rows, columns=["ym", "mkt_rf", "rf"])
    df.index = pd.PeriodIndex([f"{y[:4]}-{y[4:]}" for y in df.pop("ym")], freq="M")
    return df / 100.0, os.path.basename(fp)


def read_treasury_real():
    fp = latest("treasury/real_yield_*.csv")
    out = []
    with open(fp, encoding="utf-8") as f:
        next(f)
        for ln in f:
            p = ln.strip().split(",")
            if len(p) >= 4 and p[3]:
                out.append((pd.to_datetime(p[0], format="%m/%d/%Y"), float(p[3])))  # 셋째 열 = 10 YR
    s = pd.DataFrame(out, columns=["d", "v"]).drop_duplicates("d").set_index("d")["v"].sort_index()
    return s.groupby(s.index.to_period("M")).mean().rename("tips10"), os.path.basename(fp)


def read_fred(sid):
    fp = latest(f"fred/{sid}_*.csv")
    df = pd.read_csv(fp)
    df.columns = ["d", "v"]
    df["v"] = pd.to_numeric(df["v"], errors="coerce")
    s = df.dropna().set_index(pd.PeriodIndex(pd.to_datetime(df.dropna()["d"]), freq="M"))["v"]
    return s.rename(sid), os.path.basename(fp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pylib")
    a = ap.parse_args()
    if a.pylib:
        sys.path.insert(0, a.pylib)
    sh, f_sh = read_shiller()
    ff, f_ff = read_french()
    tips, f_tp = read_treasury_real()
    baa, f_baa = read_fred("BAA")
    gs10f, f_gs = read_fred("GS10")
    p = sh.copy()
    p = p.join(ff, how="left").join(tips, how="left").join(baa, how="left").join(gs10f.rename("GS10_fred"), how="left")

    # --- 시점 규약 ---
    # 최근 몇 달은 Shiller 이익·배당이 아직 없음(발표 지연) → 마지막 값을 최대 3개월 이어 씀(과거 구간 영향 없음)
    lagE = p["E_real"].shift(3).ffill(limit=3)
    lagD = p["D_real"].shift(3).ffill(limit=3)
    p["E10_pit"] = p["E_real"].rolling(120, min_periods=120).mean().shift(3).ffill(limit=3)
    p["CAPE_pit"] = p["P_real"] / p["E10_pit"]
    p["dp"] = np.log(lagD / p["P_real"])
    p["ep10"] = np.log(p["E10_pit"] / p["P_real"])
    p["ep1"] = np.log(lagE / p["P_real"])
    cpi_l1 = p["CPI"].shift(1)
    p["infl12"] = (cpi_l1 / cpi_l1.shift(12) - 1) * 100
    p["infl10y"] = ((cpi_l1 / cpi_l1.shift(120)) ** (1 / 10) - 1) * 100
    p["infl_vol"] = np.log(cpi_l1).diff().rolling(60, min_periods=60).std() * np.sqrt(12) * 100
    p["GS10"] = p["GS10_sh"]                     # Shiller 장기금리(1953~ = FRED GS10)
    p["real10"] = p["GS10"] - p["infl10y"]
    p["ecy"] = 100 * p["E10_pit"] / p["P_real"] - p["real10"]
    p["tbl"] = p["rf"] * 12 * 100                 # French 1개월 T-bill, 연율 %
    p["term"] = p["GS10"] - p["tbl"]
    p["credit"] = p["BAA"] - p["GS10"]
    p["breakeven"] = p["GS10"] - p["tips10"]
    # 실질 총수익 10년(시작 t, 연율 %) — Shiller TR, 월평균 기준
    p["fwd10_real"] = ((p["TR_real"].shift(-120) / p["TR_real"]) ** (1 / 10) - 1) * 100
    p["mkt"] = p["mkt_rf"] + p["rf"]
    os.makedirs(OUT, exist_ok=True)
    p.index = p.index.astype(str)
    p.index.name = "ym"
    p.to_csv(os.path.join(OUT, "panel_monthly.csv"))
    src = {"shiller": f_sh, "french": f_ff, "treasury": f_tp, "baa": f_baa, "gs10": f_gs}
    pd.Series(src).to_json(os.path.join(OUT, "panel_sources.json"), force_ascii=False, indent=1)
    print("패널", p.index[0], "~", p.index[-1], len(p), "행 | 원천", src)
    # 교차 확인: 1953~ Shiller GS10 vs FRED GS10, 내 CAPE vs Shiller CAPE
    m = p[["GS10_sh", "GS10_fred"]].dropna()
    print("GS10 Shiller−FRED 최대차", round((m.GS10_sh - m.GS10_fred).abs().max(), 3), "n", len(m))
    c = p[["CAPE_pit", "CAPE_sh"]].dropna()
    print("CAPE_pit/Shiller CAPE 비 중앙값", round((c.CAPE_pit / c.CAPE_sh).median(), 3))
    print(p[["P", "CAPE_pit", "CAPE_sh", "dp", "real10", "ecy", "tips10", "breakeven", "credit", "tbl", "mkt"]].tail(6).round(3).to_string())


if __name__ == "__main__":
    main()
