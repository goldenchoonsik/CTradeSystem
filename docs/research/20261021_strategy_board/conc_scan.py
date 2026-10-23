"""대형주 집중형 ETF 비교 (2026-10-21) — '상위 몇 종목에 몰수록 수익률이 좋은가'
상장 시점이 제각각이라 창(window)별로 같은 시작일에 비교한다. 조건은 etf_scan.py 와 같다(원화, 세후 미국 직투 22%).
집중도: QQQE(나스닥100 동일가중) < QQQ(100, 시총가중) < OEF(S&P 100) < XLG(S&P 상위 50) < MGK(메가캡 성장 ~70)
        < QTOP(상위 30) < TOPT(상위 20) < FNGS(FANG+ 10, 동일가중) < MAGS(7)
실행: .venv/bin/python docs/research/20261021_strategy_board/conc_scan.py [--end=2023-12-31]
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import pandas as pd
import etf_scan as es, board as bd
WINDOWS = [
    ('2007-12-31', ['QQQ', 'MGK', 'XLG', 'OEF', 'XLK', 'ONEQ', 'SPY', 'RSP']),
    ('2012-04-30', ['QQQ', 'QQQE', 'MGK', 'IWY', 'XLG', 'OEF', 'SPY', 'RSP']),
    ('2019-11-29', ['FNGO', 'FNGS', 'QQQ', 'MGK', 'XLG', 'QQQE', 'SPY']),
    ('2023-04-28', ['MAGS', 'FNGS', 'QQQ', 'MGK', 'XLG', 'SPY']),
    ('2024-10-31', ['TOPT', 'QTOP', 'MAGS', 'FNGS', 'QQQ', 'SPY']),
]
if __name__ == '__main__':
    for st, ts in WINDOWS:
        if pd.Timestamp(st) >= bd.END - pd.Timedelta(days=300): continue
        df = es.table({'': ts}, st).drop(columns=['분류', '이상봉'])
        print(f"=== {st} ~ {bd.END.date()} ==="); print(df.to_string(index=False))
