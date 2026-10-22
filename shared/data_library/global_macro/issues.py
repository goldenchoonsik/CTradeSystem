"""이슈 원장 — 수집·파싱·빌드·화면에서 생긴 문제를 한 곳에 쌓아 나중에 한 번에 고친다(사람이 로그를 뒤지지 않도록).

  DataVault/staging/global_macro/issues/open.jsonl      지금 열린 이슈(원천별 현재 상태; 다시 쓰기)
  DataVault/staging/global_macro/issues/history.jsonl   열림·해결 사건 append-only(추이)
  DataVault/staging/global_macro/issues/DIGEST.md       한 번에 읽는 요약(원천 × 종류 건수, 예, 상세 파일 위치)
두 가지 쓰임:
  - 상태형(state): 빌드마다 그 원천의 현재 문제 집합을 통째로 `replace(source, items)` — 고쳐지면 다음 빌드에서 자동으로 빠지고 history 에 '해결'로 남는다.
  - 사건형(event): 단계 예외 등은 `add(...)` 로 upsert(같은 키면 last_seen·count 갱신), 다음 성공 때 `resolve(source, key)`.
이슈 레코드: source(원천/단계), kind(종류), key(원천 안 고유키), title(한 줄), detail(dict, 고칠 때 필요한 자료), first_seen, last_seen, count, hint(어디를 고치나).
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from . import store

DIR = ("issues",)
KINDS_NOT_FIXABLE = {"doc_missing", "no_source"}          # 원천이 없어 코드로 못 고치는 종류(건수만)


def _p(name: str) -> str:
    return store.path(*DIR, name)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load() -> List[dict]:
    fp = _p("open.jsonl")
    if not os.path.exists(fp):
        return []
    with open(fp, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _save(items: List[dict]):
    os.makedirs(_p(""), exist_ok=True)
    tmp = _p("open.jsonl") + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False, default=str) + "\n")
    os.replace(tmp, _p("open.jsonl"))


def _hist(event: str, it: dict):
    os.makedirs(_p(""), exist_ok=True)
    with open(_p("history.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": _now(), "event": event, "source": it.get("source"), "kind": it.get("kind"), "key": it.get("key"), "title": it.get("title")}, ensure_ascii=False) + "\n")


def _norm(source: str, kind: str, key: str, title: str, detail: Optional[dict], hint: Optional[str]) -> dict:
    return {"source": source, "kind": kind, "key": str(key), "title": str(title)[:200], "detail": detail or {}, "hint": hint or "",
            "first_seen": _now(), "last_seen": _now(), "count": 1}


def add(source: str, kind: str, key: str, title: str, detail: Optional[dict] = None, hint: Optional[str] = None) -> dict:
    """사건형 upsert — 같은 (source, key) 면 last_seen·count·detail 갱신(first_seen 유지)."""
    items = load()
    for it in items:
        if it["source"] == source and it["key"] == str(key):
            it.update(kind=kind, title=str(title)[:200], detail=detail or it.get("detail") or {}, hint=hint or it.get("hint", ""), last_seen=_now(), count=int(it.get("count", 1)) + 1)
            _save(items)
            return it
    it = _norm(source, kind, key, title, detail, hint)
    items.append(it)
    _save(items)
    _hist("open", it)
    return it


def resolve(source: str, key: Optional[str] = None) -> int:
    """source 의 이슈(키 지정 시 그것만)를 닫는다. 반환 = 닫은 수."""
    items = load()
    keep, closed = [], 0
    for it in items:
        if it["source"] == source and (key is None or it["key"] == str(key)):
            _hist("resolved", it)
            closed += 1
        else:
            keep.append(it)
    if closed:
        _save(keep)
    return closed


def replace(source: str, new_items: List[dict]) -> Dict[str, int]:
    """상태형 — source 의 현재 이슈 집합을 new_items(각각 kind·key·title·detail·hint) 로 바꾼다. 사라진 키는 '해결', 새 키는 '열림', 남은 키는 count 유지."""
    items = load()
    old = {it["key"]: it for it in items if it["source"] == source}
    others = [it for it in items if it["source"] != source]
    out, opened, resolved = [], 0, 0
    for n in new_items:
        k = str(n["key"])
        if k in old:
            it = old.pop(k)
            it.update(kind=n["kind"], title=str(n["title"])[:200], detail=n.get("detail") or {}, hint=n.get("hint", it.get("hint", "")), last_seen=_now())
        else:
            it = _norm(source, n["kind"], k, n["title"], n.get("detail"), n.get("hint"))
            _hist("open", it)
            opened += 1
        out.append(it)
    for it in old.values():
        _hist("resolved", it)
        resolved += 1
    _save(others + out)
    write_digest()
    return {"open": len(out), "opened": opened, "resolved": resolved}


def summary() -> dict:
    items = load()
    by = {}
    for it in items:
        by.setdefault(it["source"], {}).setdefault(it["kind"], 0)
        by[it["source"]][it["kind"]] += 1
    fixable = sum(1 for it in items if it["kind"] not in KINDS_NOT_FIXABLE)
    return {"total": len(items), "fixable": fixable, "by_source": by, "digest": _p("DIGEST.md"), "updated": _now()}


def write_digest(per_source: int = 10) -> str:
    """DIGEST.md — 원천 × 종류 건수 → 원천별 예(고칠 때 필요한 detail 포함) → 상세 파일 위치. 고치는 사람(=다음 대화의 나)을 위한 문서."""
    items = load()
    L = ["# 글로벌 탑다운 이슈 원장 — 다이제스트 (바뀔 때마다 다시 씀)", "",
         f"생성 {_now()} · 열린 이슈 {len(items)}건 (코드로 고칠 수 있는 것 {sum(1 for it in items if it['kind'] not in KINDS_NOT_FIXABLE)}건). 원본 `issues/open.jsonl`, 추이 `issues/history.jsonl`.",
         "고치는 법: 원천별 hint 가 가리키는 코드를 고친 뒤 그 단계를 다시 실행하면 상태형 이슈는 자동으로 닫히고, 사건형은 다음 성공 때 닫힌다.", ""]
    if not items:
        L.append("열린 이슈 없음.")
    else:
        srcs = sorted({it["source"] for it in items})
        kinds = sorted({it["kind"] for it in items})
        L += ["## 원천 × 종류", "", "| 원천 | " + " | ".join(kinds) + " | 합 |", "|---|" + "---|" * (len(kinds) + 1)]
        for s in srcs:
            g = [it for it in items if it["source"] == s]
            L.append(f"| {s} | " + " | ".join(str(sum(1 for it in g if it['kind'] == k)) for k in kinds) + f" | {len(g)} |")
        for s in srcs:
            g = sorted([it for it in items if it["source"] == s], key=lambda it: (it["kind"] in KINDS_NOT_FIXABLE, it["kind"], it["key"]))
            hint = next((it["hint"] for it in g if it.get("hint")), "")
            L += ["", f"## {s} ({len(g)}건)" + (f" — {hint}" if hint else ""), ""]
            for it in g[:per_source]:
                d = it.get("detail") or {}
                dd = json.dumps(d, ensure_ascii=False, default=str)
                L.append(f"- [{it['kind']}] {it['title']} · 처음 {it['first_seen'][:10]} · {it['count']}회" + (f"\n  `{dd[:400]}`" if d else ""))
            if len(g) > per_source:
                L.append(f"- … 외 {len(g) - per_source}건 (open.jsonl)")
    fp = _p("DIGEST.md")
    os.makedirs(_p(""), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return fp


def step_failed(step: str, e: BaseException, hint: Optional[str] = None):
    """단계 예외 공통 처리: 로그 + 사건형 이슈."""
    import traceback
    store.log(f"[{step}] 실패: {e}")
    add(step, "step_error", "step", f"{step} 단계 실패: {str(e)[:120]}", {"error": str(e)[:300], "trace": traceback.format_exc()[-800:]}, hint or f"{step} 단계 코드·원천 응답 확인")


def step_ok(step: str):
    resolve(step, "step")


def from_fail_list(source: str, fails: List[str], kind: str = "fetch_error", hint: Optional[str] = None):
    """수집기의 fail 목록(문자열 '키 메시지')을 상태형으로 — 비면 전부 해결."""
    items = []
    for f in fails:
        key = f.split(" ", 1)[0]
        items.append({"kind": kind, "key": key, "title": f[:200], "detail": {"message": f}, "hint": hint or f"{source} 수집기"})
    return replace(source, items)
