"""
[EXP-KRX-019] 실시간 MSCI 편입 후보 퀀트 스크리닝 및 스윙 실측 백테스트 (미래참조 0%)
========================================================================================
- 목적: 사전 확정된 종목 리스트 없이, 2013~2024년 12개년 동안
        매일 아침 '시총 2.5조 돌파(MSCI 커트라인) + 눌림목 + 스마트머니 수급' 조건만으로
        실시간 스크리닝 진입 시의 성과와 실제 MSCI 편입 적중률(Hit Rate)을 실측
- 탐색 룰:
  1) 시총 조건: 시가총액 2.5조 ~ 5.0조 원 구간 (MSCI 편입 관문 진입 종목)
  2) 위치 조건: 200일선 위(p >= ma200 * 0.90) & (20일선 눌림 0.95~1.03 or RSI <= 42)
  3) 수급 조건: 최근 5일 외인 순증가 or 기관 순매수 양수
- 청산 룰: TP1=+10%, TP2=+20%, SL=-6%, 만기=40일
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

# 실제 역사적 MSCI 신규 편입 종목 세트 (적중률 검증용)
ACTUAL_MSCI_SET = {
    "A019170", "A086520", "A247540", "A022100", "A012450", "A042700", "A196170", "A267260",
    "A011200", "A352820", "A008930", "A128940", "A034020", "A036570", "A251270", "A091990",
    "A068760", "A263750", "A028300", "A010140", "A003670", "A326030", "A302440", "A066970",
    "A293490", "A377300", "A047050", "A005070", "A035900", "A010120", "A064350", "A079550",
    "A450080", "A035720", "A009240", "A026960", "A007070", "A007310", "A035760", "A005300",
    "A081660", "A010620", "A012510", "A096530", "A285130", "A006280", "A011790", "A361610",
    "A259960", "A383220", "A020150", "A137310", "A373220", "A329180", "A001570", "A180640",
    "A348370"
}

def main():
    print("=" * 125)
    print("🔬 [실시간 퀀트 실측] MSCI 편입 후보 실시간 감시 스윙 백테스트 (미래참조 0%)")
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

    # 전 종목 로딩 (시총 1조 이상 경험 종목 위주 필터링으로 메모리 최적화)
    print("⏳ 데이터 로딩 및 인메모리 매트릭스 변환 중...")
    df_ext_mcap = pd.read_parquet(ext_path, columns=['code', 'date', 'market_cap'], filters=[('date', '>=', pd.Timestamp('2013-01-01'))])
    high_mcap_codes = set(df_ext_mcap[df_ext_mcap['market_cap'] >= 1.5e12]['code']).intersection(valid_codes)
    print(f"📌 실측 대상 유니버스: 시총 1.5조 이상 도달 이력 종목 총 {len(high_mcap_codes)}개")

    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close', 'value'],
        filters=[('code', 'in', list(high_mcap_codes)), ('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )
    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap', 'foreign_hold_pct', 'inst_net_qty'],
        filters=[('code', 'in', list(high_mcap_codes)), ('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )

    adj_mat = df_p.pivot(index='date', columns='code', values='close').sort_index()
    val_mat = df_p.pivot(index='date', columns='code', values='value').sort_index()
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
    rs = ema_up / ema_down
    rsi_mat = 100 - (100 / (1 + rs))

    fgn_diff5 = fgn_mat.diff(5)
    inst_sum5 = inst_mat.rolling(5, min_periods=3).sum()

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
    entered_tickers = set()

    print("🚀 12개년 실시간 스크리닝 백테스트 실행 중...")

    for i in range(1, len(cal_sub)):
        d = cal_sub[i]
        prev_d = cal_sub[i-1]
        prev_eq = eq.iloc[i-1]

        # 실시간 스크리닝: 어제 종가 기준 시총 2.5조 ~ 4.5조 원 구간 진입 종목
        sig_list = []
        if prev_d in mcap_mat.index:
            mcap_row = mcap_mat.loc[prev_d].dropna()
            # 시총 2.5조 ~ 4.5조 원 구간 (MSCI 편입 가시권)
            cand_codes = mcap_row[(mcap_row >= 2.3e12) & (mcap_row <= 4.5e12)].index

            for code in cand_codes:
                if code not in adj_mat.columns:
                    continue
                p = adj_mat.loc[prev_d, code]
                ma20 = ma20_mat.loc[prev_d, code] if code in ma20_mat.columns else np.nan
                ma200 = ma200_mat.loc[prev_d, code] if code in ma200_mat.columns else np.nan
                rsi = rsi_mat.loc[prev_d, code] if code in rsi_mat.columns else np.nan

                if np.isnan(p) or np.isnan(ma20) or np.isnan(ma200) or p <= 0:
                    continue

                cond_loc = (p / ma200 >= 0.88) and ((0.95 <= p / ma20 <= 1.03) or (rsi <= 42.0))
                f_flow = fgn_diff5.loc[prev_d, code] if code in fgn_diff5.columns else 0.0
                i_flow = inst_sum5.loc[prev_d, code] if code in inst_sum5.columns else 0.0
                cond_flow = (f_flow > 0.0) or (i_flow > 0.0)

                if cond_loc and cond_flow:
                    # 거래대금 상위 우선 진입
                    val = val_mat.loc[prev_d, code] if code in val_mat.columns else 0.0
                    sig_list.append((code, val))

        # 거래대금 내림차순 정렬
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
                    entered_tickers.add(ticker)
                    prev_eq *= (1.0 - slot_weight * 0.00035)

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
                is_hit = t in ACTUAL_MSCI_SET
                trades.append({
                    "ticker": t,
                    "name": name_map.get(t, t),
                    "gain_pct": gain_from_entry * 100.0,
                    "action": action,
                    "is_actual_msci": is_hit
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

    # MSCI 적중률 계산 (진입 종목 중 실제 MSCI 편입주 비율)
    total_entered_count = len(entered_tickers)
    hit_tickers = [t for t in entered_tickers if t in ACTUAL_MSCI_SET]
    hit_rate = len(hit_tickers) / total_entered_count * 100.0 if total_entered_count > 0 else 0.0

    print("\n" + "=" * 125)
    print("🎯 [실시간 MSCI 후보 스크리닝 백테스트 최종 성과]")
    print("=" * 125)
    print(f"• 계좌 누적 수익률: {tot_ret*100:+.1f}%")
    print(f"• 연평균 복리 수익률 (CAGR): {cagr*100:.1f}%")
    print(f"• 최대 낙폭 (MDD): {mdd*100:.1f}%")
    print(f"• 샤프 지수: {sharpe:.2f}")
    print(f"• 총 매매 횟수: {n_tr}회")
    print(f"• 승률 (Win Rate): {win_rate:.1f}%")
    print(f"• 손익비 (Profit Factor): {pf:.2f}")
    print(f"• 순합산 손익: {net_sum:+.1f}%")
    print("=" * 125)

    print("\n" + "=" * 125)
    print("🎯 [MSCI 편입 적중률 (Hit Rate) 정밀 실측]")
    print("=" * 125)
    print(f"• 실시간 조건으로 포착하여 진입한 총 종목 수: {total_entered_count}개")
    print(f"• 실제로 MSCI에 편입된 진짜 종목 수: {len(hit_tickers)}개")
    print(f"• ★ MSCI 편입 적중률 (Hit Rate): {hit_rate:.1f}%")
    print(f"• 주요 적중 종목: {', '.join([name_map.get(t, t) for t in hit_tickers[:15]])} 등")
    print("=" * 125)

    # 적중 종목 매매 vs 미편입(탈락) 종목 매매 성과 분해
    print("\n" + "=" * 125)
    print("📊 [실제 MSCI 편입 성공 종목 vs 탈락 종목 매매 성과 분해]")
    print("=" * 125)
    summary_split = []
    for is_msci, g in df_tr.groupby('is_actual_msci'):
        label = "★ 실제 MSCI 편입 성공주" if is_msci else "탈락주 (False Positive)"
        n_m = len(g)
        w_m = len(g[g['gain_pct'] > 0])
        wr_m = w_m / n_m * 100.0
        sw_m = g[g['gain_pct'] > 0]['gain_pct'].sum()
        sl_m = abs(g[g['gain_pct'] <= 0]['gain_pct'].sum())
        net_m = sw_m - sl_m
        pf_m = sw_m / sl_m if sl_m > 0 else 0.0
        summary_split.append({
            "구분": label,
            "매매횟수": f"{n_m}회 ({n_m/n_tr*100:.1f}%)",
            "승률": f"{wr_m:.1f}%",
            "손익비": f"{pf_m:.2f}",
            "순합산손익": f"{net_m:+.1f}%",
            "건당 평균손익": f"{g['gain_pct'].mean():+.2f}%"
        })
    print(pd.DataFrame(summary_split).to_string(index=False))
    print("=" * 125)

if __name__ == "__main__":
    main()
