"""판독 일지 — 관계를 '읽고' 내린 판단을 기록하고, 기간이 지나면 실제 결과로 채점한다(목표: 예측 확률을 판단의 적중률로 잰다).

규칙(사후확신 차단·정직성):
  · 예측은 한 번 쓰면 고칠 수 없다(append-only). 메모 추가·철회 표시만 가능, 철회도 기록에 남는다.
  · 시작 가격 = 기록한 날 '다음' 거래일 종가(기록 당일 종가는 이미 봤을 수 있으므로). 끝 = 시작일 + 기간(개월) 이후 첫 종가.
  · 비교 대상(벤치마크)이 있으면 결과 = 대상 수익 − 벤치마크 수익(같은 날짜).
  · 기본 확률 = 그 대상(또는 상대 수익)이 기록 이전 역사에서 같은 기간에 그 방향으로 간 비율 → '그냥 찍어도 이 정도'의 기준선.
  · 확신도(50~95%)로 브라이어 점수 = (확신도 − 맞음 여부)² — 기본 확률로 찍었을 때 점수와 비교.
저장: DataVault/staging/global_macro/journal/entries.jsonl (한 줄 = 한 사건: create · note · withdraw)
"""
import json
import os
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from shared.data_library.paths import data_vault_path

from . import catalog, kr_industry, store

_LOCK = threading.Lock()
HORIZONS = (3, 6, 12)
VIEWS = {"up": "상승", "down": "하락", "outperform": "기준보다 강함", "underperform": "기준보다 약함"}


# ---------------------------------------------------------------- 대상(가격 원천)
def subjects() -> List[dict]:
    """선택 가능한 대상 목록(한국 개별 종목은 'krstk:<6자리>' 로 직접 입력)."""
    out = [
        {"key": "idx:kospi", "name": "코스피", "group": "지수"}, {"key": "idx:k200", "name": "코스피200", "group": "지수"},
        {"key": "idx:spx", "name": "S&P500", "group": "지수"}, {"key": "idx:ndx", "name": "나스닥100", "group": "지수"},
        {"key": "idx:n225", "name": "닛케이225", "group": "지수"}, {"key": "idx:dax", "name": "독일 DAX", "group": "지수"},
        {"key": "idx:sse", "name": "상하이종합", "group": "지수"},
        {"key": "fx:usdkrw", "name": "원/달러", "group": "환율·원자재"}, {"key": "cmd:gold", "name": "금", "group": "환율·원자재"},
        {"key": "cmd:oil", "name": "WTI 유가", "group": "환율·원자재"}, {"key": "cmd:copper", "name": "구리", "group": "환율·원자재"},
    ]
    out += [{"key": f"krsec:{c}", "name": f"코스피 {v[0]}", "group": "한국 업종"} for c, v in kr_industry.SECTORS.items()]
    fp = store.path("derived", "us_industry", "ret_vw.parquet")
    if os.path.exists(fp):
        out += [{"key": f"usind:{c}", "name": f"미국 업종 {c}", "group": "미국 업종(Ken French, 월)"} for c in pd.read_parquet(fp).columns]
    return out


_GM = {"idx:kospi": "kr_kospi", "idx:k200": "kr_k200", "idx:spx": "us_spx", "idx:ndx": "us_ndx", "idx:n225": "jp_n225", "idx:dax": "eu_dax",
       "idx:sse": "cn_sse", "fx:usdkrw": "kr_usdkrw", "cmd:gold": "w_gold", "cmd:oil": "w_oil", "cmd:copper": "w_copper_f"}


def price(key: str) -> Optional[pd.Series]:
    """대상의 종가 시계열(날짜 오름차순). 없으면 None."""
    from . import panel as gp
    if key in _GM:
        return gp.load(_GM[key])
    kind, _, code = key.partition(":")
    if kind == "krsec":
        fp = data_vault_path("staging", "kr_equity", "raw", "creon", "index_1d", f"{code}_1d.csv")
        if not os.path.exists(fp):
            return None
        d = pd.read_csv(fp, dtype={"date": str})
        return pd.Series(d["close"].astype(float).values, index=pd.to_datetime(d["date"], format="%Y%m%d")).sort_index()
    if kind == "krstk":
        fp = data_vault_path("staging", "kr_equity", "raw", "creon", "candle_1d", f"A{code}_1d.csv")
        if not code.isdigit() or not os.path.exists(fp):
            return None
        d = pd.read_csv(fp, parse_dates=["timestamp"])
        return d.set_index("timestamp")["upbit_close"].astype(float).sort_index()            # Creon 수정주가
    if kind == "usind":
        fp = store.path("derived", "us_industry", "ret_vw.parquet")
        if not os.path.exists(fp):
            return None
        r = pd.read_parquet(fp)
        if code not in r:
            return None
        return (1 + r[code].fillna(0)).cumprod()                                              # 월말 지수
    return None


