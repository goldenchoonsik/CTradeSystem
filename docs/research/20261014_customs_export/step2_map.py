"""2단계 — 회사↔(시군구×HS6) 칸 대응표를 개발 구간(2016~2023 수준, 2017Q1~2023Q4 전년비)에서만 찾고 고정.

  .venv/bin/python docs/research/20261014_customs_export/step2_map.py collect   # API 수집(이어받기, 한도 걸리면 멈춤)
  .venv/bin/python docs/research/20261014_customs_export/step2_map.py map       # 대응표·잡음 바닥 → mapping_dev.json, results_mapping.md

규칙(결과 보기 전 고정):
  후보 칸   : 회사 품목(HS6 합)의 2018·2022 전국 수출 중 점유 ≥3% 시군구, 최대 12개
  선택      : 개발 전년비 상관 최고 1칸에서 시작, 상관이 +0.05 이상 오르면 칸 추가(최대 3칸)
  통과      : 상관 ≥0.5 · 분기 ≥20 · 수출/매출 중앙 0.05~2.0
  잡음 바닥 : 같은 절차를 '다른 품목' 회사의 후보 칸으로 5회 → 통과율 = 우연 통과 비율
  2024~ 상관은 보고만(선택·통과에 쓰지 않음).
"""
import os, sys, json, random
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import customs_api as c
from candidates import CANDIDATES
from feasibility import SIDOS, fx, frame, sales_q

SCAN_YEARS = (2018, 2022)
YEARS = range(2016, 2027)
DEV_END = pd.Period("2023Q4")
MIN_SHARE, MAX_CELLS, MAX_PICK, STEP = 0.03, 12, 3, 0.05
GATE_CORR, GATE_N, GATE_RATIO = 0.5, 20, (0.05, 2.0)


def codes() -> dict:
    u = pd.read_csv(c.data_vault_path("staging", "kr_equity", "universe.csv"), dtype=str)
    u = u[u.is_preferred == "0"].set_index("name")
    return {n: u.loc[n, "code"][1:] for n in CANDIDATES}


def hs_groups() -> dict:
    """품목 묶음(튜플) → 회사 목록. 같은 묶음은 후보 칸을 공유."""
    g = {}
    for n, hs in CANDIDATES.items():
        g.setdefault(tuple(hs), []).append(n)
    return g


def scan(group) -> list:
    """2018·2022 전국에서 묶음 합 점유 ≥3% 시군구 [(sido, sggNm)]."""
    d = pd.concat([frame(h, s, y) for h in group for s in SIDOS for y in SCAN_YEARS])
    keep = set()
    for y in SCAN_YEARS:
        x = d[d.month.dt.year == y].groupby(["sido", "sggNm"]).expUsdAmt.sum()
        x = x / x.sum()
        keep |= set(x[x >= MIN_SHARE].index)
    tot = d.groupby(["sido", "sggNm"]).expUsdAmt.sum()
    return sorted(keep, key=lambda k: -tot.get(k, 0))[:MAX_CELLS]


def collect():
    for group in hs_groups():
        cells = scan(group)
        for h in group:
            for s in sorted({s for s, _ in cells}):
                for y in YEARS:
                    frame(h, s, y)
        print(group, len(cells), [g.split()[-1] for _, g in cells], flush=True)


def cell_quarters(group, cells, fxm) -> pd.DataFrame:
    """칸별 분기 수출(원화). 열 = sggNm."""
    parts = []
    for h in group:
        for s in sorted({s for s, _ in cells}):
            for y in YEARS:
                d = frame(h, s, y)
                if not d.empty:
                    parts.append(d[d.sggNm.isin([g for ss, g in cells if ss == s])])
    m = pd.concat(parts).groupby(["month", "sggNm"]).expUsdAmt.sum().unstack(fill_value=0)
    m = m.mul(fxm.reindex(m.index) * 1000, axis=0).dropna()
    q = m.groupby(m.index.asfreq("Q"))
    full = q.size() == 3
    return q.sum()[full]


def score(exp: pd.Series, sales: pd.Series, lo=None):
    """lo 없으면 개발 구간(~DEV_END), 있으면 lo 이후. 전년비 상관·분기 수·수출/매출 중앙."""
    j = pd.concat({"e": exp, "s": sales}, axis=1).dropna()
    j = j[j.s > 0]
    y = np.log(j.where(j > 0)).diff(4).replace([np.inf, -np.inf], np.nan)
    sel = (lambda ix: ix <= DEV_END) if lo is None else (lambda ix: ix >= lo)
    y = y[sel(y.index)].dropna()
    r = (j.e / j.s)[sel(j.index)]
    corr = y.e.corr(y.s) if len(y) > 6 else np.nan
    return corr, len(y), r.median() if len(r) else np.nan


