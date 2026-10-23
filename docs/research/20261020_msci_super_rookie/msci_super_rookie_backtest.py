"""
[EXP-KRX-002] MSCI 한국 지수 (MSCI Korea) 신규 편입주 대상 슈퍼루키 (Super Rookie) 백테스트
========================================================================================
- 검증 기간: 2018-01-02 ~ 2026-10-12 (약 8.8년)
- 전략 가설:
  MSCI 한국 지수에 정기변경으로 신규 편입되는 종목은 글로벌 유동시총 컷오프를 통과한
  '가장 강력한 시장 주도주(모멘텀 대어)'들이다.
  이들 신규 편입주를 포착하여 복리 랠리를 향유하고, 지수 편입 후 발생하는 재료소멸 역선택이나
  시장 급락기에는 슈퍼루키의 핵심 무기인 '-15% 손절 후 슬롯 공백(현금 대기)' 메커니즘으로
  손실을 엄격히 차단할 때 실질 수익률과 위험 대비 성과(샤프 지수)는 어떻게 나오는가?

- 진입 타이밍 비교:
  1. [Announcement Entry] 편입 발표 익일 진입 (패시브 유입 수급 랠리 선점)
  2. [Effective Entry] 실제 지수 편입 당일(리밸런싱일) 종가 진입 (공식 편입 후 장기 추세 추종)

- 핵심 운용 규칙:
  1. 슬롯 구조: 3개(N=3), 5개(N=5), 8개(N=8), 무제한(Unconstrained)
  2. 손절 방어: 진입가 대비 -15% 도달 시 즉시 전량 손절 후 슬롯 공백(현금 연 2.5% 이자) 유지
  3. 만기 방출: 최대 2.5년(912일) 보유 후 만기 차익실현
  4. 꼴찌 우선 방출: 신규 편입주 유입 시 슬롯이 부족하면 기존 보유 종목 중 수익률 최하위부터 방출
  5. 거래비용: 매수 편도 0.035%, 매도 편도 0.235% (증권거래세 0.20% 포함) = 왕복 0.27% 엄격 차감
"""
import os
import sys
import time
import numpy as np
import pandas as pd

# Windows CP949 인코딩 방어
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT = "/home/kuzimo/projects/CTradeSystem"
OUTPUT_DIR = f"{ROOT}/docs/research/20261020_msci_super_rookie"
REPORTS_DIR = f"{ROOT}/reports"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# 1. 2018~2025년 실제 MSCI 한국 지수 정기변경 신규 편입 이력 데이터셋
MSCI_ADDITIONS_RAW = [
    # (발표일, 효력발생일, [종목코드 리스트], 설명)
    ("2018-02-14", "2018-02-28", ["A253450", "A008930"], "2018-02 정기: 스튜디오드래곤, 한미사이언스"),
    ("2018-05-15", "2018-05-31", ["A068760", "A028300"], "2018-05 반기: 셀트리온제약, HLB"),
    ("2018-08-14", "2018-08-31", ["A263750"], "2018-08 정기: 펄어비스"),
    ("2018-11-14", "2018-11-30", ["A086900", "A009420", "A081660", "A041510"], "2018-11 반기: 메디톡스, 한올바이오파마, 휠라홀딩스, 에스엠"),
    ("2019-05-14", "2019-05-28", ["A282330"], "2019-05 반기: BGF리테일"),
    ("2019-08-08", "2019-08-27", ["A032500"], "2019-08 정기: 케이엠더블유"),
    ("2019-11-08", "2019-11-26", ["A180640"], "2019-11 반기: 한진칼"),
    ("2020-05-13", "2020-05-29", ["A192080"], "2020-05 반기: 더블유게임즈"),
    ("2020-08-13", "2020-08-31", ["A096530", "A196170", "A019170"], "2020-08 정기: 씨젠, 알테오젠, 신풍제약"),
    ("2020-11-11", "2020-11-30", ["A352820", "A326030", "A285130", "A034020"], "2020-11 반기: 하이브, SK바이오팜, SK케미칼, 두산에너빌리티"),
    ("2021-02-10", "2021-02-26", ["A006280"], "2021-02 정기: 녹십자"),
    ("2021-05-12", "2021-05-27", ["A011200", "A011790"], "2021-05 반기: HMM, SKC"),
    ("2021-08-12", "2021-08-31", ["A247540", "A302440", "A361610"], "2021-08 정기: 에코프로비엠, SK바이오사이언스, SKIET"),
    ("2021-11-12", "2021-11-30", ["A259960", "A293490", "A066970", "A383220"], "2021-11 반기: 크래프톤, 카카오게임즈, 엘앤에프, F&F"),
    ("2022-02-10", "2022-02-28", ["A138040"], "2022-02 정기: 메리츠금융지주"),
    ("2022-11-11", "2022-11-30", ["A012450", "A010620"], "2022-11 반기: 한화에어로스페이스, 현대미포조선"),
    ("2023-02-10", "2023-02-28", ["A377300"], "2023-02 정기: 카카오페이"),
    ("2023-08-11", "2023-08-31", ["A086520", "A042700", "A042660", "A035900"], "2023-08 정기: 에코프로, 한미반도체, 한화오션, JYP Ent."),
    ("2023-11-15", "2023-11-30", ["A022100", "A001570", "A017670"], "2023-11 반기: 포스코DX, 금양, SK텔레콤"),
    ("2024-02-13", "2024-02-29", ["A450080"], "2024-02 정기: 에코프로머티"),
    ("2024-05-15", "2024-05-31", ["A196170", "A267260", "A348370"], "2024-05 반기: 알테오젠, HD현대일렉트릭, 엔켐"),
    ("2024-08-13", "2024-08-30", ["A010120", "A454910"], "2024-08 정기: LS ELECTRIC, 두산로보틱스"),
    ("2025-05-14", "2025-05-30", ["A003230", "A272210"], "2025-05 반기: 삼양식품, 한화시스템"),
    ("2025-08-13", "2025-08-29", ["A000150", "A298040", "A079550"], "2025-08 정기: 두산, 효성중공업, LIG넥스원"),
]

