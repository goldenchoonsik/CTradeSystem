"""
[EXP-KRX-002-SUB] MSCI 정기변경 발표일~편입일 2주 윈도우 정밀 실측 분석
"""
import pandas as pd
import numpy as np

idx_path = 'DataVault/staging/kr_equity/derived/index_1d.parquet'
px_path = 'DataVault/staging/kr_equity/derived/prices_1d.parquet'
uni = pd.read_csv('DataVault/staging/kr_equity/universe.csv')
name_map = dict(zip(uni['code'], uni['name']))

from msci_super_rookie_backtest import MSCI_ADDITIONS_RAW

df_idx = pd.read_parquet(idx_path)
u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
cal = u180.index

all_codes = set()
for a, e, codes, _ in MSCI_ADDITIONS_RAW:
    all_codes.update(codes)

df_p = pd.read_parquet(px_path, columns=['code', 'date', 'close', 'open'], filters=[('code', 'in', list(all_codes))])
close_mat = df_p.pivot(index='date', columns='code', values='close').reindex(cal).ffill()
open_mat = df_p.pivot(index='date', columns='code', values='open').reindex(cal).ffill()

records = []
for ann_str, eff_str, codes, desc in MSCI_ADDITIONS_RAW:
    ann_ts = pd.to_datetime(ann_str)
    eff_ts = pd.to_datetime(eff_str)
    
    ann_cal = cal[cal >= ann_ts]
    if len(ann_cal) == 0: continue
    d_ann = ann_cal[0]
    
    eff_cal = cal[cal <= eff_ts]
    if len(eff_cal) == 0: continue
    d_eff = eff_cal[-1]
    
    post_cal = cal[cal > d_eff]
    d_post20 = post_cal[min(19, len(post_cal)-1)] if len(post_cal) > 0 else d_eff
    
    pre_cal = cal[cal < d_ann]
    d_pre20 = pre_cal[max(0, len(pre_cal)-20)] if len(pre_cal) >= 20 else pre_cal[0]
    
    for c in codes:
        if c not in close_mat.columns: continue
        p_pre = close_mat[c].loc[d_pre20]
        p_ann_open = open_mat[c].loc[d_ann]
        p_ann_close = close_mat[c].loc[d_ann]
        p_eff = close_mat[c].loc[d_eff]
        p_post = close_mat[c].loc[d_post20]
        
        if np.isnan(p_ann_open) or np.isnan(p_eff) or p_ann_open <= 0: continue
        
        # 1. 2주 윈도우
        ret_window_open = (p_eff / p_ann_open) - 1.0
        ret_window_close = (p_eff / p_ann_close) - 1.0
        # 2. 선반영 (D-20 -> 발표일)
        ret_pre = (p_ann_close / p_pre) - 1.0 if not np.isnan(p_pre) and p_pre > 0 else np.nan
        # 3. 편입 후 1달 숙취 (편입일 -> D+20)
        ret_post = (p_post / p_eff) - 1.0 if not np.isnan(p_post) and p_eff > 0 else np.nan
        
        records.append({
            'code': c,
            'name': name_map.get(c, c),
            'desc': desc,
            'd_ann': d_ann.strftime('%Y-%m-%d'),
            'd_eff': d_eff.strftime('%Y-%m-%d'),
            'days': (d_eff - d_ann).days,
            'ret_2w_open': ret_window_open,
            'ret_2w_close': ret_window_close,
            'ret_pre_1m': ret_pre,
            'ret_post_1m': ret_post
        })

df = pd.DataFrame(records)
csv_out = 'docs/research/20261020_msci_super_rookie/msci_2week_window_stats.csv'
df.to_csv(csv_out, index=False, encoding='utf-8-sig')

print("=" * 70)
print("📊 [실측 통계] MSCI 정기변경 발표일~편입일(약 2주) 및 전후 수익률")
print("=" * 70)
print(f"1. 대상 샘플: 2018~2025년 신규 편입 {len(df)}개 종목 전수")
print(f"2. 발표일~편입일 평균 소요 기간: 약 {df['days'].mean():.1f}일 (거래일 기준 약 10~12영업일)")

print("\n[A] 발표 익일 시초가 진입 ➡️ 편입일(리밸런싱일) 종가 청산 (약 2주 보유)")
print(f" - 평균 수익률: {df['ret_2w_open'].mean()*100:+.2f}%")
print(f" - 중앙값 수익률: {df['ret_2w_open'].median()*100:+.2f}%")
print(f" - 승률 (수익 종목 비율): {(df['ret_2w_open'] > 0).mean()*100:.1f}% ({int((df['ret_2w_open'] > 0).sum())}승 / {len(df)}전)")
print(f" - 최고 수익 종목: {df['ret_2w_open'].max()*100:+.2f}% ({df.loc[df['ret_2w_open'].idxmax()]['name']})")
print(f" - 최대 손실 종목: {df['ret_2w_open'].min()*100:+.2f}% ({df.loc[df['ret_2w_open'].idxmin()]['name']})")

print("\n[B] 발표 전 1달 선취매 구간 (D-20 거래일 ➡️ 발표일 종가)")
print(f" - 평균 수익률: {df['ret_pre_1m'].mean()*100:+.2f}%")
print(f" - 중앙값 수익률: {df['ret_pre_1m'].median()*100:+.2f}%")
print(f" - 승률: {(df['ret_pre_1m'] > 0).mean()*100:.1f}%")

print("\n[C] 편입 당일 종가 매수 ➡️ 편입 후 1달 보유 (D-Day ➡️ D+20 거래일) '재료 소멸 구간'")
print(f" - 평균 수익률: {df['ret_post_1m'].mean()*100:+.2f}%")
print(f" - 하락 종목 비율: {(df['ret_post_1m'] < 0).mean()*100:.1f}%")

print("\n" + "-" * 70)
print("🔥 [약 2주 윈도우] 역대 최고 수익률 TOP 7")
print("-" * 70)
top7 = df.sort_values('ret_2w_open', ascending=False).head(7)
for _, r in top7.iterrows():
    print(f"[{r['name']} ({r['code']})] 2주 수익률: {r['ret_2w_open']*100:+.2f}% | 편입 후 1달: {r['ret_post_1m']*100:+.2f}% | ({r['d_ann']} -> {r['d_eff']})")

print("\n" + "-" * 70)
print("💀 [약 2주 윈도우] 역대 최악 손실률 BOTTOM 7 (발표 후 급락한 종목)")
print("-" * 70)
bot7 = df.sort_values('ret_2w_open').head(7)
for _, r in bot7.iterrows():
    print(f"[{r['name']} ({r['code']})] 2주 수익률: {r['ret_2w_open']*100:+.2f}% | 편입 후 1달: {r['ret_post_1m']*100:+.2f}% | ({r['d_ann']} -> {r['d_eff']})")
