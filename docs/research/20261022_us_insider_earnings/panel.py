"""미국 보통주(Yahoo 현재 상장만) 일별 패널: 총수익률(분할 보정 + 현금배당) · 무수정 종가 · 60일 평균 거래대금.

분할 보정은 us_data.py 정정판과 같은 규칙(분할일 '전' 가격만 나눔). 배당은 권리락일에 무수정 금액/당일 분할계수로 더함.
산출: DataVault/staging/us_sec_bulk/derived/panel_{ret,close,dvol}.parquet (행=날짜 2005-01~, 열=Yahoo 심볼, float32)
"""
import os
from multiprocessing import Pool
import numpy as np
import pandas as pd

BASE = "/home/kuzimo/projects/CTradeSystem/DataVault"
OUT = f"{BASE}/staging/us_sec_bulk/derived"
START = "2005-01-01"


def load(sym):
    fp = f"{BASE}/raw/yahoo/candle_1d/{sym}_1d.csv"
    if not os.path.exists(fp):
        return None
    df = pd.read_csv(fp, usecols=["timestamp", "close", "volume"], parse_dates=["timestamp"]).dropna(subset=["close"])
    df = df[~df.timestamp.duplicated()].set_index("timestamp").sort_index()
    if len(df) < 60 or df.index[-1] < pd.Timestamp(START):
        return None
    mult = pd.Series(1.0, index=df.index)
    div = pd.Series(0.0, index=df.index)
    cp = f"{BASE}/raw/yahoo/corporate_actions/{sym}.csv"
    if os.path.exists(cp):
        ca = pd.read_csv(cp, parse_dates=["date"])
        for d, num, den in ca[ca.type == "split"].drop_duplicates("date")[["date", "numerator", "denominator"]].values:
            if num and den:
                mult[df.index < d] *= num / den
        dv = ca[ca.type == "dividend"].groupby("date").amount.sum()
        dv = dv[dv.index.isin(df.index)]
        div.loc[dv.index] = dv.values
    adj = df.close / mult
    ret = (adj + div / mult) / adj.shift(1) - 1
    dvol = (df.close * df.volume).rolling(60, min_periods=20).mean()
    keep = df.index >= START
    return sym, ret[keep].astype("float32"), df.close[keep].astype("float32"), dvol[keep].astype("float32")


def main():
    u = pd.read_csv(f"{BASE}/staging/us_equity/reference/universe.csv")
    u = u[(u.etf == "N") & u.yahoo_symbol.str.fullmatch(r"[A-Z]+(-[A-Z])?", na=False)
          & ~u.name.str.contains(r"Warrant|Right|Unit|Preferred|Notes|Debenture|%", case=False, na=False)]
    with Pool(5) as p:
        res = [r for r in p.imap_unordered(load, u.yahoo_symbol.tolist(), chunksize=20) if r]
    for i, k in enumerate(["ret", "close", "dvol"], start=1):
        w = pd.DataFrame({r[0]: r[i] for r in res}).sort_index()
        w.to_parquet(f"{OUT}/panel_{k}.parquet")
    print(f"종목 {len(res)} (후보 {len(u)}), 날짜 {w.index[0].date()}~{w.index[-1].date()} {len(w)}일")


if __name__ == "__main__":
    main()
