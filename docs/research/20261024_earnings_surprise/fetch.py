"""Nasdaq api earnings-surprise (컨센서스 EPS vs 실제, 종목당 최근 4분기) 수집. 원본 그대로 저장.
실행: .venv/bin/python -I fetch.py"""
import json, os, time, urllib.request
import pandas as pd
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
OUT = os.path.join(REPO, 'DataVault/staging/us_earnings_surprise/raw'); os.makedirs(OUT, exist_ok=True)
E = pd.read_csv(os.path.join(REPO, 'docs/research/20261023_revenue_vs_price/events.csv.gz'))
tickers = sorted(E.ticker.unique()); fail = []
for i, t in enumerate(tickers):
    fp = os.path.join(OUT, f'{t}.json')
    if os.path.exists(fp): continue
    for a in range(3):
        try:
            req = urllib.request.Request(f'https://api.nasdaq.com/api/company/{t.replace("-", ".")}/earnings-surprise', headers={'User-Agent': 'Mozilla/5.0'})
            body = urllib.request.urlopen(req, timeout=20).read()
            json.loads(body); open(fp, 'wb').write(body); break
        except Exception:
            time.sleep(3 * (a + 1))
    else: fail.append(t)
    time.sleep(0.4)
    if (i + 1) % 100 == 0: print(i + 1, len(fail), flush=True)
print('끝', len(tickers), '실패', len(fail), fail[:30])
