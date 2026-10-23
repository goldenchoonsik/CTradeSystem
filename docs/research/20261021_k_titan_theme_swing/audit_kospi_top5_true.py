"""
[AUDIT] K-Top 5 스윙 전략 원본 코드 계산 무결성 정밀 감사
============================================================
기존 `kospi_top5_swing_system.py`의 계산이 왜곡되었는지 전수 조사.
"""
import os
import sys
import numpy as np
import pandas as pd

ROOT = "/home/kuzimo/projects/CTradeSystem"
uni = pd.read_csv(f"{ROOT}/DataVault/staging/kr_equity/universe.csv")
name_map = dict(zip(uni['code'], uni['name']))
kospi_codes = set(uni[(uni['market'] == 'KOSPI') & (uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])

df_idx = pd.read_parquet(f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet")
u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
cal = u180.loc[(u180.index >= '2013-01-01') & (u180.index <= '2024-12-31')].index

df_p = pd.read_parquet(
    f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet",
    columns=['code', 'date', 'close', 'value'],
    filters=[('code', 'in', list(kospi_codes)), ('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
)
df_ext = pd.read_parquet(
    f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet",
    columns=['code', 'date', 'market_cap', 'foreign_hold_pct', 'inst_net_qty'],
    filters=[('code', 'in', list(kospi_codes)), ('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
)

adj_mat = df_p.pivot(index='date', columns='code', values='close').sort_index()
mcap_mat = df_ext.pivot(index='date', columns='code', values='market_cap').sort_index()
fgn_mat = df_ext.pivot(index='date', columns='code', values='foreign_hold_pct').sort_index()
inst_mat = df_ext.pivot(index='date', columns='code', values='inst_net_qty').sort_index()
ret_mat = adj_mat.pct_change().fillna(0.0)

ma20_mat = adj_mat.rolling(20, min_periods=10).mean()
ma200_mat = adj_mat.rolling(200, min_periods=100).mean()

delta = adj_mat.diff()
up = delta.clip(lower=0)
down = -1 * delta.clip(upper=0)
ema_up = up.ewm(com=13, adjust=False).mean()
ema_down = down.ewm(com=13, adjust=False).mean()
rsi_mat = 100 - (100 / (1 + (ema_up / ema_down)))

fgn_diff5 = fgn_mat.diff(5)
inst_sum5 = inst_mat.rolling(5, min_periods=3).sum()

monthly_dates = set([d for d in cal if d.day <= 7 and cal[cal.get_loc(d)-1].month != d.month])

num_slots = 5
cost_rate = 0.0027

# 1. 순수 개별 거래(Trade Log) 추출
slots = [{'ticker': None, 'entry_px': 0.0, 'peak_px': 0.0, 'entry_date': None, 'days': 0} for _ in range(num_slots)]
trades = []

curr_top5 = []
for i in range(1, len(cal)):
    d = cal[i]
    prev_d = cal[i-1]
    if d in monthly_dates or not curr_top5:
        mcap_row = mcap_mat.loc[prev_d].dropna()
        curr_top5 = mcap_row.sort_values(ascending=False).head(5).index.tolist()

    empty = [s for s in slots if s['ticker'] is None]
    held = set(s['ticker'] for s in slots if s['ticker'] is not None)
    eligible = []
    for c in curr_top5:
        if c in held:
            continue
        p = adj_mat.loc[prev_d, c] if c in adj_mat.columns else np.nan
        ma20 = ma20_mat.loc[prev_d, c] if c in ma20_mat.columns else np.nan
        ma200 = ma200_mat.loc[prev_d, c] if c in ma200_mat.columns else np.nan
        rsi = rsi_mat.loc[prev_d, c] if c in rsi_mat.columns else np.nan
        if np.isnan(p) or np.isnan(ma20) or np.isnan(ma200) or p <= 0:
            continue
        cond_loc = (p / ma200 >= 0.85) and ((0.95 <= p / ma20 <= 1.03) or (rsi <= 42.0))
        f_flow = fgn_diff5.loc[prev_d, c] if c in fgn_diff5.columns else 0.0
        i_flow = inst_sum5.loc[prev_d, c] if c in inst_sum5.columns else 0.0
        if cond_loc and (f_flow > 0 or i_flow > 0):
            eligible.append(c)

    for s, t in zip(empty, eligible):
        p_entry = adj_mat.loc[d, t]
        s['ticker'] = t
        s['entry_px'] = p_entry
        s['peak_px'] = p_entry
        s['entry_date'] = d
        s['days'] = 0

    for s in slots:
        if s['ticker'] is None:
            continue
        t = s['ticker']
        curr_px = adj_mat.loc[d, t]
        s['days'] += 1
        if curr_px > s['peak_px']:
            s['peak_px'] = curr_px
        gain = (curr_px / s['entry_px']) - 1.0
        drop = (curr_px / s['peak_px']) - 1.0

        action = None
        if gain >= 0.15:
            action = '15% 익절'
        elif gain >= 0.07 and drop <= -0.05:
            action = '트레일링스탑'
        elif gain <= -0.07:
            action = '-7% 손절'
        elif s['days'] >= 40:
            action = '40일 만기'

        if action:
            net_return = gain - cost_rate
            trades.append({
                'entry_date': s['entry_date'].strftime('%Y-%m-%d'),
                'exit_date': d.strftime('%Y-%m-%d'),
                'code': t,
                'name': name_map.get(t, t),
                'gain_pct': gain * 100.0,
                'net_return': net_return,
                'days': s['days'],
                'action': action
            })
            s['ticker'] = None

df_tr = pd.DataFrame(trades)

print("=" * 80)
print("🔍 [K-Top 5 스윙 실제 거래 원장(Trade Log) 감사 결과]")
print("=" * 80)
print(f"총 매매 횟수: {len(df_tr)}회")
win_cnt = (df_tr['net_return'] > 0).sum()
loss_cnt = (df_tr['net_return'] <= 0).sum()
win_rate = win_cnt / len(df_tr) * 100.0
avg_win = df_tr[df_tr['net_return'] > 0]['net_return'].mean() * 100.0
avg_loss = df_tr[df_tr['net_return'] <= 0]['net_return'].mean() * 100.0
sum_win = df_tr[df_tr['net_return'] > 0]['net_return'].sum()
sum_loss = abs(df_tr[df_tr['net_return'] <= 0]['net_return'].sum())
profit_factor = sum_win / sum_loss if sum_loss > 0 else 0.0

print(f"- 승리 거래: {win_cnt}회 / 패배 거래: {loss_cnt}회")
print(f"- 실제 승률: {win_rate:.1f}%")
print(f"- 평균 승리 수익률: +{avg_win:.2f}%")
print(f"- 평균 패배 손실률: {avg_loss:.2f}%")
print(f"- 실제 손익비 (Profit Factor): {profit_factor:.2f}")
print(f"- 12년간 단순 합산 수익률 (Net Sum): {(sum_win - sum_loss)*100:+.1f}%")

# 청산 사유별 분석
print("\n[청산 사유별 상세 통계]")
for act, grp in df_tr.groupby('action'):
    print(f"  * {act:12s}: {len(grp):3d}회 | 평균 수익률: {grp['net_return'].mean()*100:+.2f}% | 평균 보유일: {grp['days'].mean():.1f}일")

# 실제 1억 원 자산 계좌 정산 (정석 시가평가)
init_cap = 100_000_000.0
slots_acc = [{'val': init_cap / num_slots, 'ticker': None, 'entry_px': 0.0, 'peak_px': 0.0, 'days': 0} for _ in range(num_slots)]
eq_history = [init_cap]

for i in range(1, len(cal)):
    d = cal[i]
    prev_d = cal[i-1]
    if d in monthly_dates or not curr_top5:
        mcap_row = mcap_mat.loc[prev_d].dropna()
        curr_top5 = mcap_row.sort_values(ascending=False).head(5).index.tolist()

    empty = [s for s in slots_acc if s['ticker'] is None]
    held = set(s['ticker'] for s in slots_acc if s['ticker'] is not None)
    eligible = []
    for c in curr_top5:
        if c in held:
            continue
        p = adj_mat.loc[prev_d, c] if c in adj_mat.columns else np.nan
        ma20 = ma20_mat.loc[prev_d, c] if c in ma20_mat.columns else np.nan
        ma200 = ma200_mat.loc[prev_d, c] if c in ma200_mat.columns else np.nan
        rsi = rsi_mat.loc[prev_d, c] if c in rsi_mat.columns else np.nan
        if np.isnan(p) or np.isnan(ma20) or np.isnan(ma200) or p <= 0:
            continue
        cond_loc = (p / ma200 >= 0.85) and ((0.95 <= p / ma20 <= 1.03) or (rsi <= 42.0))
        f_flow = fgn_diff5.loc[prev_d, c] if c in fgn_diff5.columns else 0.0
        i_flow = inst_sum5.loc[prev_d, c] if c in inst_sum5.columns else 0.0
        if cond_loc and (f_flow > 0 or i_flow > 0):
            eligible.append(c)

    for s, t in zip(empty, eligible):
        p_entry = adj_mat.loc[d, t]
        s['val'] *= (1.0 - 0.00035)  # 매수 슬리피지
        s['ticker'] = t
        s['entry_px'] = p_entry
        s['peak_px'] = p_entry
        s['days'] = 0

    for s in slots_acc:
        if s['ticker'] is None:
            s['val'] *= (1.0 + 0.025 / 252.0)
        else:
            t = s['ticker']
            r_day = ret_mat.loc[d, t]
            curr_px = adj_mat.loc[d, t]
            s['val'] *= (1.0 + r_day)
            s['days'] += 1
            if curr_px > s['peak_px']:
                s['peak_px'] = curr_px
            gain = (curr_px / s['entry_px']) - 1.0
            drop = (curr_px / s['peak_px']) - 1.0

            action = None
            if gain >= 0.15:
                action = '15% 익절'
            elif gain >= 0.07 and drop <= -0.05:
                action = '트레일링스탑'
            elif gain <= -0.07:
                action = '-7% 손절'
            elif s['days'] >= 40:
                action = '40일 만기'

            if action:
                s['val'] *= (1.0 - cost_rate)
                s['ticker'] = None

    eq_history.append(sum(s['val'] for s in slots_acc))

final_eq = eq_history[-1]
total_ret = (final_eq / init_cap) - 1.0
cagr = (final_eq / init_cap) ** (1.0 / 12.0) - 1.0
print("\n" + "=" * 80)
print(f"💰 [진짜 최종 계좌 결과 (1억 원 원금 -> 12년 뒤)]")
print(f"- 최종 자산: {final_eq/1e8:.2f}억 원 ({final_eq:,.0f}원)")
print(f"- 누적 수익률: {total_ret*100:+.2f}%")
print(f"- CAGR (연복리): {cagr*100:.2f}%")
print("=" * 80)
