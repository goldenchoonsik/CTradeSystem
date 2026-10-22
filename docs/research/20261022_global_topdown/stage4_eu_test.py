"""4단계 보충 — 유럽 시장 경쟁 E1(점유 지속)·E2(EU 업종 성분)·E3(미국+EU 합성). PREREGISTRATION_stage4_eu.md 그대로.

  .venv/bin/python -I docs/research/20261022_global_topdown/stage4_eu_test.py
출력: results_stage4_eu.md·json, DataVault/staging/global_macro/signals/registry_stage4b.json(신호 상태 탭)
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from shared.data_library.global_macro import reading as R, reading_eval as E, store, us_imports, eu_imports  # noqa: E402

START, SPLIT = pd.Timestamp("2012-03-31"), E.SPLIT
me = E.me


def load12(name: str) -> pd.DataFrame:
    df = pd.read_parquet(store.path("derived", "trade", f"{name}.parquet"))
    piv = df.pivot_table(index="month", columns=["cmd", "partner"], values="value", aggfunc="sum").sort_index()
    return piv.rolling(12, min_periods=12).sum()


def competition(m12: pd.DataFrame, t, kr: str, cn: str) -> dict:
    """reading.competition 과 같은 식(시장·국가 코드만 매개변수). us_imports_12m 키 이름은 R.sectors 가중치 호환용."""
    L2 = me(t, -2)
    base = me(L2 - pd.DateOffset(months=12))
    out = {}
    for cmd in sorted({c for c, _ in m12.columns}):
        if (cmd, "-") not in m12:
            continue
        tot = m12[(cmd, "-")]
        rec = {}
        for code, nm in ((kr, "kr"), (cn, "cn")):
            if (cmd, code) not in m12:
                continue
            sh = m12[(cmd, code)] / tot * 100
            a, b = sh.get(L2), sh.get(base)
            if a is None or b is None or pd.isna(a) or pd.isna(b):
                continue
            dlt = float(a - b)
            rec[nm] = {"share": float(a), "delta": dlt, "state": "확대" if dlt >= R.SHARE_BAND else "잠식" if dlt <= -R.SHARE_BAND else "유지"}
        if rec:
            rec["us_imports_12m"] = float(tot.get(L2, np.nan))
            rec["month"] = str(L2.date())
            out[cmd] = rec
    return out


def persistence(m12: pd.DataFrame, months, kr: str, cn: str) -> pd.DataFrame:
    rows = []
    for t in months:
        comp = competition(m12, t, kr, cn)
        L2, T = me(t, -2), me(t, 10)
        for cmd, c in comp.items():
            for nm, code in (("kr", kr), ("cn", cn)):
                if nm not in c or c[nm]["state"] == "유지":
                    continue
                sh = m12[(cmd, code)] / m12[(cmd, "-")] * 100
                a, b = sh.get(T), sh.get(L2)
                if a is None or pd.isna(a):
                    continue
                rows.append({"t": t, "cmd": cmd, "who": nm, "state": c[nm]["state"], "hit": bool(np.sign(a - b) == np.sign(c[nm]["delta"]))})
    return pd.DataFrame(rows)


def zc(vals: dict):
    s = pd.Series({k: v for k, v in vals.items() if v is not None}, dtype=float)
    if len(s) < 3 or s.std() == 0:
        return None
    return (s - s.mean()) / s.std()


def sector_tracks(D, hist, eu12, us12):
    """월별 C^US·C^EU → E2(EU 단독)·E3(z 합성)·참고 C^US 단독 점수의 hist-like 목록."""
    cache = {}
    tracks = {"E2": [], "E3": [], "US": []}
    latest = None
    for r in hist:
        t = pd.Timestamp(r["t"])
        if t < START or not r.get("sectors"):
            continue
        comp_eu = competition(eu12, t, "KR", "CN")
        sec_eu = R.sectors(D, t, r["demand"], comp_eu, cache)
        c_us = {s: v["C"] for s, v in r["sectors"].items()}
        c_eu = {s: sec_eu[s]["C"] for s in r["sectors"] if s in sec_eu}
        z_us, z_eu = zc(c_us), zc(c_eu)
        def mk(z):
            return {s: {"name": r["sectors"][s]["name"], "score": float(z[s]), "score_D": float(z[s]), "D": 0.0, "C": float(z[s])} for s in z.index}
        if z_eu is not None:
            tracks["E2"].append({"t": r["t"], "sectors": mk(z_eu)})
            latest = {"t": r["t"], "c_eu": c_eu, "c_us": c_us, "z_eu": z_eu.to_dict()}
        if z_us is not None:
            tracks["US"].append({"t": r["t"], "sectors": mk(z_us)})
        if z_us is not None and z_eu is not None:
            both = (z_us.reindex(z_eu.index) + z_eu).dropna()
            if len(both) >= 3:
                tracks["E3"].append({"t": r["t"], "sectors": mk(both)})
    return tracks, latest


def ic_summary(D, track):
    res, ic = E.score_sectors(D, track)
    out = {}
    for per, m_ic, m_s in (("all", ic.index == ic.index, res.t == res.t), ("early", ic.index <= SPLIT, res.t <= SPLIT), ("late", ic.index > SPLIT, res.t > SPLIT)):
        icm, t, n = E.mean_nw(ic["composite"][m_ic], R.H)
        ss = res[m_s]
        sp = E._spread(ss, "score")
        out[per] = {"ic": None if pd.isna(icm) else float(icm), "t": None if pd.isna(t) else float(t), "n_months": int(n),
                    "hit": float(ss.hit.dropna().mean()) if len(ss) and ss.hit.notna().any() else None,
                    "spread": float(sp.mean()) if len(sp) else None}
    return out


def verdict_r5(s: dict) -> str:
    pos = all((s[p]["ic"] or 0) > 0 for p in ("early", "late"))
    return "실전 후보" if pos and (s["all"]["ic"] or 0) > 0 and (s["all"]["t"] or 0) >= 2 else "작게 실험" if pos else "버림"


def share_map(m12: pd.DataFrame, partners: dict) -> dict:
    out = {}
    for cmd in sorted({c for c, _ in m12.columns}):
        if (cmd, "-") not in m12:
            continue
        sh = m12.xs(cmd, axis=1, level=0).div(m12[(cmd, "-")], axis=0) * 100
        sh = sh.dropna(how="all")
        if not len(sh):
            continue
        last = sh.iloc[-1]
        y17 = sh.loc[:"2017-12-31"]
        rec = {"month": str(sh.index[-1].date())}
        for p, nm in partners.items():
            if p == "-" or p not in sh:
                continue
            rec[nm] = {"now": round(float(last[p]), 1) if pd.notna(last[p]) else None,
                       "vs2017": round(float(last[p] - y17[p].iloc[-1]), 1) if len(y17) and pd.notna(last[p]) and pd.notna(y17[p].iloc[-1]) else None}
        out[cmd] = rec
    return out


def main():
    D = R.Data()
    eu12, us12 = load12("eu_imports"), load12("us_imports")
    hist = E.run_engine(D)
    months = [pd.Timestamp(r["t"]) for r in hist if pd.Timestamp(r["t"]) >= START]
    # E1 지속
    pe = persistence(eu12, months, "KR", "CN")
    pu = persistence(us12, months, "5800", "5700")
    def pers(df):
        o = {}
        for per, dd in (("all", df), ("early", df[df.t <= SPLIT]), ("late", df[df.t > SPLIT])):
            o[per] = {"n": int(len(dd)), "hit": float(dd.hit.mean()) if len(dd) else None}
        o["by"] = {f"{w}·{st}": {"n": int(((df.who == w) & (df.state == st)).sum()), "hit": float(df[(df.who == w) & (df.state == st)].hit.mean())}
                   for w in ("kr", "cn") for st in ("확대", "잠식") if ((df.who == w) & (df.state == st)).any()}
        kr = df[df.who == "kr"]
        o["kr"] = {per: {"n": int(len(dd)), "hit": float(dd.hit.mean()) if len(dd) else None} for per, dd in (("all", kr), ("early", kr[kr.t <= SPLIT]), ("late", kr[kr.t > SPLIT]))}
        return o
    E1 = {"eu": pers(pe), "us_same_window": pers(pu)}
    E1["verdict"] = "유효" if all((E1["eu"]["kr"][p]["hit"] or 0) >= 0.6 for p in ("early", "late")) else "무효"
    # E2·E3
    tracks, latest = sector_tracks(D, hist, eu12, us12)
    S = {k: ic_summary(D, v) for k, v in tracks.items() if v}
    V = {"E1": E1["verdict"], "E2": verdict_r5(S["E2"]) if "E2" in S else "판정 불가", "E3": verdict_r5(S["E3"]) if "E3" in S else "판정 불가"}
    maps = {"eu": share_map(eu12, eu_imports.PARTNERS), "us": share_map(us12, us_imports.PARTNERS)}
    res = {"generated_at": datetime.now().isoformat(timespec="seconds"), "prereg_sha256": open(os.path.join(HERE, "PREREGISTRATION_stage4_eu.sha256")).read().split()[0],
           "verdicts": V, "E1": E1, "sectors": S, "latest": latest, "maps": maps, "start": str(START.date()), "n_months": len(months)}
    json.dump(res, open(os.path.join(HERE, "results_stage4_eu.json"), "w"), ensure_ascii=False, indent=1, default=str)
    # 신호 원장(화면 신호 상태 탭)
    def sig(id_, name, s, verdict):
        cur = ""
        if latest:
            z = pd.Series(latest["z_eu"]).sort_values(ascending=False)
            nm = {k: v["name"] for k, v in hist[-1]["sectors"].items()}
            cur = "EU 경쟁 우호 " + "·".join(nm.get(k, k) for k in z.index[:2]) + " / 비우호 " + "·".join(nm.get(k, k) for k in z.index[-2:])
        f = lambda p: {"effect": s[p]["ic"], "t": s[p]["t"], "spread": s[p]["spread"], "hit": s[p]["hit"]}
        return {"id": id_, "family": "유럽 시장 경쟁 → 업종 순위(가격)", "name": name, "verdict": verdict, "horizon": "6개월", "periods": ["~2016", "2017~"],
                "dev": f("early"), "conf": f("late"), "current": {"date": latest["t"] if latest else None, "text": cur}}
    reg = {"stage": "4b", "generated_at": res["generated_at"], "prereg_sha256": res["prereg_sha256"], "source": "docs/research/20261022_global_topdown/stage4_eu_test.py",
           "periods": ["~2016", "2017~"], "signals": [sig("E2", "EU 시장 한국 점유 변화(업종 성분) 단독", S["E2"], V["E2"]), sig("E3", "미국+EU 점유 변화 z 합성 단독", S["E3"], V["E3"])] if "E2" in S and "E3" in S else []}
    os.makedirs(store.path("signals"), exist_ok=True)
    json.dump(reg, open(store.path("signals", "registry_stage4b.json"), "w"), ensure_ascii=False, indent=1, default=str)
    report(res)


def report(res):
    pc = lambda x: "–" if x is None else f"{x * 100:.0f}%"
    f3 = lambda x: "–" if x is None else f"{x:+.3f}"
    f1 = lambda x: "–" if x is None else f"{x:+.1f}"
    S, E1, V = res["sectors"], res["E1"], res["verdicts"]
    L = ["# 4단계 보충 — 유럽 시장 경쟁 (Eurostat Comext)", "", f"사전등록 sha256 {res['prereg_sha256'][:12]}…, 2012-03~ 매달 그 시점 자료(t−2)로 재현, {res['n_months']}개월. 계산 {res['generated_at']}", "",
         "## 판정", "", f"- **E1 EU 점유 지속(한국산)**: {V['E1']}", f"- **E2 EU 업종 성분 단독**: {V['E2']}", f"- **E3 미국+EU 합성 단독**: {V['E3']}", "",
         "## E1 점유 변화의 12개월 지속률 (확대·잠식만 채점)", "", "| 시장 | 대상 | 전체 | ~2016 | 2017~ |", "|---|---|---|---|---|"]
    for mk, nm in (("eu", "EU"), ("us_same_window", "미국(같은 창)")):
        p = E1[mk]
        L.append(f"| {nm} | 한국산 | {pc(p['kr']['all']['hit'])} (n={p['kr']['all']['n']}) | {pc(p['kr']['early']['hit'])} | {pc(p['kr']['late']['hit'])} |")
        L.append(f"| {nm} | 한국+중국 풀링 | {pc(p['all']['hit'])} (n={p['all']['n']}) | {pc(p['early']['hit'])} | {pc(p['late']['hit'])} |")
        L.append(f"| {nm} | 상태별 | " + " · ".join(f"{k.replace('kr', '한국').replace('cn', '중국')} {pc(v['hit'])}(n={v['n']})" for k, v in p["by"].items()) + " | | |")
    L += ["", "## E2·E3 업종 성분 → 업종 6개월 초과수익(코스피 대비)", "", "| 점수 | 구간 | IC | NW t | 개월 | 우호/비우호 적중 | 상위2−하위2 |", "|---|---|---|---|---|---|---|"]
    for k, nm in (("E2", "EU 점유 성분 단독"), ("E3", "미국+EU z 합성"), ("US", "미국 점유 성분 단독(참고, 같은 창)")):
        if k not in S:
            continue
        for per, pn in (("all", "전체"), ("early", "~2016"), ("late", "2017~")):
            v = S[k][per]
            L.append(f"| {nm} | {pn} | {f3(v['ic'])} | {f1(v['t'])} | {v['n_months']} | {pc(v['hit'])} | {'–' if v['spread'] is None else f'{v['spread'] * 100:+.1f}%p'} |")
    L += ["", "## 점유 지도 — 2017 대비 변화(12개월 합, %p) — 판정 없음, 현재 파악용", ""]
    for mk, nm, partners in (("eu", "EU 역외 수입", eu_imports.PARTNERS), ("us", "미국 수입", us_imports.PARTNERS)):
        names = [n for p, n in partners.items() if p != "-"]
        L += [f"### {nm}", "", "| 품목 | 기준월 | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
        for cmd, rec in res["maps"][mk].items():
            cells = [f"{rec[n]['now']}% ({f1(rec[n]['vs2017'])})" if n in rec and rec[n]["now"] is not None else "–" for n in names]
            L.append(f"| {us_imports.PRODUCTS.get(cmd, cmd)} | {rec['month'][:7]} | " + " | ".join(cells) + " |")
        L.append("")
    lt = res.get("latest")
    if lt:
        L += [f"## 최신 EU 경쟁 성분 ({lt['t']})", ""] + [f"- {k}: C^EU {('–' if v is None else f'{v:+.2f}%p')} · C^US {('–' if lt['c_us'].get(k) is None else f'{lt['c_us'][k]:+.2f}%p')}" for k, v in lt["c_eu"].items()]
    open(os.path.join(HERE, "results_stage4_eu.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
