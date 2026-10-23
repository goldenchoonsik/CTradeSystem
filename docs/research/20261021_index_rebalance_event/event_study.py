"""지수 정기변경(MSCI 한국·코스피200) 편입·편출 사건 연구 (2026-10-21)

질문: 제미나이 '리밸런싱 이벤트 드리븐' 주장 — 편입 전 선취매 → 편입일 종가 매도가 남는가, 편입일 이후 되돌림은 얼마인가.
명단: msci_korea_changes.csv / kospi200_changes.csv (공식 발표·기사 대조로 수집, 출처 열 포함).
가격: staging/kr_equity prices_1d(수정 시가·종가, 상폐 포함), 기준 = 코스피200(U180) 같은 시점 가격.

구간 (E0 = 발표 내용을 알고 처음 거래할 수 있는 날, R = 패시브가 종가에 체결하는 날):
  A40   : R-40 종가 → E0-1 종가   발표 전 선취매(결과를 아는 명단이라 상한값, 탈락 후보 미포함)
  A2    : 심사 자료 마감일 종가 → E0-1 종가   편입 여부가 공개 자료로 사실상 정해진 뒤의 선취매
          (MSCI = 발표 전달 마지막 거래일 — 가격 기준일이 그 달 마지막 10영업일 중 하루,
           코스피200 = 효력 두 달 전 말일(4월·10월 말) — 심사기준일)
  FULL  : R-40 종가 → R 종가     제미나이 원안(40일 전 매수 → 편입일 종가 매도), 완벽한 예측 가정
  A2R   : 심사 자료 마감일 종가 → R 종가   현실형 선취매(자료로 정해진 뒤 매수 → 편입일 종가 매도), 예측 오류 미반영
  GAP   : E0-1 종가 → E0 시가     발표 반응(아무도 못 먹는 몫)
  B     : E0 시가 → R 종가        발표 확인 후 매수 → 편입일 종가 매도 (예측 불필요)
  RDAY  : R-1 종가 → R 종가       편입일 당일(동시호가 포함)
  C1/C5/C20 : R 종가 → R+1 시가 / R+5 / R+20 종가   편입일 이후 되돌림
초과수익 = 종목 수익률 − 같은 시점 코스피200 수익률. t 는 같은 회차 종목끼리 묶어(회차 평균) 회차 간 계산.
비용(B 실매매): 매수 0.05% + 매도 0.07% + 연도별 거래세.
"""
import os
import sys
import numpy as np
import pandas as pd

ROOT = '/home/kuzimo/projects/CTradeSystem'
HERE = f'{ROOT}/docs/research/20261021_index_rebalance_event'
DV = f'{ROOT}/DataVault/staging/kr_equity'


def tax_kr(d):
    if d < pd.Timestamp('2019-05-30'): return 0.0030
    if d.year <= 2020: return 0.0025
    if d.year <= 2022: return 0.0023
    return {2023: 0.0020, 2024: 0.0018, 2025: 0.0015}.get(d.year, 0.0020)


def load_events():
    parts = []
    if os.path.exists(f'{HERE}/msci_korea_changes.csv'):
        m = pd.read_csv(f'{HERE}/msci_korea_changes.csv', dtype=str)
        m = m[m.action.isin(['add', 'delete'])].copy()
        m['index'] = 'MSCI'
        m['e0_date'] = pd.to_datetime(m.first_trade_date_kst)
        m['e0_rule'] = 'same'                 # 미국 장 마감 후 발표 → 한국 다음 날 시가부터 거래 가능
        m['r_date'] = pd.to_datetime(m.effective_close_date)
        parts.append(m)
    k = pd.read_csv(f'{HERE}/kospi200_changes.csv', dtype=str)
    k = k[k.action.isin(['add', 'delete'])].copy()
    k['index'] = 'K200'
    k['e0_date'] = pd.to_datetime(k.announce_date_kst)
    note = k.announce_time_note.fillna('unknown').str.lower()
    k['e0_rule'] = np.where(note.str.contains('before'), 'same', 'next')   # 장전 발표만 당일, 나머지는 보수적으로 다음 날
    k['r_date'] = pd.to_datetime(k.rebalance_trade_date)
    parts.append(k)
    cols = ['index', 'review', 'type', 'action', 'code', 'name', 'e0_date', 'e0_rule', 'r_date', 'confidence', 'note']
    ev = pd.concat([p[cols] for p in parts], ignore_index=True)
    ev = ev[ev.r_date >= pd.Timestamp('2013-03-01')]
    # 패시브 매매가 없는 사건: 거래정지 중 제외, 인적분할 신설회사 편입(지수 펀드가 이미 보유)
    note = ev.note.fillna('').str.lower()
    ev['no_flow'] = note.str.contains('no normal rebalance trade') | note.str.contains('already held')
    ev['main'] = ev.type.isin(['regular', 'quarterly', 'semi_annual']) & ~ev.no_flow
    return ev


