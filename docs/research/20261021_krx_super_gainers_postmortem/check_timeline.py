import pandas as pd
import numpy as np

px_path = '/home/kuzimo/projects/CTradeSystem/DataVault/staging/kr_equity/derived/prices_1d.parquet'
df_p = pd.read_parquet(px_path, columns=['code', 'date', 'close'], filters=[('code', 'in', ['A086520', 'A022100', 'A019170', 'A011200'])])
adj_mat = df_p.pivot(index='date', columns='code', values='close').sort_index()

p_eco = adj_mat['A086520']
print("=== 에코프로 (편입일 2023-08-31) 타임라인 주가 ===")
dates_eco = ['2023-01-02', '2023-03-31', '2023-05-31', '2023-06-30', '2023-07-26', '2023-08-11', '2023-08-31', '2023-10-31', '2023-12-28', '2024-06-28']
for d_str in dates_eco:
    d = pd.Timestamp(d_str)
    sub = p_eco.loc[p_eco.index >= d]
    if not sub.empty:
        curr_d = sub.index[0]
        print(f"{curr_d.strftime('%Y-%m-%d')}: {p_eco.loc[curr_d]:,.0f}원")

p_pdx = adj_mat['A022100']
print("\n=== 포스코DX (편입일 2023-11-30) 타임라인 주가 ===")
dates_pdx = ['2023-01-02', '2023-06-30', '2023-08-31', '2023-10-31', '2023-11-30', '2024-01-31', '2024-05-31']
for d_str in dates_pdx:
    d = pd.Timestamp(d_str)
    sub = p_pdx.loc[p_pdx.index >= d]
    if not sub.empty:
        curr_d = sub.index[0]
        print(f"{curr_d.strftime('%Y-%m-%d')}: {p_pdx.loc[curr_d]:,.0f}원")

p_hmm = adj_mat['A011200']
print("\n=== HMM (편입일 2021-05-27) 타임라인 주가 ===")
dates_hmm = ['2020-06-30', '2020-12-30', '2021-03-31', '2021-05-12', '2021-05-27', '2021-08-31', '2021-12-30']
for d_str in dates_hmm:
    d = pd.Timestamp(d_str)
    sub = p_hmm.loc[p_hmm.index >= d]
    if not sub.empty:
        curr_d = sub.index[0]
        print(f"{curr_d.strftime('%Y-%m-%d')}: {p_hmm.loc[curr_d]:,.0f}원")
