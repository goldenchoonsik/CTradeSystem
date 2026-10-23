"""나스닥100 당시 구성 종목 복원 (2026-10-21)

현재 명단(api.nasdaq.com list-type/nasdaq100, 2026-10-21 조회) 에서 위키피디아 'Historical components of the Nasdaq-100'
변경표(2007-02 ~ 2026-10)를 거꾸로 적용해 날짜별 명단을 만든다. 원본은 data_ndx_wiki/ (무수정).
members_at(date) → 그날 장 마감 후 기준 명단(티커는 당시 표기).
"""
import os, re, json
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'data_ndx_wiki')


def _cells(row):
    out = []
    for ln in row.split('\n'):
        ln = ln.strip()
        if not ln.startswith('|') or ln.startswith('|-') or ln.startswith('|}') or ln.startswith('|+'):
            continue
        for c in ln[1:].split('||'):
            c = re.sub(r'^[^|\[]*?\|(?!\|)', '', c) if re.match(r'^\s*(rowspan|colspan|style|data-sort)', c) else c
            out.append(c.strip())
    return out


def changes():
    txt = open(os.path.join(SRC, 'historical_components.wiki'), encoding='utf-8').read()
    tab = txt[txt.index('id="changes"'):]
    tab = tab[:tab.index('\n|}')]
    rows = tab.split('\n|-')[1:]
    ev = []
    for r in rows:
        c = _cells(r)
        if len(c) < 5:
            continue
        try:
            d = pd.Timestamp(re.sub(r'<.*', '', c[0]).strip())
        except Exception:
            continue
        clean = lambda s: re.sub(r'<ref.*', '', re.sub(r'\[\[|\]\]|\{\{.*?\}\}', '', s)).strip()
        ev.append(dict(date=d, add=clean(c[1]), add_name=clean(c[2]), rem=clean(c[3]), rem_name=clean(c[4]),
                       reason=clean(c[5]) if len(c) > 5 else ''))
    return pd.DataFrame(ev).sort_values('date').reset_index(drop=True)


def current():
    d = json.load(open(os.path.join(SRC, 'nasdaq_api_ndx.json')))
    return {r['symbol'] for r in d['data']['data']['rows']}


_EV = changes()
_CUR = current()


def members_at(date):
    """date 장 마감 후 명단. 변경 효력일(date 열)의 개장 전에 바뀐 것으로 본다."""
    date = pd.Timestamp(date)
    s = set(_CUR)
    for _, e in _EV[_EV.date > date].sort_values('date', ascending=False).iterrows():
        if e['add']:
            s.discard(e['add'])
        if e['rem']:
            s.add(e['rem'])
    return s


if __name__ == '__main__':
    print(f"변경 {len(_EV)}건 ({_EV.date.min().date()} ~ {_EV.date.max().date()}), 현재 {len(_CUR)}")
    for y in range(2007, 2027):
        d = pd.Timestamp(f'{y}-06-30')
        print(y, len(members_at(d)))
    bad = _EV[(_EV['add'] == '') & (_EV['rem'] == '')]
    print("빈 행:", len(bad))
    print(_EV.head(3).to_string()); print(_EV.tail(3).to_string())