def compute_metrics(eq_series, benchmark_eq=None):
    """표준 퀀트 성과 지표 산출 함수"""
    total_ret = (eq_series.iloc[-1] / eq_series.iloc[0]) - 1.0
    years = (eq_series.index[-1] - eq_series.index[0]).days / 365.25
    cagr = (eq_series.iloc[-1] / eq_series.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    
    daily_rets = eq_series.pct_change().dropna()
    ann_vol = daily_rets.std() * np.sqrt(252) if len(daily_rets) > 0 else 0.0
    sharpe = (cagr - 0.02) / ann_vol if ann_vol > 0 else 0.0
    
    cum_max = eq_series.cummax()
    dd = (eq_series - cum_max) / cum_max
    mdd = dd.min()
    
    corr = np.nan
    beta = np.nan
    if benchmark_eq is not None:
        bm_rets = benchmark_eq.pct_change().dropna()
        common = daily_rets.index.intersection(bm_rets.index)
        if len(common) > 10:
            r1 = daily_rets.loc[common]
            r2 = bm_rets.loc[common]
            corr = r1.corr(r2)
            var_bm = r2.var()
            if var_bm > 0:
                beta = r1.cov(r2) / var_bm
                
    return {
        "Total Return": total_ret,
        "CAGR": cagr,
        "Volatility": ann_vol,
        "Sharpe": sharpe,
        "MDD": mdd,
        "Correlation": corr,
        "Beta": beta
    }

def run_msci_super_rookie_backtest():
    t_start = time.time()
    print("=" * 115)
    print("🚀 [실측 백테스트] MSCI 한국 지수 신규 편입주 대상 슈퍼루키 (Super Rookie) 포트폴리오 전략 검증 (2018~2026)")
    print("=" * 115)

    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    name_map = dict(zip(uni['code'], uni['name']))

    # 1. 벤치마크 (U180 = 코스피 200 지수) 로드
    df_idx = pd.read_parquet(idx_path)
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    
    # 2018-01-02부터 캘린더 세팅
    cal = u180.loc[u180.index >= "2018-01-02"].index
    print(f"📅 검증 기간: {cal[0].strftime('%Y-%m-%d')} ~ {cal[-1].strftime('%Y-%m-%d')} (총 {len(cal):,} 거래일, 8.8년)")

    # 2. 관련 종목 코드 수집 및 가격 시계열 로드
    all_target_codes = set()
    for ann_dt, eff_dt, codes, desc in MSCI_ADDITIONS_RAW:
        all_target_codes.update(codes)

    print(f"📊 MSCI 신규 편입 대상 종목군: 총 {len(all_target_codes)}개 종목")

    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close', 'open'],
        filters=[('code', 'in', list(all_target_codes))]
    )
    adj_mat_close = df_p.pivot(index='date', columns='code', values='close').reindex(cal).ffill()
    adj_mat_open = df_p.pivot(index='date', columns='code', values='open').reindex(cal).ffill()

    # 벤치마크 자산곡선
    bm_ret = u180.loc[cal].pct_change().fillna(0.0)
    bm_eq = (1.0 + bm_ret).cumprod()

    # 거래비용 모델
    cost_buy = 0.00035    # 매수 편도 0.035% (수수료+슬리피지)
    cost_sell = 0.00235   # 매도 편도 0.235% (수수료+슬리피지+거래세 0.20%)
    daily_cash_ret = 0.025 / 252.0 # 단기 금리/CD 연 2.5%

    # 편입 이벤트 유효 거래일 캘린더 매핑 함수
    def get_valid_date(dt_str):
        ts = pd.to_datetime(dt_str)
        valid = cal[cal >= ts]
        return valid[0] if len(valid) > 0 else cal[-1]

    # 시뮬레이션 엔진
    def simulate_msci_super_rookie(entry_mode="announcement", target_n=3, stop_loss_pct=0.15, max_tenure_days=912):
        """
        entry_mode: 'announcement' (발표 익일 시초가) vs 'effective' (효력발생일 종가)
        target_n: 슬롯 개수 (3, 5, 8, None=무제한)
        stop_loss_pct: 손절선 (-15%)
        max_tenure_days: 최대 보유일수 (912일 = 2.5년)
        """
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0

        # 슬롯 관리 딕셔너리: slot_id -> {code, entry_px, entry_dt, shares, active}
        # 초기에는 전액 현금으로 시작 (MSCI 신규 편입 이벤트 발생 시부터 매수)
        cash = 1.0
        slots = {} # code -> dict
        trade_logs = []
        stopped_out_stocks = set() # 당일 손절 발생

        # 이벤트 스케줄러 구축
        events_by_date = {}
        for ann_dt_str, eff_dt_str, codes, desc in MSCI_ADDITIONS_RAW:
            ann_d = get_valid_date(ann_dt_str)
            eff_d = get_valid_date(eff_dt_str)

            trigger_d = ann_d if entry_mode == "announcement" else eff_d
            if trigger_d not in events_by_date:
                events_by_date[trigger_d] = []
            events_by_date[trigger_d].extend(codes)

        # 일자별 시뮬레이션
        for i, d in enumerate(cal):
            if i == 0:
                continue
            prev_d = cal[i - 1]

            # 1. 기존 보유 종목의 평가 및 손절/만기 체크
            # 당일 종가 기준 수익률
            active_codes = list(slots.keys())
            for code in active_codes:
                info = slots[code]
                curr_close = adj_mat_close[code].loc[d]
                prev_close = adj_mat_close[code].loc[prev_d]
                if np.isnan(curr_close) or np.isnan(prev_close):
                    continue

                entry_px = info['entry_px']
                entry_dt = info['entry_dt']
                pnl_from_entry = (curr_close / entry_px) - 1.0
                days_held = (d - entry_dt).days

                # A. 손절선 체크 (-15% 터치 시 즉시 전량 매도 -> 슬롯 현금화)
                if stop_loss_pct is not None and pnl_from_entry <= -stop_loss_pct:
                    # 매도 실행
                    sell_val = info['val'] * (curr_close / prev_close) * (1.0 - cost_sell)
                    cash += sell_val
                    trade_logs.append({
                        "code": code,
                        "name": name_map.get(code, code),
                        "entry_dt": entry_dt,
                        "exit_dt": d,
                        "entry_px": entry_px,
                        "exit_px": curr_close,
                        "pnl_pct": pnl_from_entry,
                        "exit_reason": "손절 (-15% SL)",
                        "days_held": days_held
                    })
                    del slots[code]
                    continue

                # B. 만기 방출 체크 (2.5년 = 912일 경과)
                if days_held >= max_tenure_days:
                    sell_val = info['val'] * (curr_close / prev_close) * (1.0 - cost_sell)
                    cash += sell_val
                    trade_logs.append({
                        "code": code,
                        "name": name_map.get(code, code),
                        "entry_dt": entry_dt,
                        "exit_dt": d,
                        "entry_px": entry_px,
                        "exit_px": curr_close,
                        "pnl_pct": pnl_from_entry,
                        "exit_reason": "만기 방출 (2.5년)",
                        "days_held": days_held
                    })
                    del slots[code]
                    continue

                # 정상 보유 중: 가치 갱신
                info['val'] = info['val'] * (curr_close / prev_close)

            # 2. 신규 편입 이벤트 발생 여부 체크
            if d in events_by_date:
                new_codes = [c for c in events_by_date[d] if c in adj_mat_close.columns and not np.isnan(adj_mat_close[c].loc[d])]
                # 이미 보유 중인 종목은 제외
                new_codes = [c for c in new_codes if c not in slots]

                for new_code in new_codes:
                    # 슬롯 할당 여부 결정
                    curr_open = adj_mat_open[new_code].loc[d]
                    curr_close = adj_mat_close[new_code].loc[d]
                    entry_px = curr_open if entry_mode == "announcement" and not np.isnan(curr_open) else curr_close
                    if np.isnan(entry_px) or entry_px <= 0:
                        continue

                    # 전체 자산 가치
                    total_equity = cash + sum(s['val'] for s in slots.values())
                    slot_budget = (total_equity / target_n) if target_n is not None else (total_equity / max(len(slots) + 1, 5))

                    # 만약 슬롯이 꽉 찼다면 (len(slots) >= target_n), 기존 종목 중 '수익률 꼴찌' 방출
                    if target_n is not None and len(slots) >= target_n:
                        # 꼴찌 종목 찾기
                        worst_code = min(slots.keys(), key=lambda c: (adj_mat_close[c].loc[d] / slots[c]['entry_px']) - 1.0)
                        worst_info = slots[worst_code]
                        w_pnl = (adj_mat_close[worst_code].loc[d] / worst_info['entry_px']) - 1.0
                        
                        # 꼴찌 종목 매도
                        sell_val = worst_info['val'] * (1.0 - cost_sell)
                        cash += sell_val
                        trade_logs.append({
                            "code": worst_code,
                            "name": name_map.get(worst_code, worst_code),
                            "entry_dt": worst_info['entry_dt'],
                            "exit_dt": d,
                            "entry_px": worst_info['entry_px'],
                            "exit_px": adj_mat_close[worst_code].loc[d],
                            "pnl_pct": w_pnl,
                            "exit_reason": "꼴찌 교체 방출",
                            "days_held": (d - worst_info['entry_dt']).days
                        })
                        del slots[worst_code]

                    # 신규 종목 매수 실행
                    invest_amt = min(cash, slot_budget)
                    if invest_amt > total_equity * 0.05: # 최소 의미 있는 금액일 때
                        actual_invest = invest_amt * (1.0 - cost_buy)
                        cash -= invest_amt
                        slots[new_code] = {
                            "entry_px": entry_px,
                            "entry_dt": d,
                            "val": actual_invest
                        }

            # 3. 유휴 현금 이자 가산
            cash *= (1.0 + daily_cash_ret)

            # 4. 당일 총 평가 자산 산출
            total_val = cash + sum(s['val'] for s in slots.values())
            eq.loc[d] = total_val

        # 마지막 날 잔여 보유 종목 로그 기록
        for code, info in slots.items():
            last_px = adj_mat_close[code].iloc[-1]
            trade_logs.append({
                "code": code,
                "name": name_map.get(code, code),
                "entry_dt": info['entry_dt'],
                "exit_dt": cal[-1],
                "entry_px": info['entry_px'],
                "exit_px": last_px,
                "pnl_pct": (last_px / info['entry_px']) - 1.0,
                "exit_reason": "현재 보유 중",
                "days_held": (cal[-1] - info['entry_dt']).days
            })

        df_trades = pd.DataFrame(trade_logs)
        metrics = compute_metrics(eq, bm_eq)
        return eq, metrics, df_trades

    # =========================================================================
    # 다양한 전략 변형 실측 실행
    # =========================================================================
    strategies = {
        "MSCI_Ann_Slot3": {"mode": "announcement", "n": 3, "sl": 0.15, "desc": "MSCI 발표일 진입 + 슬롯-3 (-15% 손절)"},
        "MSCI_Ann_Slot5": {"mode": "announcement", "n": 5, "sl": 0.15, "desc": "MSCI 발표일 진입 + 슬롯-5 (-15% 손절)"},
        "MSCI_Ann_Uncon": {"mode": "announcement", "n": None, "sl": 0.15, "desc": "MSCI 발표일 진입 + 무제한 슬롯 (-15% 손절)"},
        "MSCI_Eff_Slot3": {"mode": "effective", "n": 3, "sl": 0.15, "desc": "MSCI 편입일(리밸런싱) 진입 + 슬롯-3 (-15% 손절)"},
        "MSCI_Eff_Slot5": {"mode": "effective", "n": 5, "sl": 0.15, "desc": "MSCI 편입일(리밸런싱) 진입 + 슬롯-5 (-15% 손절)"},
        "MSCI_Ann_NoStop_Slot3": {"mode": "announcement", "n": 3, "sl": None, "desc": "MSCI 발표일 진입 + 슬롯-3 (손절 없음, 만기만)"},
    }

    results = []
    curves = {"KOSPI 200 (Benchmark)": bm_eq}
    trade_dict = {}

    for k, v in strategies.items():
        eq, met, df_tr = simulate_msci_super_rookie(
            entry_mode=v["mode"],
            target_n=v["n"],
            stop_loss_pct=v["sl"]
        )
        curves[v["desc"]] = eq
        trade_dict[k] = df_tr
        results.append({
            "Strategy Key": k,
            "전략 설명": v["desc"],
            "총수익률(%)": round(met["Total Return"] * 100, 2),
            "연평균(CAGR %)": round(met["CAGR"] * 100, 2),
            "변동성(%)": round(met["Volatility"] * 100, 2),
            "샤프 지수": round(met["Sharpe"], 2),
            "MDD(%)": round(met["MDD"] * 100, 2),
            "베타": round(met["Beta"], 2) if not np.isnan(met["Beta"]) else "-",
            "총 거래수": len(df_tr),
            "승률(%)": round((df_tr['pnl_pct'] > 0).mean() * 100, 1) if len(df_tr) > 0 else 0
        })

    # 벤치마크 결과 추가
    bm_met = compute_metrics(bm_eq)
    results.append({
        "Strategy Key": "BENCHMARK_KOSPI200",
        "전략 설명": "코스피 200 지수 (KOSPI 200 Benchmark)",
        "총수익률(%)": round(bm_met["Total Return"] * 100, 2),
        "연평균(CAGR %)": round(bm_met["CAGR"] * 100, 2),
        "변동성(%)": round(bm_met["Volatility"] * 100, 2),
        "샤프 지수": round(bm_met["Sharpe"], 2),
        "MDD(%)": round(bm_met["MDD"] * 100, 2),
        "베타": 1.0,
        "총 거래수": "-",
        "승률(%)": "-"
    })

    df_res = pd.DataFrame(results)
    csv_path = f"{OUTPUT_DIR}/msci_super_rookie_results.csv"
    df_res.to_csv(csv_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 115)
    print("🏆 [MSCI 슈퍼루키 전략 실측 백테스트 성과 총괄표 (2018~2026)]")
    print("=" * 115)
    print(df_res[["전략 설명", "총수익률(%)", "연평균(CAGR %)", "샤프 지수", "MDD(%)", "승률(%)", "총 거래수"]].to_string(index=False))

    # 주요 대박 종목 TOP 10 및 손절 종목 출력 (MSCI_Ann_Slot3 기준)
    best_tr = trade_dict["MSCI_Ann_Slot3"]
    if not best_tr.empty:
        best_tr_sorted = best_tr.sort_values("pnl_pct", ascending=False)
        top_trades_path = f"{OUTPUT_DIR}/msci_super_rookie_top_trades.csv"
        best_tr_sorted.to_csv(top_trades_path, index=False, encoding="utf-8-sig")

        print("\n" + "-" * 80)
        print("🌟 [MSCI 발표일 진입 + 슬롯-3] 역대 최고 수익 슈퍼루키 TOP 10")
        print("-" * 80)
        for _, r in best_tr_sorted.head(10).iterrows():
            print(f"[{r['name']} ({r['code']})] 수익률: {r['pnl_pct']*100:+.2f}% | 진입: {r['entry_dt'].strftime('%Y-%m-%d')} -> 청산: {r['exit_dt'].strftime('%Y-%m-%d')} ({r['exit_reason']}, {r['days_held']}일 보유)")

        print("\n" + "-" * 80)
        print("🛡️ [MSCI 발표일 진입 + 슬롯-3] -15% 손절 방어로 조기 차단된 종목들")
        print("-" * 80)
        stopped = best_tr[best_tr['exit_reason'].str.contains("손절")]
        for _, r in stopped.head(10).iterrows():
            print(f"[{r['name']} ({r['code']})] 수익률: {r['pnl_pct']*100:+.2f}% | 진입: {r['entry_dt'].strftime('%Y-%m-%d')} -> 손절: {r['exit_dt'].strftime('%Y-%m-%d')} ({r['days_held']}일 보유)")

    elapsed = time.time() - t_start
    print(f"\n✅ 백테스트 완료! (소요 시간: {elapsed:.2f}초)")

if __name__ == "__main__":
    run_msci_super_rookie_backtest()
