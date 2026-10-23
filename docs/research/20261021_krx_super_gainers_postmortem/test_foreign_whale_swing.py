"""
[EXP-KRX-020] 한국형 박스권 외인 쓸어담기(Foreign Whale) 스윙 실측 백테스트
=============================================================================
- 배경: "한국 증시는 박스권이 많으므로 200일선 바닥이나 외인지분율 절대값에 얽매이지 않고,
        어디서든 외국인이 지분을 +1.0%p 이상 연속 쓸어담는 종목을 스윙하는 것이 맞다"는 가설 검증
- 유니버스: KOSPI & KOSDAQ 보통주 전체 (일평균 거래대금 30억 이상)
- 진입 룰:
  1) 외인 지분율 절대값 제한 없음 (기존 지분율 무관)
  2) 200일선 바닥 조건 철폐 (박스권 수용, 단 심각한 역배열 p < ma200 * 0.70 만 배제)
  3) 핵심 트리거: 최근 20일 외국인 지분율 +1.0%p 이상 순증가 AND 20일 중 13일 이상 외인 순매수
  4) 단기 타점: 20일선 근처 눌림 (p / ma20 <= 1.05) or RSI <= 52 (추격매수 방지)
- 청산 룰: TP1=+10% (50%), TP2=+20% (전량), SL=-6%, 만기=40일, 5-슬롯 (20%씩)
"""
import os
import sys
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = "/home/kuzimo/projects/CTradeSystem"
RESEARCH_DIR = f"{ROOT}/docs/research/20261021_krx_super_gainers_postmortem"

