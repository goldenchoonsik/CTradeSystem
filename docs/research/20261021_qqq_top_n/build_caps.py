"""나스닥100 구성 종목의 당시 시가총액 추정 (2026-10-21)

시총(q) = Yahoo 분할 보정 종가(q, 오늘 단위) × 주식 수(q 이전에 '공시된' 최신 값, 오늘 단위)
주식 수 원천: SEC companyfacts(+ 지주회사 재편 전 옛 CIK: {티커}__pred{CIK}.json)
  개념: 희석/기본 가중평균, 표지 발행주식수(dei), 재무상태표 발행주식수, IFRS 가중평균, 순이익 ÷ 희석 EPS — 전부 모은 뒤
  ① 오늘 단위: 공시일 이후 Yahoo 분할 배수를 곱함(분할 뒤 공시는 과거 기준일 값도 분할 후 단위로 고쳐 적기 때문)
  ② 이상값 제거: 기준일 ±2년 이웃 중앙값과 2.5배 넘게 다르면 버림(천 주 단위 표기, 종류주 일부만 적은 값 등)
  ③ 단위 점프: 남은 계열에서 연속 기준일 비율이 1.8배를 넘으면 앞 구간을 뒤에 맞춤(ADR 비율 변경·보통주 분할)
  ④ 눈금: 현재 구성 종목은 api.nasdaq.com 현재 시총 ÷ 현재가(ADR 은 ADS 기준)에 맞춤, 비현재 ADR 은 ADS_PER_ORD
  ⑤ XBRL 시작 전(대략 2009~2011) 구간은 가장 이른 값을 최대 3년까지 뒤로 채움(주식 수만, 근사)
출력: caps_quarterly.csv (분기말 × 티커, 십억 달러), caps_diag.txt
"""
import os, sys, json, glob
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from ndx_common import ROOT, RENAME
from ndx_membership import members_at

CF = f"{ROOT}/staging/us_fundamentals_sec/raw/companyfacts"
QS = pd.date_range('2009-12-31', '2026-09-30', freq='QE')
ADS_PER_ORD = {'JD': 0.5, 'NTES': 0.2, 'TCOM': 1.0, 'BIDU': 0.125, 'VOD': 0.1, 'INFY': 1.0, 'VIP': 1.0}
# ADR: 공시 주식 수는 '보통주', Yahoo 가격·분할은 'ADS' → Yahoo 분할(ADS 비율 변경)을 보통주에 곱하지 않고 현재 비율만 곱함.
#      보통주 자체 분할(TCOM 2021 8:1 등)은 ③ 단위 점프로 맞춤. 미국 회사는 ③ 미적용(큰 단차 = 실제 합병·상장).
ADR = set(ADS_PER_ORD) | {'AZN', 'PDD', 'ASML', 'ARM'}
CONCEPTS = [('us-gaap', 'WeightedAverageNumberOfDilutedSharesOutstanding'), ('us-gaap', 'WeightedAverageNumberOfSharesOutstandingBasic'),
            ('dei', 'EntityCommonStockSharesOutstanding'), ('us-gaap', 'CommonStockSharesOutstanding'),
            ('ifrs-full', 'AdjustedWeightedAverageShares'), ('ifrs-full', 'WeightedAverageShares')]


def splits(t):
    cp = f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv"
    if not os.path.exists(cp):
        return []
    ca = pd.read_csv(cp, parse_dates=['date'])
    ca = ca[ca.type == 'split'].drop_duplicates('date')
    return [(d, n / m) for d, n, m in zip(ca.date, ca.numerator, ca.denominator)]


def raw_facts(t):
    rows = []
    for fp in [f"{CF}/{t}.json"] + sorted(glob.glob(f"{CF}/{t}__pred*.json")):
        if not os.path.exists(fp):
            continue
        f = json.load(open(fp))['facts']
        for ns, k in CONCEPTS:
            for v in f.get(ns, {}).get(k, {}).get('units', {}).get('shares', []):
                if v.get('val') and v['val'] > 1e6:
                    rows.append((pd.Timestamp(v['end']), pd.Timestamp(v['filed']), float(v['val'])))
        ni = {(x.get('start'), x['end']): x for x in f.get('us-gaap', {}).get('NetIncomeLoss', {}).get('units', {}).get('USD', [])}
        for x in f.get('us-gaap', {}).get('EarningsPerShareDiluted', {}).get('units', {}).get('USD/shares', []):
            key = (x.get('start'), x['end'])
            if key in ni and x['val'] and abs(x['val']) > 0.05 and ni[key]['val'] and ni[key]['val'] / x['val'] > 1e6:
                rows.append((pd.Timestamp(x['end']), pd.Timestamp(max(x['filed'], ni[key]['filed'])), float(ni[key]['val'] / x['val'])))
    return pd.DataFrame(rows, columns=['end', 'filed', 'val'])