def main(bench='k200'):
    """bench='k200' → 코스피200 대비, 'size' → 규모 맞춤 지수 대비(_s 열)."""
    ev = load_events()
    idx = pd.read_parquet(f'{DV}/derived/index_1d.parquet')
    k2 = idx[idx.code == 'U180'].set_index('date').sort_index()
    cal = k2.index
    B_ = {c: g.set_index('date').sort_index().reindex(cal) for c, g in idx[idx.code.isin(['U180', 'U003', 'U155', 'U202'])].groupby('code')}
    mkt = pd.read_csv(f'{DV}/universe.csv').set_index('code').market.to_dict()
    px = pd.read_parquet(f'{DV}/derived/prices_1d.parquet', columns=['code', 'date', 'open', 'close', 'value'],
                         filters=[('code', 'in', sorted(ev.code.unique()))])
    O = px.pivot(index='date', columns='code', values='open').reindex(cal)
    C = px.pivot(index='date', columns='code', values='close').reindex(cal)
    V = px.pivot(index='date', columns='code', values='value').reindex(cal)

    rows = []
    for _, e in ev.iterrows():
        if e.code not in C.columns:
            rows.append(dict(e, missing='no_price')); continue
        i0 = cal.searchsorted(e.e0_date, 'left')
        if e.e0_rule == 'next' and i0 < len(cal) and cal[i0] == e.e0_date: i0 += 1
        r = cal.searchsorted(e.r_date, 'right') - 1
        if i0 >= len(cal) or r < 0 or r < i0 or r + 1 >= len(cal):
            rows.append(dict(e, missing='bad_dates')); continue
        o, c = O[e.code].values, C[e.code].values
        # 규모 맞춤 기준: 코스피200 사건 = 코스피 중형주, MSCI = 코스피 종목은 코스피200 동일가중·코스닥 종목은 코스닥 대형주
        sb_code = 'U003' if e['index'] == 'K200' else ('U202' if mkt.get(e.code) == 'KOSDAQ' else 'U155')

        def ex(sa, ia, sb, ib, bench='U180'):   # 종목 − 기준 지수, s∈{'o','c'}
            if ia < 0 or ib >= len(cal): return np.nan
            pa = (o if sa == 'o' else c)[ia]; pb = (o if sb == 'o' else c)[ib]
            bo, bc = B_[bench].open.values, B_[bench].close.values
            ka = (bo if sa == 'o' else bc)[ia]; kb = (bo if sb == 'o' else bc)[ib]
            if not (pa > 0 and pb > 0): return np.nan
            return (pb / pa - 1) - (kb / ka - 1)

        ref = (e.e0_date - pd.offsets.MonthBegin(1)) if e['index'] == 'MSCI' else (e.r_date - pd.offsets.MonthBegin(2))
        a2 = cal.searchsorted(ref.normalize().replace(day=1), 'left') - 1   # 그 달 1일 직전 거래일 = 전달 말일
        a2 = a2 if a2 < i0 - 1 else -1

        def win(bench):
            return dict(A40=ex('c', r - 40, 'c', i0 - 1, bench), A2=ex('c', a2, 'c', i0 - 1, bench) if a2 >= 0 else np.nan,
                        FULL=ex('c', r - 40, 'c', r, bench), A2R=ex('c', a2, 'c', r, bench) if a2 >= 0 else np.nan, GAP=ex('c', i0 - 1, 'o', i0, bench),
                        B=ex('o', i0, 'c', r, bench), RDAY=ex('c', r - 1, 'c', r, bench),
                        C1=ex('c', r, 'o', r + 1, bench), C5=ex('c', r, 'c', min(r + 5, len(cal) - 1), bench),
                        C20=ex('c', r, 'c', r + 20, bench) if r + 20 < len(cal) else np.nan)

        def raw(sa, ia, sb, ib):
            if ia < 0 or ib >= len(cal): return np.nan
            pa = (o if sa == 'o' else c)[ia]; pb = (o if sb == 'o' else c)[ib]
            return pb / pa - 1 if (pa > 0 and pb > 0) else np.nan

        cost = 0.0005 + 0.0007 + tax_kr(cal[r])
        rows.append(dict(e, e0=cal[i0], r=cal[r], gap_days=r - i0, bench_size=sb_code, B_raw=raw('o', i0, 'c', r),
                         **win('U180'), **{f'{k}_s': v for k, v in win(sb_code).items()}, cost=cost, value_r=V[e.code].values[r], value_med20=np.nanmedian(V[e.code].values[max(0, r - 25):r - 5])))
    d = pd.DataFrame(rows)
    d.to_csv(f'{HERE}/event_returns.csv', index=False, encoding='utf-8-sig')
    bad = d[d.get('missing').notna()] if 'missing' in d else d.iloc[:0]
    print(f'사건 {len(d)}건, 가격 결측/날짜 오류 {len(bad)}건')
    if len(bad): print(bad[['index', 'review', 'action', 'code', 'name', 'missing']].to_string())
    d = d[d.get('missing').isna()] if 'missing' in d else d
    d['period'] = np.where(d.r.dt.year <= 2017, '2013-17', np.where(d.r.dt.year <= 2021, '2018-21', '2022-26'))
    if bench == 'size':
        for k in ['A40', 'A2', 'FULL', 'A2R', 'GAP', 'B', 'RDAY', 'C1', 'C5', 'C20']: d[k] = d[f'{k}_s']
    d['B_net'] = d.B - d.cost
    d['A2R_net'] = d.A2R - d.cost
    d['C1_net'] = d.C1 - d.cost
    print(f'\n##### 기준: {"코스피200" if bench == "k200" else "규모 맞춤(K200 사건=코스피 중형주, MSCI=K200 동일가중/코스닥 대형주)"}')

    def summ(g, col):
        x = g[col].dropna()
        if len(x) == 0: return None
        rv = g.groupby('review')[col].mean().dropna()
        t = rv.mean() / rv.std(ddof=1) * np.sqrt(len(rv)) if len(rv) > 2 else np.nan
        return f'{x.mean()*100:+6.2f} {x.median()*100:+6.2f} {(x>0).mean()*100:4.0f}% t{t:+5.2f} n{len(x)}/{len(rv)}'

    cols = ['A40', 'A2', 'FULL', 'A2R', 'A2R_net', 'GAP', 'B', 'B_net', 'RDAY', 'C1', 'C1_net', 'C5', 'C20']
    side = d[~d['main'].astype(bool)]
    if len(side):
        print('\n=== 본표 제외(특례·조기편입·수시변경·패시브 매매 없음) — 참고')
        for (ix, typ, act), g in side.groupby(['index', 'type', 'action']):
            print(f'  {ix} {typ} {act}: ' + ' | '.join(f'{c} {summ(g, c)}' for c in ['B', 'C5', 'C20'] if summ(g, c)))
    d = d[d['main'].astype(bool)]
    for (ix, act), g in d.groupby(['index', 'action']):
        print(f'\n=== {ix} {act}  (평균 중앙값 플러스비율 회차t n종목/회차)')
        for per, gg in [('전체', g)] + list(g.groupby('period')):
            print(f'  [{per}]')
            for c in cols:
                s = summ(gg, c)
                if s: print(f'    {c:6s} {s}')
    # 같은 회차 편입 − 편출 (공통 규모 요인 상쇄)
    print('\n=== 회차별 (편입 평균 − 편출 평균) — 평균 중앙값 플러스비율 t n회차')
    for ix, g in d.groupby('index'):
        for c in ['A40', 'A2', 'FULL', 'B', 'C5', 'C20']:
            p = g.groupby(['review', 'action'])[c].mean().unstack()
            if not {'add', 'delete'} <= set(p.columns): continue
            sp = (p['add'] - p['delete']).dropna()
            for per, x in [('전체', sp), ('2022-26', sp[sp.index >= '2022'])]:
                if len(x) > 2:
                    print(f'  {ix} {c:4s} [{per}] {x.mean()*100:+6.2f} {x.median()*100:+6.2f} {(x>0).mean()*100:4.0f}% t{x.mean()/x.std(ddof=1)*np.sqrt(len(x)):+5.2f} n{len(x)}')
    # 회차 바구니(동일가중) 비용 차감 초과수익을 연도별로 합산 — 자금 전부를 그 구간에만 넣었다면 1년에 기준 대비 몇 %p 더 벌었나
    print('\n=== 연간 합산 (회차 바구니 비용 후 초과수익의 연 합계, %p) — 2014~2025 완전 연도')
    for ix, act, col in [('MSCI', 'add', 'A2R_net'), ('K200', 'add', 'A2R_net'), ('MSCI', 'add', 'B_net'),
                         ('MSCI', 'delete', 'C1_net'), ('K200', 'delete', 'C1_net')]:
        g = d[(d['index'] == ix) & (d.action == act)]
        y = g.groupby('review')[col].mean().groupby(lambda rv: int(rv[:4])).sum()
        y = y[(y.index >= 2014) & (y.index <= 2025)].reindex(range(2014, 2026), fill_value=0.0)
        print(f'  {ix} {act} {col:8s} 평균 {y.mean()*100:+5.2f}%p/년  2014-17 {y.loc[2014:2017].mean()*100:+5.2f}  2018-21 {y.loc[2018:2021].mean()*100:+5.2f}  2022-25 {y.loc[2022:2025].mean()*100:+5.2f}  플러스 연도 {(y>0).sum()}/{len(y)}')
    return d


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'k200')