def name_of(key: str) -> str:
    for s in subjects():
        if s["key"] == key:
            return s["name"]
    if key.startswith("krstk:"):
        return f"종목 {key[6:]}"
    return key


# ---------------------------------------------------------------- 저장
def _path() -> str:
    return store.path("journal", "entries.jsonl")


def _append(ev: dict):
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    with _LOCK, open(_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _events() -> List[dict]:
    if not os.path.exists(_path()):
        return []
    with open(_path(), encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def create(subject: str, view: str, horizon: int, confidence: float, reasoning: str, benchmark: Optional[str] = None,
           tags: Optional[List[str]] = None, now: Optional[datetime] = None) -> dict:
    if view not in VIEWS:
        raise ValueError(f"방향은 {list(VIEWS)} 중 하나")
    if view in ("outperform", "underperform") and not benchmark:
        raise ValueError("'기준보다 강함/약함'은 비교 대상(벤치마크)이 필요")
    if int(horizon) not in HORIZONS:
        raise ValueError(f"기간은 {HORIZONS} 개월 중 하나")
    c = float(confidence)
    if not 0.5 <= c <= 0.95:
        raise ValueError("확신도는 50~95%")
    if not reasoning or len(reasoning.strip()) < 10:
        raise ValueError("근거를 10자 이상 적어 주세요(무엇을 읽었나)")
    for k in [subject] + ([benchmark] if benchmark else []):
        p = price(k)
        if p is None or p.dropna().empty:
            raise ValueError(f"가격 자료가 없는 대상: {k}")
    now = now or datetime.now()
    ev = {"type": "create", "id": now.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6], "at": now.isoformat(timespec="seconds"),
          "subject": subject, "subject_name": name_of(subject), "view": view, "benchmark": benchmark or None,
          "benchmark_name": name_of(benchmark) if benchmark else None, "horizon": int(horizon), "confidence": round(c, 3),
          "reasoning": reasoning.strip(), "tags": [t.strip() for t in (tags or []) if t and t.strip()]}
    _append(ev)
    return ev


def note(entry_id: str, text: str, now: Optional[datetime] = None) -> dict:
    if entry_id not in _entries_raw():
        raise ValueError("없는 기록")
    ev = {"type": "note", "id": entry_id, "at": (now or datetime.now()).isoformat(timespec="seconds"), "text": text.strip()}
    _append(ev)
    return ev


def withdraw(entry_id: str, reason: str, now: Optional[datetime] = None) -> dict:
    e = _entries_raw().get(entry_id)
    if not e:
        raise ValueError("없는 기록")
    if e.get("withdrawn"):
        raise ValueError("이미 철회됨")
    if score(e).get("state") == "채점":
        raise ValueError("만기가 지나 채점된 기록은 철회할 수 없음(결과를 숨기지 않기 위해) — 메모로 남기세요")
    ev = {"type": "withdraw", "id": entry_id, "at": (now or datetime.now()).isoformat(timespec="seconds"), "reason": reason.strip()}
    _append(ev)
    return ev


def _entries_raw() -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for ev in _events():
        if ev["type"] == "create":
            out[ev["id"]] = dict(ev, notes=[], withdrawn=None)
        elif ev["id"] in out:
            if ev["type"] == "note":
                out[ev["id"]]["notes"].append({"at": ev["at"], "text": ev["text"]})
            elif ev["type"] == "withdraw":
                out[ev["id"]]["withdrawn"] = {"at": ev["at"], "reason": ev["reason"]}
    return out


# ---------------------------------------------------------------- 채점
def _after(s: pd.Series, d: pd.Timestamp, strict: bool) -> Optional[tuple]:
    s = s.dropna()
    s = s[s.index > d] if strict else s[s.index >= d]
    return (s.index[0], float(s.iloc[0])) if len(s) else None


def _base_rate(s: pd.Series, b: Optional[pd.Series], until: pd.Timestamp, months: int, up: bool) -> Optional[float]:
    """기록 이전 역사(최근 20년)에서 같은 기간(월말 기준) 그 방향으로 간 비율."""
    m = s.dropna().resample("ME").last()
    x = np.log(m.shift(-months) / m)
    if b is not None:
        bm = b.dropna().resample("ME").last().reindex(m.index)
        x = x - np.log(bm.shift(-months) / bm)
    x = x[(x.index + pd.DateOffset(months=months) <= until) & (x.index >= until - pd.DateOffset(years=20))].dropna()
    if len(x) < 24:
        return None
    return float((x > 0).mean() if up else (x < 0).mean())


def score(e: dict, today: Optional[pd.Timestamp] = None) -> dict:
    today = today or pd.Timestamp(datetime.now().date())
    created = pd.Timestamp(e["at"]).normalize()
    s, b = price(e["subject"]), price(e["benchmark"]) if e.get("benchmark") else None
    out = {"state": "대기"}
    if s is None:
        return {"state": "자료 없음"}
    st = _after(s, created, strict=True)
    if not st:
        return out                                                   # 다음 거래일 종가가 아직 없음
    sd, sv = st
    bst = _after(b, created, strict=True) if b is not None else None
    if e.get("benchmark") and not bst:
        return out                                                   # 비교 대상 시작 가격이 아직 없음 → 대기
    target = sd + pd.DateOffset(months=e["horizon"])
    up_view = e["view"] in ("up", "outperform")
    out.update(start_date=str(sd.date()), start_value=sv, target_date=str(target.date()))
    # 현재까지(평가 중) 또는 만기
    en = _after(s, target, strict=False)
    last_d, last_v = s.dropna().index[-1], float(s.dropna().iloc[-1])
    done = en is not None and (b is None or _after(b, target, strict=False) is not None)   # 비교 대상도 만기 가격이 있어야 채점
    ed, ev = en if done else (last_d, last_v)
    r = ev / sv - 1
    rb = None
    if b is not None and bst:
        ben = _after(b, target, strict=False) if done else (b.dropna().index[-1], float(b.dropna().iloc[-1]))
        if ben:
            rb = ben[1] / bst[1] - 1
    rel = r - rb if rb is not None else None
    outcome = rel if e.get("benchmark") and rel is not None else r
    out.update(end_date=str(ed.date()), ret=r, ret_bench=rb, ret_rel=rel, outcome=outcome, days_left=max(0, (target - today).days))
    if done:
        hit = (outcome > 0) if up_view else (outcome < 0)
        base = _base_rate(s, b if e.get("benchmark") else None, created, e["horizon"], up_view)
        out.update(state="채점", hit=bool(hit), base_rate=base, brier=(e["confidence"] - float(hit)) ** 2,
                   brier_base=None if base is None else (base - float(hit)) ** 2)
    else:
        out["state"] = "진행 중"
        out["base_rate"] = _base_rate(s, b if e.get("benchmark") else None, created, e["horizon"], up_view)
    return out


def listing(today: Optional[pd.Timestamp] = None) -> dict:
    ents = sorted(_entries_raw().values(), key=lambda x: x["at"], reverse=True)
    rows = []
    for e in ents:
        sc = {"state": "철회"} if e.get("withdrawn") else score(e, today)
        rows.append(dict(e, score=sc))
    done = [r for r in rows if r["score"].get("state") == "채점"]
    summ = {"n_total": len(rows), "n_open": sum(r["score"].get("state") in ("진행 중", "대기") for r in rows),
            "n_scored": len(done), "n_withdrawn": sum(bool(r.get("withdrawn")) for r in rows)}
    if done:
        hits = [r["score"]["hit"] for r in done]
        bases = [r["score"]["base_rate"] for r in done if r["score"].get("base_rate") is not None]
        summ.update(hit_rate=float(np.mean(hits)), base_rate=float(np.mean(bases)) if bases else None,
                    brier=float(np.mean([r["score"]["brier"] for r in done])),
                    brier_base=float(np.mean([r["score"]["brier_base"] for r in done if r["score"].get("brier_base") is not None])) if bases else None,
                    avg_signed=float(np.mean([r["score"]["outcome"] * (1 if r["view"] in ("up", "outperform") else -1) for r in done])))
        cal = {}
        for r in done:
            c = r["confidence"]
            bk = "50~60%" if c < 0.6 else "60~70%" if c < 0.7 else "70~80%" if c < 0.8 else "80% 이상"
            cal.setdefault(bk, []).append(r["score"]["hit"])
        summ["calibration"] = {k: {"n": len(v), "hit": float(np.mean(v))} for k, v in sorted(cal.items())}
    return {"entries": rows, "summary": summ}
