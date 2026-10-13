"""단타 종결 프로그램 C 판정 (PREREGISTRATION.md 고정 정의).

  .venv/bin/python docs/research/20261011_structural_c/run_c.py
"""
import os

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FUT = os.path.join(REPO, "DataVault", "normalized", "macro", "10100_1m.parquet")
ETF = os.path.join(REPO, "DataVault", "normalized", "candles_1m", "{}_1m.parquet")
RECENT_FROM = pd.Timestamp("2025-10-09")
FUT_FEE, ETF_FEE = 0.00006, 0.0003


def fut_cost(px):
    return 0.1 / px + FUT_FEE


def etf_cost(px):
    return 5.0 / px + ETF_FEE


def minute_grid(start, end):
    t = pd.timedelta_range(start, end, freq="1min")
    return [int(f"{x.components.hours:02d}{x.components.minutes:02d}") for x in t]


def load_futures():
    d = pd.read_parquet(FUT)
    ts = pd.to_datetime(d["timestamp"])
    d["day"], d["tm"] = ts.dt.normalize(), ts.dt.hour * 100 + ts.dt.minute
    piv = d.pivot_table(index="day", columns="tm", values="close", aggfunc="last").sort_index()
    piv = piv.reindex(columns=minute_grid("08:45:00", "16:45:00"))
    piv = piv.ffill(axis=1, limit=5)                       # 무체결 분은 직전 체결가(최대 5분)
    last_tm = d.groupby("day")["tm"].max()
    regular = last_tm == 1545
    bad = ~regular
    excl = bad | bad.shift(1, fill_value=False)            # 비정규일 + 다음 거래일
    prev_last = d.groupby("day")["close"].last().shift(1)
    return piv, prev_last, ~excl


def stats(ret: pd.Series, val_from=None, val_to=None):
    ret = ret.dropna()
    out = {"n": len(ret)}
    if len(ret) < 3:
        return out
    t = ret.mean() / ret.std(ddof=1) * np.sqrt(len(ret))
    yr = ret.groupby(ret.index.year).agg(["sum", "size"])
    yr = yr[yr["size"] >= 10]
    out.update(mean=ret.mean() * 100, t=t, win=(ret > 0).mean(), years=f"{int((yr['sum'] > 0).sum())}/{len(yr)}",
               yr_ok=bool(len(yr) and (yr["sum"] > 0).mean() >= 2 / 3), recent=ret[ret.index >= RECENT_FROM].sum() * 100)
    if val_from is not None:
        v = ret[(ret.index >= val_from) & (ret.index <= val_to)]
        vs = stats(v) if len(v) >= 3 else {"n": len(v)}
        out["val"] = vs
    ok = out["mean"] > 0 and t > 3 and out["yr_ok"] and out["recent"] > 0
    if "val" in out:
        vs = out["val"]
        ok = ok and vs.get("mean", -1) > 0 and vs.get("t", 0) > 3 and vs.get("yr_ok", False)
    out["PASS"] = bool(ok)
    return out


def fmt(name, s):
    if s.get("n", 0) < 3:
        return f"{name:42s} n={s.get('n', 0)} (표본 부족)"
    line = (f"{name:42s} n={s['n']:5d} 순 {s['mean']:+.3f}% t{s['t']:+.2f} 승률 {s['win']:.0%} 연도+ {s['years']} "
            f"최근12M 합 {s['recent']:+.2f}%")
    if "val" in s:
        v = s["val"]
        line += f" | 검증구간 n={v.get('n')} " + (f"{v['mean']:+.3f}% t{v['t']:+.2f} 연도+ {v['years']}" if "t" in v else "")
    return line + ("  ✅합격" if s.get("PASS") else "  ❌")


def c1(piv, prev, ok):
    print("\n== C1 레버리지 ETF 리밸런싱 (선물) ==")
    r = piv[1530] / prev - 1
    res = {}
    for ent in (1531, 1532, 1533, 1535):
        sel = ok & (r.abs() > 0.01) & piv[ent].notna() & piv[1545].notna()
        g = np.sign(r) * (piv[1545] / piv[ent] - 1) - fut_cost(piv[ent])
        s = stats(g[sel])
        res[ent] = s
        print(fmt(f"진입 {ent} (판정={'예' if ent == 1532 else '민감도'})", s))
    return res[1532]


def c2(sym, label):
    e = pd.read_parquet(ETF.format(sym))
    ts = pd.to_datetime(e["timestamp"])
    e["day"], e["tm"] = ts.dt.normalize(), ts.dt.hour * 100 + ts.dt.minute
    piv = e.pivot_table(index="day", columns="tm", values="close", aggfunc="last").ffill(axis=1, limit=5)
    first = e.groupby("day")["tm"].min()
    regular = (first == 901) & piv.get(1530).notna()
    prev = piv[1530].shift(1)
    res = None
    for ent in (1511, 1512, 1513):
        sel = regular & regular.shift(1, fill_value=False) & (piv[1510] < prev)
        g = piv[1530] / piv[ent] - 1 - etf_cost(piv[ent])
        s = stats(g[sel])
        print(fmt(f"{label} 진입 {ent} (판정={'예' if ent == 1511 else '민감도'})", s))
        res = s if ent == 1511 else res
    return res


