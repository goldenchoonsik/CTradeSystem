"""미국 Yahoo 원천 로더 (분할 보정 정정판)

원본(20261018_qqq_replication/max_tenure_hybrid_backtest.py, 20261021_us_titan_tax_drag_swing)은
`mult.loc[df.index <= 분할일]` 로 분할 당일(권리락일) 가격까지 한 번 더 나눠 분할일마다 −90%/+900% 가짜 봉이 생겼다.
여기서는 `< 분할일` 만 나눈다. 배당은 반영하지 않는다(가격 기준).
"""
import os
import pandas as pd

ROOT = "/home/kuzimo/projects/CTradeSystem/DataVault"


def load(t):
    fp = f"{ROOT}/raw/yahoo/candle_1d/{t}_1d.csv"
    if not os.path.exists(fp):
        return None
    df = pd.read_csv(fp, parse_dates=["timestamp"]).set_index("timestamp")
    df = df[~df.index.duplicated()].sort_index()
    raw_close = df['close'].copy()
    cp = f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv"
    mult = pd.Series(1.0, index=df.index)
    if os.path.exists(cp):
        ca = pd.read_csv(cp, parse_dates=["date"])
        ca = ca[ca.type == "split"].drop_duplicates("date")
        for d, num, den in zip(ca.date, ca.numerator, ca.denominator):
            mult[df.index < d] *= num / den
    for k in ('open', 'high', 'low', 'close'):
        df[k] = df[k] / mult
    df['volume'] = df['volume'] * mult
    df['dvol_60d'] = (raw_close * df['volume'] / mult).rolling(60, min_periods=20).mean()   # 달러 거래대금(분할 무관)
    return df
