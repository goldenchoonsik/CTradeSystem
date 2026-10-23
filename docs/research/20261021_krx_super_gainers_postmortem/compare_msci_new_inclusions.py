"""
[EXP-KRX-016] MSCI 신규 편입 종목 대상: K-Titan 스윙 vs 슈퍼루키 전략 실측 비교
====================================================================================
- 목적: 역대 MSCI Korea 신규 편입 종목들을 대상으로
  1) [K-Titan Pullback Swing] 단기 눌림목 수급 스윙 (+10%/+20%/-6%/40일)
  2) [슈퍼루키 Super Rookie] 바닥권 거래대금 폭증 + 스마트머니 계단식 매집 선취매 스윙 (+20%/+50%/-8%/60일)
  두 전략의 성과를 12개년(2013~2024년) 실측 비교 분석
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
    print("🔬 [실측 비교] MSCI 신규 편입 종목 대상: K-Titan 스윙 vs 슈퍼루키 전략 (2013~2024, 12개년)")
    print("=" * 125)

    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    # MSCI 한국 지수는 코스피뿐만 아니라 코스닥 대형주(에코프로, 알테오젠, HLB, 펄어비스 등)도 포함하므로 코스피+코스닥 보통주 전체 대상
    valid_codes = set(uni[(uni['market'].isin(['KOSPI', 'KOSDAQ'])) & (uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])

    df_idx = pd.read_parquet(idx_path)
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    cal = u180.loc[(u180.index >= '2013-01-01') & (u180.index <= '2024-12-31')].index

    # 1. 역대 MSCI Korea 신규 편입 핵심 종목군 식별 (2013~2024)
    # 시총 100위 밖에서 시총 상위권으로 진입하여 시총 2.5조 이상을 돌파했던 역대 신규 편입 대장주들
    known_msci_new = [
        "A019170", # 신풍제약 (2020 편입)
        "A086520", # 에코프로 (2023 편입)
        "A247540", # 에코프로비엠 (2021 편입)
        "A022100", # 포스코DX (2023 편입)
        "A012450", # 한화에어로스페이스 (2023 편입)
        "A042700", # 한미반도체 (2023 편입)
        "A196170", # 알테오젠 (2020/2024 편입)
        "A267260", # HD현대일렉트릭 (2024 편입)
        "A011200", # HMM (2021 편입)
        "A352820", # 하이브 (2021 편입)
        "A008930", # 한미사이언스 (2015 편입)
        "A128940", # 한미약품 (2015 편입)
        "A034020", # 두산에너빌리티 (2020 편입)
        "A036570", # 엔씨소프트 (2017 편입)
        "A251270", # 넷마블 (2017 편입)
        "A091990", # 셀트리온헬스케어 (2017 편입)
        "A068760", # 셀트리온제약 (2018 편입)
        "A263750", # 펄어비스 (2018 편입)
        "A028300", # HLB (2018 편입)
        "A010140", # 삼성중공업 (2018)
        "A003670", # 포스코퓨처엠 (2018 편입)
        "A005380", # 현대차
        "A326030", # SK바이오팜 (2020 편입)
        "A302440", # SK바이오사이언스 (2021 편입)
        "A066970", # 엘앤에프 (2021 편입)
        "A293490", # 카카오게임즈 (2021 편입)
        "A377300", # 카카오페이 (2023 편입)
        "A005490", # POSCO홀딩스
        "A010130", # 고려아연
        "A010950", # S-Oil
        "A047050", # 포스코인터내셔널 (2023 편입)
        "A005070", # 코스모신소재 (2023 편입)
        "A035900", # JYP Ent. (2023 편입)
        "A010120", # LS ELECTRIC (2024 편입)
        "A064350", # 현대로템 (2024 편입)
        "A079550", # LIG넥스원 (2024 편입)
        "A450080", # 에코프로머티 (2024 편입)
        "A096770", # SK이노베이션
        "A028260", # 삼성물산
        "A035720", # 카카오
    ]

    target_codes = [c for c in known_msci_new if c in valid_codes]
    print(f"📌 실측 대상 역대 MSCI 신규 편입 핵심 대장주: 총 {len(target_codes)}개 종목")

    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close', 'value'],
        filters=[('code', 'in', target_codes), ('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )
    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap', 'foreign_hold_pct', 'inst_net_qty'],
        filters=[('code', 'in', target_codes), ('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )

    adj_mat = df_p.pivot(index='date', columns='code', values='close').sort_index()
    val_mat = df_p.pivot(index='date', columns='code', values='value').sort_index()
    mcap_mat = df_ext.pivot(index='date', columns='code', values='market_cap').sort_index()
    fgn_mat = df_ext.pivot(index='date', columns='code', values='foreign_hold_pct').sort_index()
    inst_mat = df_ext.pivot(index='date', columns='code', values='inst_net_qty').sort_index()

    ret_mat = adj_mat.pct_change().fillna(0.0)

    ma20_mat = adj_mat.rolling(20, min_periods=10).mean()
    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()
    val_ma60_mat = val_mat.rolling(60, min_periods=20).mean()

    delta = adj_mat.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=13, adjust=False).mean()
    ema_down = down.ewm(com=13, adjust=False).mean()
    rs = ema_up / ema_down
    rsi_mat = 100 - (100 / (1 + rs))

    fgn_diff5 = fgn_mat.diff(5)
    inst_sum5 = inst_mat.rolling(5, min_periods=3).sum()

    fgn_diff20 = fgn_mat.diff(20)
    inst_sum20 = inst_mat.rolling(20, min_periods=10).sum()

    cal_sub = cal[(cal >= '2013-03-01') & (cal <= '2024-12-31')]
    start_pos = 0
    num_slots = 5
    slot_weight = 1.0 / num_slots
    cost_rate = 0.0027
    daily_cash_ret = 0.025 / 252.0

    # =========================================================================
    # 전략 1: K-Titan Pullback Swing (단기 눌림목 스윙)
    # - TP1=+10%, TP2=+20%, SL=-6%, 만기=40일
    # - 진입: 20일선 눌림 or RSI <= 42 + 5일 수급
    # =========================================================================
    def run_ktitan_swing():
        eq = pd.Series(index=cal_sub, dtype=float)
        eq.iloc[0] = 1.0

        slots = [{"ticker": None, "entry_price": 0.0, "peak_price": 0.0, "holding_days": 0, "remaining_ratio": 1.0, "is_cash": True} for _ in range(num_slots)]
        trade_gains = []

        for i in range(1, len(cal_sub)):
            d = cal_sub[i]
            prev_d = cal_sub[i-1]
            prev_eq = eq.iloc[i-1]

            # 신호 검색
            sig_list = []
            for code in target_codes:
                p = adj_mat.loc[prev_d, code] if code in adj_mat.columns else np.nan
                ma20 = ma20_mat.loc[prev_d, code] if code in ma20_mat.columns else np.nan
                ma200 = ma200_mat.loc[prev_d, code] if code in ma200_mat.columns else np.nan
                rsi = rsi_mat.loc[prev_d, code] if code in rsi_mat.columns else np.nan

                if np.isnan(p) or np.isnan(ma20) or np.isnan(ma200) or p <= 0:
                    continue

                cond_loc = (p / ma200 >= 0.85) and ((0.95 <= p / ma20 <= 1.03) or (rsi <= 42.0))
                f_flow = fgn_diff5.loc[prev_d, code] if code in fgn_diff5.columns else 0.0
                i_flow = inst_sum5.loc[prev_d, code] if code in inst_sum5.columns else 0.0
                cond_flow = (f_flow > 0.0) or (i_flow > 0.0)

                if cond_loc and cond_flow:
                    sig_list.append(code)

            # 진입
            empty_slots = [idx for idx, s in enumerate(slots) if s["is_cash"] or s["ticker"] is None]
            if empty_slots and sig_list:
                currently_held = [s["ticker"] for s in slots if not s["is_cash"] and s["ticker"] is not None]
                new_picks = [c for c in sig_list if c not in currently_held]

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

            # 청산
            tp1, tp2, sl, max_days = 0.10, 0.20, -0.06, 40
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
                    trade_gains.append(gain_from_entry * 100.0)
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

        n_tr = len(trade_gains)
        tg = np.array(trade_gains) if n_tr > 0 else np.array([])
        wins = tg[tg > 0]
        losses = tg[tg <= 0]
        win_rate = (len(wins) / n_tr * 100.0) if n_tr > 0 else 0.0
        sum_w = np.sum(wins) if len(wins) > 0 else 0.0
        sum_l = abs(np.sum(losses)) if len(losses) > 0 else 0.0
        net_sum = sum_w - sum_l
        pf = (sum_w / sum_l) if sum_l > 0 else 0.0

        return {
            "name": "K-Titan Pullback Swing (MSCI 신규편입주 대상)",
            "tot_ret": tot_ret * 100.0,
            "cagr": cagr * 100.0,
            "mdd": mdd * 100.0,
            "sharpe": sharpe,
            "n_tr": n_tr,
            "win_rate": win_rate,
            "pf": pf,
            "net_sum": net_sum
        }

    # =========================================================================
    # 전략 2: 슈퍼루키 (Super Rookie) 스마트머니 바닥 탈출 전략
    # - 진입: 바닥권 (p / ma200 <= 1.15), 거래대금 폭증 (val >= val_ma60 * 2.5),
    #         20일 외인/기관 순매수 지속성 (fgn_diff20 > 0.5%p or inst_sum20 > 0)
    # - 청산: TP1=+20% (절반 익절), TP2=+50% (전량 익절), SL=-8%, 만기=60일
    # =========================================================================
    def run_super_rookie():
        eq = pd.Series(index=cal_sub, dtype=float)
        eq.iloc[0] = 1.0

        slots = [{"ticker": None, "entry_price": 0.0, "peak_price": 0.0, "holding_days": 0, "remaining_ratio": 1.0, "is_cash": True} for _ in range(num_slots)]
        trade_gains = []

        for i in range(1, len(cal_sub)):
            d = cal_sub[i]
            prev_d = cal_sub[i-1]
            prev_eq = eq.iloc[i-1]

            # 신호 검색 (슈퍼루키 조건: 바닥권 + 거래대금 폭증 + 장기 수급 유입)
            sig_list = []
            for code in target_codes:
                p = adj_mat.loc[prev_d, code] if code in adj_mat.columns else np.nan
                ma200 = ma200_mat.loc[prev_d, code] if code in ma200_mat.columns else np.nan
                v = val_mat.loc[prev_d, code] if code in val_mat.columns else np.nan
                v_ma60 = val_ma60_mat.loc[prev_d, code] if code in val_ma60_mat.columns else np.nan

                if np.isnan(p) or np.isnan(ma200) or np.isnan(v) or np.isnan(v_ma60) or p <= 0 or v_ma60 <= 0:
                    continue

                cond_bottom = (0.75 <= p / ma200 <= 1.20)  # 200일선 바닥권 탈출 초입
                cond_volume = (v / v_ma60 >= 2.5)          # 평소 대비 거래대금 2.5배 이상 폭증

                f_flow20 = fgn_diff20.loc[prev_d, code] if code in fgn_diff20.columns else 0.0
                i_flow20 = inst_sum20.loc[prev_d, code] if code in inst_sum20.columns else 0.0
                cond_smart_money = (f_flow20 > 0.0) or (i_flow20 > 0.0)

                if cond_bottom and cond_volume and cond_smart_money:
                    sig_list.append(code)

            # 진입
            empty_slots = [idx for idx, s in enumerate(slots) if s["is_cash"] or s["ticker"] is None]
            if empty_slots and sig_list:
                currently_held = [s["ticker"] for s in slots if not s["is_cash"] and s["ticker"] is not None]
                new_picks = [c for c in sig_list if c not in currently_held]

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

            # 청산 (슈퍼루키: 긴 파동 수확)
            tp1, tp2, sl, max_days = 0.20, 0.50, -0.08, 60
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
                elif gain_from_entry >= tp1 and drop_from_peak <= -0.08:
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
                    trade_gains.append(gain_from_entry * 100.0)
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

        n_tr = len(trade_gains)
        tg = np.array(trade_gains) if n_tr > 0 else np.array([])
        wins = tg[tg > 0]
        losses = tg[tg <= 0]
        win_rate = (len(wins) / n_tr * 100.0) if n_tr > 0 else 0.0
        sum_w = np.sum(wins) if len(wins) > 0 else 0.0
        sum_l = abs(np.sum(losses)) if len(losses) > 0 else 0.0
        net_sum = sum_w - sum_l
        pf = (sum_w / sum_l) if sum_l > 0 else 0.0

        return {
            "name": "슈퍼루키 (Super Rookie 바닥탈출 전략)",
            "tot_ret": tot_ret * 100.0,
            "cagr": cagr * 100.0,
            "mdd": mdd * 100.0,
            "sharpe": sharpe,
            "n_tr": n_tr,
            "win_rate": win_rate,
            "pf": pf,
            "net_sum": net_sum
        }

    res_k = run_ktitan_swing()
    res_sr = run_super_rookie()

    df_out = pd.DataFrame([
        {
            "전략 명칭": res_k["name"],
            "핵심 철학": "단기 눌림목 수급 스윙",
            "목표 익절": "+10% (50%) / +20% (전량)",
            "손절선 / 만기": "-6% / 40일",
            "누적수익률": f"{res_k['tot_ret']:+.1f}%",
            "CAGR": f"{res_k['cagr']:.1f}%",
            "MDD": f"{res_k['mdd']:.1f}%",
            "샤프지수": f"{res_k['sharpe']:.2f}",
            "승률": f"{res_k['win_rate']:.1f}%",
            "손익비": f"{res_k['pf']:.2f}",
            "순합산손익": f"{res_k['net_sum']:+.1f}%",
            "매매횟수": f"{res_k['n_tr']}회"
        },
        {
            "전략 명칭": res_sr["name"],
            "핵심 철학": "바닥 대량거래 + 스마트머니 매집 선취매",
            "목표 익절": "+20% (50%) / +50% (전량)",
            "손절선 / 만기": "-8% / 60일",
            "누적수익률": f"{res_sr['tot_ret']:+.1f}%",
            "CAGR": f"{res_sr['cagr']:.1f}%",
            "MDD": f"{res_sr['mdd']:.1f}%",
            "샤프지수": f"{res_sr['sharpe']:.2f}",
            "승률": f"{res_sr['win_rate']:.1f}%",
            "손익비": f"{res_sr['pf']:.2f}",
            "순합산손익": f"{res_sr['net_sum']:+.1f}%",
            "매매횟수": f"{res_sr['n_tr']}회"
        }
    ])

    out_csv = f"{RESEARCH_DIR}/msci_new_inclusions_strategies.csv"
    df_out.to_csv(out_csv, index=False)

    print("\n" + "=" * 125)
    print("📊 [MSCI 신규 편입주 대상 전략 비교 실측표]")
    print("=" * 125)
    print(df_out.to_string(index=False))
    print("=" * 125)

if __name__ == "__main__":
    main()