def c3(piv, prev, ok):
    print("\n== C3 오버나잇 vs 보유 (선물 1배, 069500 근사) ==")
    nxt_open = piv[901].shift(-1)
    nxt_close = piv[1530].shift(-1)
    both = ok & ok.shift(-1, fill_value=False)
    on = (nxt_open / piv[1530] - 1 - ETF_FEE)[both].dropna()
    hold = (nxt_close / piv[1530] - 1)[both].dropna()

    def perf(x):
        ann = x.mean() * 250
        vol = x.std() * np.sqrt(250)
        eq = (1 + x).cumprod()
        mdd = (eq / eq.cummax() - 1).min()
        return ann, vol, ann / vol, mdd

    for name, x in (("오버나잇만(비용 후)", on), ("보유", hold)):
        a, v, sh, mdd = perf(x)
        print(f"{name:20s} 연 {a*100:+.1f}% 변동성 {v*100:.1f}% 샤프 {sh:.2f} 최대낙폭 {mdd*100:.1f}%")
    s = stats(on)
    s["PASS"] = s["PASS"] and perf(on)[2] > perf(hold)[2]
    print(fmt("오버나잇 일별(판정)", s))
    return s


def c4(piv, prev, ok):
    print("\n== C4 시장 투매 반등 (선물) ==")
    cols = minute_grid("09:30:00", "15:00:00")
    allc = list(piv.columns)
    out = {}
    for X in (0.010, 0.015):
        for H in (15, 30, 60):
            rets = {}
            for day in piv.index[ok.reindex(piv.index, fill_value=False).to_numpy()]:
                row = piv.loc[day]
                for tm in cols:
                    i = allc.index(tm)
                    if i < 15 or i + 1 + H >= len(allc):
                        continue
                    a, b = row.iloc[i - 15], row.iloc[i]
                    if np.isfinite(a) and np.isfinite(b) and b / a - 1 <= -X:
                        ent, ex = row.iloc[i + 1], row.iloc[min(i + 1 + H, allc.index(1545))]
                        if np.isfinite(ent) and np.isfinite(ex):
                            rets[day] = ex / ent - 1 - fut_cost(ent)
                        break
            s = stats(pd.Series(rets, dtype=float), pd.Timestamp("2016-06-01"), pd.Timestamp("2024-08-31"))
            out[(X, H)] = s
            print(fmt(f"X={X:.1%} +{H}분", s))
    return out


def c5(piv, prev, ok):
    print("\n== C5 장마감 직전 패턴 (선물) ==")
    vf, vt = pd.Timestamp("2016-06-01"), pd.Timestamp("2023-12-31")
    d1 = np.sign(piv[1500] / prev - 1)
    a = (d1 * (piv[1510] / piv[1500] - 1) - fut_cost(piv[1500]))[ok & (d1 != 0)]
    d2 = -np.sign(piv[1510] / prev - 1)
    b = (d2 * (piv[1520] / piv[1510] - 1) - fut_cost(piv[1510]))[ok & (d2 != 0)]
    sa, sb = stats(a, vf, vt), stats(b, vf, vt)
    print(fmt("(a) 15:00 추종 → 15:10", sa))
    print(fmt("(b) 15:10 반대 → 15:20", sb))
    return sa, sb


def main():
    piv, prev, ok = load_futures()
    print(f"선물 {piv.index.min():%Y-%m-%d}~{piv.index.max():%Y-%m-%d}, 사용 가능일 {int(ok.sum())}/{len(ok)}")
    verdict = {"C1": c1(piv, prev, ok)["PASS"]}
    print("\n== C2 장마감 반전 (ETF, 동시호가 청산) ==")
    verdict["C2a 122630"] = c2("122630", "122630")["PASS"]
    verdict["C2b 069500"] = c2("069500", "069500")["PASS"]
    verdict["C3"] = c3(piv, prev, ok)["PASS"]
    for k, s in c4(piv, prev, ok).items():
        verdict[f"C4 X{k[0]:.1%} +{k[1]}"] = s.get("PASS", False)
    sa, sb = c5(piv, prev, ok)
    verdict["C5a"], verdict["C5b"] = sa.get("PASS", False), sb.get("PASS", False)
    print("\n== 판정 ==")
    for k, v in verdict.items():
        print(f"  {k:20s} {'✅ 합격' if v else '❌ 불합격'}")


if __name__ == "__main__":
    main()
