import os
import time
import pandas as pd
import numpy as np

def main():
    root = "/home/kuzimo/projects/CTradeSystem"
    uni_path = f"{root}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{root}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    idx_path = f"{root}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{root}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    name_map = dict(zip(uni['code'], uni['name']))
    kospi_codes = set(uni[(uni['market'] == 'KOSPI') & (uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])

    df_idx = pd.read_parquet(idx_path)
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    cal = u180.index

    print(f"Loading daily_ext for {len(kospi_codes)} KOSPI common stocks...")
    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap'],
        filters=[('code', 'in', list(kospi_codes))]
    )

    mcap_pivot = df_ext.pivot(index='date', columns='code', values='market_cap').reindex(cal).ffill()
    mcap_roll = mcap_pivot.rolling(60, min_periods=20).mean()

    # Semi-annual rebalancing dates (early June and early December)
    rebal_dates = []
    prev_period = None
    for d in cal:
        period = (d.year, 0 if d.month <= 6 else 1)
        if prev_period is not None and period != prev_period:
            rebal_dates.append(d)
        prev_period = period

    rebal_dates = [cal[40]] + rebal_dates

    d0 = rebal_dates[0]
    top200_0 = mcap_roll.loc[d0].dropna().sort_values(ascending=False).head(200).index.tolist()
    known_universe = set(top200_0)

    print(f"Initial Top 200 at {d0.strftime('%Y-%m-%d')}: {len(known_universe)}")
    entrants_log = []
    for d in rebal_dates[1:]:
        top200_curr = set(mcap_roll.loc[d].dropna().sort_values(ascending=False).head(200).index.tolist())
        new_in = top200_curr - known_universe
        for s in sorted(list(new_in)):
            entrants_log.append((d, s, name_map.get(s, s)))
            known_universe.add(s)

    df_ent = pd.DataFrame(entrants_log, columns=['date', 'code', 'name'])
    print(f"Total new entrants detected: {len(df_ent)}")
    print("\nEntrants per year:")
    print(df_ent.groupby(df_ent['date'].dt.year).size())
    print("\nSample 25 entrants:")
    for idx, row in df_ent.head(25).iterrows():
        print(f"  {row['date'].strftime('%Y-%m-%d')} | {row['code']} | {row['name']}")

if __name__ == '__main__':
    main()
