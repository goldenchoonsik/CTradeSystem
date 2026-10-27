"""Yahoo 애널리스트 등급·목표주가 변경 이력 수집 (yfinance upgrades_downgrades). 원본 그대로 저장.
출력 DataVault/staging/us_analyst_actions/raw/{ticker}.csv. 이미 있으면 건너뜀.
실행: PYTHONPATH=<yfinance 설치 경로> .venv/bin/python -I fetch.py [티커...|--all]   (-I 와 PYTHONPATH 충돌 시 -I 빼고 실행)"""
import os, sys, time, warnings
import pandas as pd
warnings.filterwarnings('ignore')
import yfinance as yf
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
OUT = os.path.join(REPO, 'DataVault', 'staging', 'us_analyst_actions', 'raw')
os.makedirs(OUT, exist_ok=True)
args = sys.argv[1:]
if args == ['--all']:
    E = pd.read_csv(os.path.join(REPO, 'docs/research/20261023_revenue_vs_price/events.csv.gz'))
    tickers = sorted(E.ticker.unique())
else:
    tickers = args
fail = []
for i, t in enumerate(tickers):
    fp = os.path.join(OUT, f'{t}.csv')
    if os.path.exists(fp): continue
    for attempt in range(3):
        try:
            d = yf.Ticker(t).upgrades_downgrades
            if d is None: d = pd.DataFrame()
            d.to_csv(fp)
            break
        except Exception as e:  # noqa: BLE001
            time.sleep(5 * (attempt + 1))
    else:
        fail.append(t)
    time.sleep(0.7)
    if (i + 1) % 50 == 0: print(i + 1, '/', len(tickers), 'fail', len(fail), flush=True)
print('끝', len(tickers), '실패', fail)