def pick(cq: pd.DataFrame, sales: pd.Series):
    chosen, best = [], -np.inf
    while len(chosen) < MAX_PICK:
        trial = {}
        for col in cq.columns.difference(chosen):
            cr, n, _ = score(cq[chosen + [col]].sum(axis=1), sales)
            if n >= GATE_N and np.isfinite(cr):
                trial[col] = cr
        if not trial:
            break
        col, cr = max(trial.items(), key=lambda kv: kv[1])
        if chosen and cr < best + STEP:
            break
        chosen.append(col)
        best = cr
    if not chosen:
        return [], np.nan, 0, np.nan
    cr, n, ratio = score(cq[chosen].sum(axis=1), sales)
    return chosen, cr, n, ratio


def passes(cr, n, ratio):
    return bool(np.isfinite(cr) and cr >= GATE_CORR and n >= GATE_N and GATE_RATIO[0] <= ratio <= GATE_RATIO[1])


def run_map():
    fxm, code = fx(), codes()
    groups = hs_groups()
    cq_by = {g: cell_quarters(g, scan(g), fxm) for g in groups}
    sales = {n: sales_q(code[n]) for n in CANDIDATES}
    rows, mapping = [], {}
    for g, names in groups.items():
        for n in names:
            chosen, cr, k, ratio = pick(cq_by[g], sales[n])
            ok = passes(cr, k, ratio)
            post = score(cq_by[g][chosen].sum(axis=1), sales[n], lo=pd.Period("2024Q1")) if chosen else (np.nan, 0, np.nan)
            rows.append(dict(name=n, code=code[n], hs=",".join(g), cells=" + ".join(x.split()[-1] for x in chosen),
                             corr=cr, n=k, ratio=ratio, ok=ok, corr24=post[0], n24=post[1], ratio24=post[2]))
            mapping[n] = dict(code=code[n], hs=list(g), cells=chosen, corr_dev=cr, ratio_dev=ratio, ok=ok)
    # 잡음 바닥: 다른 품목 묶음의 칸으로 같은 절차
    rng = random.Random(20261014)
    null = []
    for _ in range(5):
        for g, names in groups.items():
            others = [h for h in groups if not set(h) & set(g)]
            for n in names:
                h = rng.choice(others)
                _, cr, k, ratio = pick(cq_by[h], sales[n])
                null.append(dict(corr=cr, ok=passes(cr, k, ratio)))
    r, nl = pd.DataFrame(rows).sort_values("corr", ascending=False), pd.DataFrame(null)
    json.dump(mapping, open(os.path.join(HERE, "mapping_dev.json"), "w", encoding="utf-8"), ensure_ascii=False,
              indent=1, default=float)
    L = ["# 2단계 — 회사↔칸 대응표 (개발 구간 2016~2023 에서만 선택)\n",
         f"대상 {len(r)}개, 통과 {int(r.ok.sum())}개 ({r.ok.mean():.0%}). "
         f"잡음 바닥(다른 품목 칸, {len(nl)}회): 통과 {nl.ok.mean():.0%}, 선택 상관 중앙 {nl['corr'].median():+.2f} "
         f"/ 90% {nl['corr'].quantile(.9):+.2f}. 실제 대상 선택 상관 중앙 {r['corr'].median():+.2f}.\n",
         "2024~ 열은 보고만(선택에 미사용).\n",
         "| 회사 | 코드 | HS | 고른 칸 | 개발 상관 | 분기 | 수출/매출 | 통과 | 2024~ 상관 | 분기 | 수출/매출 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for x in r.itertuples():
        L.append(f"| {x.name} | {x.code} | {x.hs} | {x.cells} | {x.corr:+.2f} | {x.n} | {x.ratio:.2f} | "
                 f"{'✅' if x.ok else ''} | {x.corr24:+.2f} | {x.n24} | {x.ratio24:.2f} |")
    open(os.path.join(HERE, "results_mapping.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L[:3]))


if __name__ == "__main__":
    {"collect": collect, "map": run_map}[sys.argv[1]]()
