#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Raw Store 원천 CSV(크레온·해외선물·가상자산·cTrader)를 날짜 단위로 전체 검증하고, 손상된 파일을 복구한다(복구는 크레온).

  python scripts/raw_store_integrity.py verify [--symbols 069500 005930 ...]
  python scripts/raw_store_integrity.py repair [--symbols ...] [--apply]      # --apply 없으면 계획만 출력
  python scripts/raw_store_integrity.py baseline [--symbols ...]              # 검증을 통과한 파일에 원장(<파일>.days.json) 기준선 기록
  python scripts/raw_store_integrity.py conflicts [--symbols ...]             # '확인 필요'(같은 시각에 다른 데이터) 목록
  python scripts/raw_store_integrity.py resolve --symbols 069500 --label 1M --day 2026-09-29 --use new|old
  (verify/conflicts/resolve 는 크레온뿐 아니라 DataVault/raw 의 모든 원천을 본다 — --symbols 는 경로 일부로 거른다)
크레온 1분봉도 일자별 저장소(candle_1m/A{종목}/YYYY-MM-DD.csv)로 옮겨진 종목은 같다 — verify 는 일자 저장소 검사(candle_day_store.verify)로 갈음하고,
baseline/repair/resolve 는 대상으로 삼지 않는다(통합 CSV 로 남은 종목만 종전대로). 1D 와 크레온 외 원천은 통합 CSV 그대로다.
틱은 일자별 저장소라 이 도구의 대상이 아니다 — 검증·봉인은 scripts/tick_day_store_tool.py. 틱의 '확인 필요'는 conflicts 로 보이기만 하고
resolve 하지 않는다(봉인된 날을 직접 고치게 되므로 — 판단이 서면 tick_day_store 경로로 처리).

검증: 날짜별 블록 연속성·시각 오름차순·완전 중복·상위 원천 대비 거래량 비율·원장 해시·확인 필요 항목. 하나라도 무효인 날이 있으면 그 파일은 무효.
     거래일 누락(첫날~마지막 날 사이 거래일인데 기록이 없는 날)은 따로 보고하고, 원장에 영구 결손으로 기록된 날은 그렇게 표시한다.
