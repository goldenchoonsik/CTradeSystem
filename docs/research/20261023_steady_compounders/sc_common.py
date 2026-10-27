"""공용 — 당시 S&P500 명단·Yahoo 티커 대응·총수익 지수.

명단·티커 대응은 docs/research/20261021_random_portfolio_vs_qqq/random_portfolios.py 와 같은 규칙(이름 변경 RENAME, 티커 재사용 REUSED).
가격은 DataVault raw/yahoo 무수정 + 분할(`<` 분할일) 보정 + 배당 재투자(총수익).
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ROOT = os.path.join(REPO, "DataVault")
MEMB = os.path.join(REPO, "docs", "research", "20261021_random_portfolio_vs_qqq", "data_sp500_membership",
                    "sp500_components_updated.csv")
SEC_DIR = os.path.join(ROOT, "staging", "us_fundamentals_sec")
STAGE = os.path.join(ROOT, "staging", "steady_compounders")

RENAME = {'FB': 'META', 'ANTM': 'ELV', 'ABC': 'COR', 'BLL': 'BALL', 'BK': 'BNY', 'MMC': 'MRSH', 'WLTW': 'WTW',
          'TMK': 'GL', 'JEC': 'J', 'CTL': 'LUMN', 'MYL': 'VTRS', 'NLOK': 'GEN', 'SYMC': 'GEN', 'PKI': 'RVTY',
          'RE': 'EG', 'PEAK': 'DOC', 'HCP': 'DOC', 'FLT': 'CPAY', 'BHGE': 'BKR', 'HRS': 'LHX', 'UTX': 'RTX',
          'ARNC': 'HWM', 'DWDP': 'DD', 'GPS': 'GAP', 'HFC': 'DINO', 'FBHS': 'FBIN', 'HCN': 'WELL', 'PX': 'LIN',
          'ACE': 'CB', 'PCLN': 'BKNG', 'KFT': 'MDLZ', 'BBT': 'TFC', 'SBC': 'T'}
REUSED = {'T': pd.Timestamp('2005-11-21'), 'CB': pd.Timestamp('2016-01-15'), 'IR': pd.Timestamp('2020-03-02')}
REUSED_TO = {'IR': 'TT'}


def yahoo_ticker(t, d):
    t = t.replace('.', '-')
    if t in REUSED and d < REUSED[t]:
        return REUSED_TO.get(t)
    return RENAME.get(t, t)


def membership():
    m = pd.read_csv(MEMB, parse_dates=['date'])
    return m.sort_values('date').reset_index(drop=True)


def members_at(memb, d):
    """날짜 d 의 당시 명단 → [(명단 티커, yahoo 티커 or None)]."""
    row = memb[memb.date <= d].iloc[-1]
    return [(t, yahoo_ticker(t, d)) for t in row.tickers.split(',')]


def load_raw(t):
    fp = f"{ROOT}/raw/yahoo/candle_1d/{t}_1d.csv"
    if not os.path.exists(fp):
        return None
    df = pd.read_csv(fp, parse_dates=["timestamp"]).set_index("timestamp")
    df = df[~df.index.duplicated()].sort_index()
    return df


def split_mult(t, idx):
    mult = pd.Series(1.0, index=idx)
    cp = f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv"
    ca = None
    if os.path.exists(cp):
        ca = pd.read_csv(cp, parse_dates=["date"])
        sp = ca[ca.type == "split"].drop_duplicates("date")
        for d, num, den in zip(sp.date, sp.numerator, sp.denominator):
            mult[idx < d] *= num / den
    return mult, ca


def price_tr(t, cal):
    """(분할 보정 종가, 총수익 지수, 달러 거래대금 60일, 무수정 종가, 배당 12개월 합(분할 보정)) — cal 에 맞춤."""
    raw = load_raw(t)
    if raw is None:
        return None
    mult, ca = split_mult(t, raw.index)
    close = raw["close"] / mult
    dvol = (raw["close"] * raw["volume"]).rolling(60, min_periods=20).mean()
    dv = pd.Series(0.0, index=raw.index)
    if ca is not None:
        g = ca[ca.type == "dividend"].groupby("date").amount.sum()
        g = g / mult.reindex(g.index).fillna(1.0)
        dv = g.reindex(raw.index).fillna(0.0)
    c = close.reindex(cal).ffill()
    d = dv.reindex(cal).fillna(0.0)
    prev = c.shift(1)
    rr = ((c + d) / prev).where(prev.notna(), 1.0).fillna(1.0)
    tr = rr.cumprod()
    first = close.first_valid_index()
    tr[tr.index < first] = np.nan
    c[c.index < first] = np.nan
    div12 = d.rolling(252, min_periods=1).sum()
    return pd.DataFrame({"close": c, "tr": tr, "dvol": dvol.reindex(cal).ffill(), "raw_close": raw["close"].reindex(cal).ffill(),
                         "div12": div12, "obs": raw["close"].reindex(cal).notna()})


def sec_env():
    """~/.bashrc 의 SEC_USER_AGENT (값은 출력하지 않는다)."""
    ua = os.environ.get("SEC_USER_AGENT")
    if ua:
        return ua
    try:
        for ln in open(os.path.expanduser("~/.bashrc"), encoding="utf-8"):
            if ln.startswith("export SEC_USER_AGENT="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None