def main():
    print("=" * 125)
    print("🔬 [실측 검증] 한국형 박스권 '외인 쓸어담기(Foreign Whale)' 스윙 시스템 (2013~2024, 12개년)")
    print("=" * 125)

    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    name_map = uni.set_index('code')['name'].to_dict()
    valid_codes = set(uni[(uni['market'].isin(['KOSPI', 'KOSDAQ'])) & (uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])

    df_idx = pd.read_parquet(idx_path)
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    cal = u180.loc[(u180.index >= '2013-01-01') & (u180.index <= '2024-12-31')].index

    print("⏳ 데이터 로딩 및 인메모리 행렬 구축 중...")
    # 거래대금 30억 이상 필터링
    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close', 'value'],
        filters=[('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )
    df_p = df_p[df_p['code'].isin(valid_codes)]

    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap', 'foreign_hold_pct'],
        filters=[('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )
    df_ext = df_ext[df_ext['code'].isin(valid_codes)]

    adj_mat = df_p.pivot(index='date', columns='code', values='close').sort_index()
    val_mat = df_p.pivot(index='date', columns='code', values='value').sort_index()
    fgn_mat = df_ext.pivot(index='date', columns='code', values='foreign_hold_pct').sort_index()

    ret_mat = adj_mat.pct_change().fillna(0.0)
    ma20_mat = adj_mat.rolling(20, min_periods=10).mean()
    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()
    val20_mat = val_mat.rolling(20, min_periods=10).mean()

    delta = adj_mat.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=13, adjust=False).mean()
    ema_down = down.ewm(com=13, adjust=False).mean()
    rs = ema_up / ema_down
    rsi_mat = 100 - (100 / (1 + rs))

    # 외인 지분율 20일 순증가폭 및 20일간 일별 순매수 일수 계산
    fgn_diff20 = fgn_mat.diff(20)
    fgn_daily_diff = fgn_mat.diff(1)
    fgn_buy_days20 = (fgn_daily_diff > 0).rolling(20, min_periods=15).sum()

    cal_sub = cal[(cal >= '2013-03-01') & (cal <= '2024-12-31')]
    num_slots = 5
    slot_weight = 1.0 / num_slots
    cost_rate = 0.0027
    daily_cash_ret = 0.025 / 252.0

    tp1, tp2, sl, max_days = 0.10, 0.20, -0.06, 40

    eq = pd.Series(index=cal_sub, dtype=float)
    eq.iloc[0] = 1.0

    slots = [{"ticker": None, "entry_price": 0.0, "peak_price": 0.0, "holding_days": 0, "remaining_ratio": 1.0, "is_cash": True} for _ in range(num_slots)]
    trades = []
    active_slot_days = 0

    print("🚀 12개년 박스권 외인 쓸어담기 스윙 시뮬레이션 시작...")

    for i in range(1, len(cal_sub)):
        d = cal_sub[i]
        prev_d = cal_sub[i-1]
        prev_eq = eq.iloc[i-1]

        # 실시간 신호 검색
        sig_list = []
        if prev_d in fgn_diff20.index:
            # 외인 지분율 20일간 +1.0%p 이상 순증가 종목 필터
            f_diff_row = fgn_diff20.loc[prev_d].dropna()
            f_streak_row = fgn_buy_days20.loc[prev_d].dropna()
            val_row = val20_mat.loc[prev_d].dropna()

            # 1) 20일 지분 증가 >= 1.0%p AND 20일 중 13일 이상 순매수 AND 20일 평균 거래대금 >= 30억
            cands = f_diff_row[(f_diff_row >= 1.0) & (f_streak_row >= 13) & (val_row >= 30e8)].index

            for code in cands:
                if code not in adj_mat.columns:
                    continue
                p = adj_mat.loc[prev_d, code]
                ma20 = ma20_mat.loc[prev_d, code] if code in ma20_mat.columns else np.nan
                ma200 = ma200_mat.loc[prev_d, code] if code in ma200_mat.columns else np.nan
                rsi = rsi_mat.loc[prev_d, code] if code in rsi_mat.columns else np.nan

                if np.isnan(p) or np.isnan(ma20) or p <= 0:
                    continue

                # 박스권 수용: 심각한 침몰 역배열(p < ma200 * 0.70)만 아니면 OK
                if not np.isnan(ma200) and p < ma200 * 0.70:
                    continue

                # 단기 과열 추격 매수 방지: 20일선 근처(p/ma20 <= 1.06) 또는 RSI <= 55
                cond_timing = (p / ma20 <= 1.06) or (rsi <= 55.0)

                if cond_timing:
                    # 외인 지분율 증가폭 큰 순서대로 우선순위
                    sig_list.append((code, f_diff_row.loc[code]))

        sig_list.sort(key=lambda x: x[1], reverse=True)
        sorted_picks = [x[0] for x in sig_list]

        # 신규 진입
        empty_slots = [idx for idx, s in enumerate(slots) if s["is_cash"] or s["ticker"] is None]
        if empty_slots and sorted_picks:
            currently_held = [s["ticker"] for s in slots if not s["is_cash"] and s["ticker"] is not None]
            new_picks = [c for c in sorted_picks if c not in currently_held]

            for slot_idx, ticker in zip(empty_slots, new_picks):
                p_entry = adj_mat.loc[d, ticker] if ticker in adj_mat.columns else np.nan
                if not np.isnan(p_entry) and p_entry > 0:
                    slots[slot_idx] = {
                        "ticker": ticker,
                        "entry_price": p_entry,
                        "peak_price": p_entry,
                        "holding_days": 0,
                        "remaining_ratio": 1.0,
                        "is_cash": False
                    }
                    prev_eq *= (1.0 - slot_weight * 0.00035)

        for s in slots:
            if not s["is_cash"] and s["ticker"] is not None:
                active_slot_days += 1

        # 일별 청산 검사
        slot_returns = []
        for s in slots:
            if s["is_cash"] or s["ticker"] is None:
                slot_returns.append(daily_cash_ret)
                continue

            t = s["ticker"]
            r_day = ret_mat.loc[d, t] if t in ret_mat.columns else 0.0
            curr_px = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan

            if np.isnan(curr_px) or curr_px <= 0:
                slot_returns.append(0.0)
                continue

            s["holding_days"] += 1
            if curr_px > s["peak_price"]:
                s["peak_price"] = curr_px

            gain_from_entry = (curr_px / s["entry_price"]) - 1.0
            drop_from_peak = (curr_px / s["peak_price"]) - 1.0

            action = None
            exit_ret = 0.0

            if gain_from_entry >= tp2:
                action = "TP2"
                exit_ret = tp2 - cost_rate
            elif gain_from_entry >= tp1 and drop_from_peak <= -0.05:
                action = "TRAIL"
                exit_ret = gain_from_entry - cost_rate
            elif gain_from_entry <= sl:
                action = "SL"
                exit_ret = sl - cost_rate
            elif s["holding_days"] >= max_days:
                action = "TIME"
                exit_ret = gain_from_entry - cost_rate

            if action is not None:
                slot_returns.append(exit_ret * s["remaining_ratio"])
                trades.append({
                    "ticker": t,
                    "name": name_map.get(t, t),
                    "gain_pct": gain_from_entry * 100.0,
                    "action": action,
                    "days": s["holding_days"]
                })
                s["is_cash"] = True
                s["ticker"] = None
            else:
                if s["remaining_ratio"] == 1.0 and gain_from_entry >= tp1:
                    s["remaining_ratio"] = 0.5
                    slot_returns.append(r_day * 0.5 + tp1 * 0.5)
                else:
                    slot_returns.append(r_day * s["remaining_ratio"])

        port_ret = sum(slot_returns) / num_slots
        eq.iloc[i] = prev_eq * (1.0 + port_ret)

    tot_ret = (eq.iloc[-1] / eq.iloc[0]) - 1.0
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years) - 1.0
    daily_r = eq.pct_change().dropna()
    vol = daily_r.std() * np.sqrt(252)
    sharpe = (cagr - 0.02) / vol if vol > 0 else 0.0
    dd = (eq - eq.cummax()) / eq.cummax()
    mdd = dd.min()

    df_tr = pd.DataFrame(trades)
    n_tr = len(df_tr)
    wins = df_tr[df_tr['gain_pct'] > 0]
    losses = df_tr[df_tr['gain_pct'] <= 0]
    win_rate = len(wins) / n_tr * 100.0 if n_tr > 0 else 0.0
    sum_w = wins['gain_pct'].sum() if len(wins) > 0 else 0.0
    sum_l = abs(losses['gain_pct'].sum()) if len(losses) > 0 else 0.0
    net_sum = sum_w - sum_l
    pf = sum_w / sum_l if sum_l > 0 else 0.0
    avg_util = (active_slot_days / (len(cal_sub) * num_slots)) * 100.0

    print("\n" + "=" * 125)
    print("🏆 [박스권 외인 쓸어담기 스윙 12개년 실측 최종 결과]")
    print("=" * 125)
    print(f"• 계좌 누적 수익률: {tot_ret*100:+.1f}%")
    print(f"• 연평균 복리 수익률 (CAGR): {cagr*100:.1f}%")
    print(f"• 최대 낙폭 (MDD): {mdd*100:.1f}%")
    print(f"• 샤프 지수: {sharpe:.2f}")
    print(f"• 총 매매 횟수: {n_tr}회 (연평균 약 {n_tr/11.8:.0f}회)")
    print(f"• 승률 (Win Rate): {win_rate:.1f}%")
    print(f"• 손익비 (Profit Factor): {pf:.2f}")
    print(f"• 순합산 손익: {net_sum:+.1f}%")
    print(f"• 평균 슬롯 가동률: {avg_util:.1f}%")
    print("=" * 125)

    # 청산 유형별 통계
    print("\n" + "=" * 125)
    print("📊 [청산 유형별 분석]")
    print("=" * 125)
    for act, g in df_tr.groupby('action'):
        print(f"• {act:<6}: {len(g):>4}회 ({len(g)/n_tr*100:>5.1f}%) | 평균 손익: {g['gain_pct'].mean():>+6.2f}% | 평균 보유: {g['days'].mean():>4.1f}일")
    print("=" * 125)

if __name__ == "__main__":
    main()