def clean_series(t, diag):
    df = raw_facts(t)
    if df.empty:
        return df
    f = np.ones(len(df))
    for d, r in ([] if t in ADR else splits(t)):                              # ① 공시일 이후 분할
        f[df.filed.to_numpy() < np.datetime64(d)] *= r
    df = df.assign(val=df.val * f).sort_values(['end', 'filed']).reset_index(drop=True)
    ends = df.end.to_numpy(); vals = df.val.to_numpy()                         # ② 이웃 중앙값 대비 이상값
    keep = np.ones(len(df), bool)
    for i in range(len(df)):
        w = np.abs((ends - ends[i]).astype('timedelta64[D]').astype(int)) <= 730
        med = np.median(vals[w])
        keep[i] = 1 / 2.5 <= vals[i] / med <= 2.5
    if (~keep).sum():
        diag.append(f"{t}: 이상값 {(~keep).sum()}/{len(df)}개 제외")
    df = df[keep].reset_index(drop=True)
    if t not in ADR:
        return df
    u = df.sort_values('filed').groupby('end').val.first()                    # ③ 단위 점프(ADR 만, 최초 공시값 기준)
    fac = pd.Series(1.0, index=u.index)
    for i in range(len(u) - 1, 0, -1):
        r = u.iloc[i] / u.iloc[i - 1]
        if r > 1.8 or r < 1 / 1.8:
            fac.iloc[:i] *= r
            diag.append(f"{t}: 단위 점프 {u.index[i - 1].date()}→{u.index[i].date()} ×{r:.2f}")
    return df.assign(val=df.val.to_numpy() * fac.reindex(df.end).to_numpy())


def asof(df, q, t=''):
    s = df[df.filed <= q]
    if s.empty:                                                                # ⑤ XBRL 이전: 가장 이른 값 뒤로 채움(3년)
        first = df.sort_values('filed').iloc[0]
        lim = 3300 if t in ADR else 1095       # 외국 기업은 XBRL 시작이 늦음(VOD 2018~) → 9년까지(주식 수 변화 작음, 근사)
        return (first.val, 'backfill') if (first.filed - q).days <= lim else (np.nan, None)
    last_end = s.end.max()
    if (q - last_end).days > 550:
        return np.nan, None
    return s[s.end == last_end].iloc[-1].val, 'ok'


api = json.load(open(os.path.join(HERE, 'data_ndx_wiki', 'nasdaq_api_ndx.json')))['data']['data']['rows']
NOW = {r['symbol'].replace('.', '-'): float(r['marketCap'].replace(',', '')) / float(r['lastSalePrice'].replace('$', '').replace(',', ''))
       for r in api if r['marketCap'] not in ('', 'NA') and r['lastSalePrice']}

if __name__ == '__main__':
    sys.path.insert(0, os.path.join(HERE, '..', '20261021_slot_swing_recompute'))
    from us_data import load
    tick = set()
    for q in QS:
        tick |= {RENAME.get(t, t).replace('.', '-') for t in members_at(q)}
    caps = pd.DataFrame(index=QS, dtype=float)
    diag, nback = [], 0
    for t in sorted(tick):
        px = load(t)
        if px is None:
            continue
        df = clean_series(t, diag)
        if df.empty:
            diag.append(f"{t}: SEC 주식 수 없음"); continue
        last = df.sort_values('end').val.iloc[-1]
        k = NOW[t] / last if t in NOW else ADS_PER_ORD.get(t, 1.0)
        if t in NOW:
            if 1 / 1.6 <= k <= 1.6:        # 국내 상장사 수준 차이 = 최근 증자·자사주 → 과거에 적용 안 함
                if abs(k - 1) > 0.15: diag.append(f"{t}: 현재 시총 차이 ×{k:.3f} (적용 안 함)")
                k = 1.0
            else:                           # ADR 비율·종류주 누락 같은 단위 차이 → 전 구간 적용
                diag.append(f"{t}: 현재 시총 눈금 ×{k:.3f} 적용")
        close = px['close']
        col = []
        for q in QS:
            s, how = asof(df, q, t)
            nback += how == 'backfill'
            c = close.loc[:q]
            col.append(s * k * c.iloc[-1] / 1e9 if len(c) and not np.isnan(s) and (q - c.index[-1]).days < 10 else np.nan)
        caps[t] = col
    caps.to_csv(os.path.join(HERE, 'caps_quarterly.csv'))
    open(os.path.join(HERE, 'caps_diag.txt'), 'w').write('\n'.join(diag))
    print('\n'.join(d for d in diag if '점프' in d or '눈금' in d or '없음' in d))
    print("뒤로 채운 종목·분기 수:", nback)
    for q in ['2010-12-31', '2012-12-31', '2014-12-31', '2016-12-31', '2018-12-31', '2020-12-31', '2022-12-31', '2026-09-30']:
        r = caps.loc[q]
        m = {RENAME.get(t, t).replace('.', '-') for t in members_at(pd.Timestamp(q))}
        priced = {t for t in m if t in caps.columns or os.path.exists(f"{ROOT}/raw/yahoo/candle_1d/{t}_1d.csv")}
        priced = {t for t in priced if (lambda d: d is not None and d.index[0] <= pd.Timestamp(q))(load(t))}
        r = r[r.index.isin(m)].dropna().sort_values(ascending=False)
        print(q, "명단", len(m), "가격", len(priced), "시총", len(r), "상위 15:", r.head(15).round(0).astype(int).to_dict())
        print("   가격 있는데 시총 없음:", sorted(priced - set(r.index)))