복구 순서: 일봉 → 1분봉.
소스: 격리 폴더/임시 폴더의 옛 백업에서, 기준 거래량에 더 가까운 날짜를 가져온다.
"""
import argparse
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "CAiModelBaker"))

from core import raw_day_store as rds                                   # noqa: E402
from core import candle_day_store as cds                                # noqa: E402
from shared.data_library import candle_store                            # noqa: E402
from shared.data_library.data_quality import DataQualityAuditor                       # noqa: E402
from shared.data_library.source_mapping_manager import SourceMappingManager           # noqa: E402
from shared.core.market_calendar import get_market_holidays, get_trading_days  # noqa: E402

VAULT = os.path.join(ROOT, "DataVault")
BACKUP_DIR = os.path.join(VAULT, "quarantine", "repair_backups")
KIND = {"1M": "candle", "1D": "candle"}


def creon_symbols(only):
    syms = [r["symbol"] for r in SourceMappingManager.get_feed_registry_status() if (r.get("primary_source") or "").upper() == "CREON"]
    return [s for s in syms if not only or s in only]


def summarize(v):
    s = v["summary"]
    miss = v.get("missing_days") or {}
    lost = sum(1 for r in miss.values() if r.startswith("영구"))
    extra = []
    if miss:
        extra.append(f"거래일 누락 {len(miss)}일" + (f"(영구 결손 {lost})" if lost else ""))
    if s.get("pending_conflicts"):
        extra.append(f"확인 필요 {s['pending_conflicts']}건")
    return (f"{'유효' if v['valid'] else '무효'} · 날짜 {s['days']}일 중 유효 {s['valid_days']} / 무효 {len(s['invalid_days'])}"
            + (" · " + " · ".join(extra) if extra else ""))


_CAL = None


def krx_trading_days():
    """캘린더가 휴장일을 아는 기간(가장 이른 휴장일 이후 ~ 오늘)의 KRX 거래일. 그 이전은 휴장일을 몰라 누락 검사를 하지 않는다."""
    global _CAL
    if _CAL is None:
        import datetime as _dt
        hol = sorted(get_market_holidays("KRX"))
        _CAL = get_trading_days(hol[0], _dt.date.today().isoformat(), "KRX") if hol else []
    return _CAL


def other_raw_files(only=None):
    """크레온 외 원천(해외선물·미국주식·가상자산·cTrader) — (표시 이름, 경로, 봉 간격 초, 누락일 검사용 달력 또는 None).
    달력: 가상자산은 연중무휴라 매일, 해외선물·CFD 는 한국시간 날짜와 거래 세션이 어긋나 날짜 단위 누락 판정을 하지 않는다."""
    import datetime as _dt
    import re as _re
    from shared.data_library.raw_ingest import interval_seconds
    out = []
    for f in sorted(glob.glob(os.path.join(VAULT, "raw", "**", "*.csv"), recursive=True)):
        rel = os.path.relpath(f, os.path.join(VAULT, "raw"))
        if rel.startswith("creon" + os.sep) or (only and not any(o.upper() in os.path.basename(f).upper() for o in only)):
            continue
        m = _re.search(r"_([0-9]+[a-zA-Z]+)\.csv$", f)
        sec = interval_seconds(m.group(1)) if m else None
        cal = None
        if rel.startswith("crypto" + os.sep):
            first = rds.load_ledger(f) or {}
            days = sorted(first.get("days", {}))
            if days:
                d0 = _dt.date.fromisoformat(days[0]); n = (_dt.date.today() - d0).days
                cal = [(d0 + _dt.timedelta(days=i)).isoformat() for i in range(n + 1)]
        out.append((rel, f, sec, cal))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["verify", "repair", "baseline", "conflicts", "resolve"])
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--label", choices=["1D", "1M", "TICK"])            # TICK 은 conflicts 조회용
    ap.add_argument("--day")
    ap.add_argument("--use", choices=["new", "old"])
    a = ap.parse_args()
    order = ["1D", "1M"]                                    # 틱(일자 저장소)은 scripts/tick_day_store_tool.py
    if a.cmd in ("conflicts", "resolve"):
        return conflicts_cmd(a)
    if a.cmd == "verify":
        for rel, f, sec, cal in other_raw_files(a.symbols):
            v = rds.verify_file(f, check_ledger=True, trading_days=cal, interval_seconds=sec)
            print(f"[{rel:40s}] {summarize(v)}")
            for d in v["summary"]["invalid_days"][:2]:
                print(f"      {d}: {'; '.join(v['days'][d]['issues'])}")
            miss = v.get("missing_days") or {}
            if miss:
                ds = sorted(miss)
                print(f"      누락: {', '.join(f'{d}({miss[d][:5]})' for d in ds[:6])}{' …' if len(ds) > 6 else ''}")
    for sym in creon_symbols(a.symbols):
        paths = DataQualityAuditor.granularity_paths(sym)
        for label in order:
            p = paths.get(label)
            if not p:
                continue
            reference = None
            csym = candle_store.symbol_from_path(p) if label == "1M" else None
            if csym and candle_store.is_day_layout(csym):       # 1분봉 일자 저장소 — 통합 CSV 가 없다(있어도 일자 저장소가 우선)
                if a.cmd == "verify":
                    issues = cds.verify(candle_store.candle_root(), csym)
                    print(f"[{sym:7s} {label:4s}] 일자 저장소 · 날짜 {len(candle_store.days(csym))}일 중 문제 {len(issues)}일"
                          f" · 확인 필요 {len(candle_store.pending_conflicts(csym))}건")
                    for d in sorted(issues)[:3]:
                        print(f"      {d}: {'; '.join(issues[d])}")
                else:
                    print(f"[{sym:7s} {label:4s}] 일자 저장소 — {a.cmd} 는 대상이 아닙니다(검증은 verify)")
                continue
            if a.cmd == "verify":
                v = rds.verify_file(p, reference=reference, check_ledger=True, trading_days=krx_trading_days())
                print(f"[{sym:7s} {label:4s}] {summarize(v)}")
                for d in v["summary"]["invalid_days"][:3]:
                    print(f"      {d}: {'; '.join(v['days'][d]['issues'])}")
                miss = v.get("missing_days") or {}
                if miss:
                    ds = sorted(miss)
                    print(f"      거래일 누락: {', '.join(f'{d}({miss[d][:5]})' for d in ds[:6])}{' …' if len(ds) > 6 else ''}")
            elif a.cmd == "baseline":
                v = rds.verify_file(p, reference=reference, check_ledger=False)
                if v["valid"]:
                    rds.rebuild_ledger(p, note="baseline"); print(f"[{sym:7s} {label:4s}] 원장 기준선 기록 ({v['summary']['days']}일)")
                else:
                    print(f"[{sym:7s} {label:4s}] 무효 — 기준선을 만들지 않음 ({summarize(v)})")
            else:
                rep = rds.repair_file(p, kind=KIND[label], reference=reference, sources=[],
                                      backup_dir=BACKUP_DIR, apply=a.apply)
                ch = rep["changed_days"]
                acts = {}
                for d in ch:
                    acts.setdefault(rep["plan"][d]["action"], []).append(d)
                print(f"[{sym:7s} {label:4s}] 변경 대상 {len(ch)}일 · 복구 후 {'유효' if rep['valid_after'] else '무효(' + str(len(rep['verify_after']['invalid_days'])) + '일)'}"
                      f"{' · 적용됨' if rep.get('applied') else ''}")
                for act, ds in acts.items():
                    print(f"      - {act}: {len(ds)}일 ({ds[0]} ~ {ds[-1]})")
                left = rep["verify_after"]["invalid_days"]
                if left:
                    print(f"      ! 복구 후에도 무효: {left[:6]}{' …' if len(left) > 6 else ''}")


def conflicts_cmd(a):
    """'확인 필요' 항목 조회/해결 (모든 원천) — 같은 시각에 기존과 다른 데이터가 들어와 기존 값을 유지하고 보류해 둔 곳."""
    total = 0
    files = sorted(glob.glob(os.path.join(VAULT, "raw", "**", "*.csv"), recursive=True))
    for p in files:
        name = os.path.relpath(p, os.path.join(VAULT, "raw"))
        is_tick = os.sep + os.path.join("creon", "tick") + os.sep in p
        is_candle_day = (os.sep + os.path.join("creon", "candle_1m") + os.sep in p
                         and os.path.abspath(os.path.dirname(p)) != os.path.abspath(candle_store.candle_root()))   # candle_1m/A{종목}/날짜.csv
        if a.symbols and not any(s_.upper() in name.upper() for s_ in a.symbols):
            continue
        if a.label:
            by_name = not is_tick and not is_candle_day and f"_{a.label.lower()}." in os.path.basename(p).lower()
            if not ({"TICK": is_tick, "1M": is_candle_day or by_name}.get(a.label, by_name)):
                continue
        pend = rds.pending_conflicts(p)
        if not pend:
            continue
        if a.cmd == "resolve" and (is_tick or is_candle_day):
            print(f"[{name}] {'틱' if is_tick else '1분봉'} 일자 파일의 확인 필요 {len(pend)}건 — 이 도구로 해결하지 않습니다(봉인본 보호)")
            continue
        if a.cmd == "resolve":
            if not a.use:
                print("--use new|old 가 필요합니다"); return
            rep = rds.resolve_conflicts(p, a.day, use=a.use, backup_dir=BACKUP_DIR)
            print(f"[{name}] {rep['resolved']}건 처리({a.use}) · 날짜 {rep.get('days')}")
            continue
        total += len(pend)
        print(f"[{name}] 확인 필요 {len(pend)}건")
        for c in pend[:10]:
            print(f"    {c['ts']}  (감지 {c['detected_at']})")
            print(f"      기존 {len(c['old'])}행: {c['old'][0][:110]}")
            print(f"      신규 {len(c['new'])}행: {c['new'][0][:110]}")
    if a.cmd == "conflicts":
        print(f"확인 필요 합계 {total}건" if total else "확인 필요 항목 없음")

if __name__ == "__main__":
    main()
