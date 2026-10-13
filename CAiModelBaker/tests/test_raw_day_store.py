# -*- coding: utf-8 -*-
"""원천 CSV 날짜 단위 무결성 엔진 — 2026-09-24 사고 시나리오를 그대로 재현하고, 엔진이 그것을 막는지/고치는지 검증한다."""
import csv
import os

import pytest

from CAiModelBaker.core import raw_day_store as rds

H = ["timestamp", "upbit_open", "upbit_high", "upbit_low", "upbit_close", "upbit_volume", "upbit_trade_price", "cvd"]
HL = ",".join(H)


def tick(day, minute, i, vol=1.0, hh=9):
    ts = f"{day} {hh:02d}:{minute:02d}:00"
    px = 100000.0 + i
    return [ts, px, px, px, px, vol, px * vol * 10, float(1000 + i)]


def minute_rows(day, m, n, vol=1.0, hh=9):
    return [tick(day, m, i, vol, hh) for i in range(n)]


def received(day, minutes, n, vol=1.0):
    """크레온은 최신→과거 순으로 돌려준다 (같은 분 안도 역순)."""
    return [r for m in sorted(minutes, reverse=True) for r in reversed(minute_rows(day, m, n, vol))]


def write_csv(path, rows, header=H):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def read_rows(path):
    with open(path, newline="") as f:
        r = list(csv.reader(f))
    return r[0], r[1:]


def merge(tmp_path, target, rows, **kw):
    tmp = os.path.join(str(tmp_path), "new.tmp")
    write_csv(tmp, rows)
    return rds.merge_file(tmp, str(target), log=lambda m: None, **kw)


def ascending_ok(rows):
    ts = [r[0] for r in rows]
    return all(a <= b for a, b in zip(ts, ts[1:]))


D1, D2, D3 = "2026-08-03", "2026-08-04", "2026-08-05"


# ── 병합 ─────────────────────────────────────────────────────────────────────
def test_first_creation_is_sorted_even_though_the_server_sends_newest_first(tmp_path):
    """T1 — 구 코드는 수신 순서 그대로 저장해 내림차순 파일이 만들어졌고, 그것이 이중 기록의 씨앗이었다."""
    t = tmp_path / "a.csv"
    r = merge(tmp_path, t, received(D1, range(5), 4) + received(D2, range(3), 4))
    _, rows = read_rows(t)
    assert r["created"] and ascending_ok(rows) and len(rows) == 32
    assert rows[0][0].startswith(D1) and rows[-1][0].startswith(D2)


def test_incremental_overlap_and_repeat_sync_are_idempotent(tmp_path):
    """T2/T3 — 마지막 분 재수신 + 새 분 병합, 같은 증분을 또 동기화해도 바이트 단위로 같다."""
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received(D1, range(0, 5), 4))
    merge(tmp_path, t, received(D1, range(4, 7), 4))
    first = t.read_bytes()
    _, rows = read_rows(t)
    assert len(rows) == 28 and ascending_ok(rows)
    r = merge(tmp_path, t, received(D1, range(4, 7), 4))
    assert t.read_bytes() == first and r["added_rows"] == 0
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp") and p != "new.tmp"]


def test_genuinely_repeated_fills_are_preserved(tmp_path):
    """T4 — 같은 초에 같은 가격·수량으로 두 번 체결된 실제 체결은 병합 후에도 2건이다."""
    t = tmp_path / "a.csv"
    same = tick(D1, 3, 0)
    write_csv(t, [r for m in range(3) for r in minute_rows(D1, m, 4)] + [same, same])
    merge(tmp_path, t, [same, same] + list(reversed(minute_rows(D1, 2, 4))))
    _, rows = read_rows(t)
    assert sum(1 for r in rows if r == [str(v) for v in same]) == 2


def test_a_descending_file_is_refused_not_silently_doubled(tmp_path):
    """T5/T7 — 구 코드는 내림차순 파일에 병합해 이중 기록/행 손실을 냈다. 이제는 병합을 거부하고 원본을 그대로 둔다."""
    t = tmp_path / "a.csv"
    write_csv(t, received(D1, range(5), 4))                 # 내림차순으로 저장된 파일
    before = t.read_bytes()
    with pytest.raises(rds.OrderViolation):
        merge(tmp_path, t, received(D1, range(3, 7), 4))
    assert t.read_bytes() == before
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp") and p != "new.tmp"]      # 임시 파일 정리


def test_sparse_refetch_cannot_overwrite_a_complete_day_but_complete_refetch_fills_a_sparse_one(tmp_path):
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received(D1, range(6), 20))
    before = t.read_bytes()
    r = merge(tmp_path, t, received(D1, range(6), 2))       # 서버가 희소하게 돌려준 재수신
    assert t.read_bytes() == before and r["kept_groups"] == 6 and r["replaced_groups"] == 0
    t2 = tmp_path / "b.csv"
    merge(tmp_path, t2, received(D1, range(6), 2))          # 희소하게 저장된 날
    r = merge(tmp_path, t2, received(D1, range(6), 20))     # 나중에 온전한 데이터가 오면 채워진다
    _, rows = read_rows(t2)
    assert r["replaced_groups"] == 6 and len(rows) == 120


def test_a_half_fetched_last_minute_is_completed_by_the_next_sync(tmp_path):
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received(D1, range(3), 5) + list(reversed(minute_rows(D1, 3, 2))))      # 3분은 2건만 받은 상태
    merge(tmp_path, t, list(reversed(minute_rows(D1, 3, 5))) + received(D1, [4], 5))
    _, rows = read_rows(t)
    assert sum(1 for r in rows if r[0].endswith("09:03:00")) == 5 and len(rows) == 25 and ascending_ok(rows)


def test_invariant_violation_aborts_and_leaves_the_original_untouched(tmp_path, monkeypatch):
    """병합이 어떤 날짜의 행을 줄이면(엔진 버그를 가정) 교체하지 않는다."""
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received(D1, range(4), 6))
    before = t.read_bytes()
    monkeypatch.setattr(rds, "_better", lambda new, old, idx, tie_goes_to_new=False: True)          # 부실한 쪽을 항상 채택하도록 망가뜨림
    with pytest.raises(rds.MergeAborted, match="줄었다"):
        merge(tmp_path, t, received(D1, range(4), 2))
    assert t.read_bytes() == before


def test_header_mismatch_is_refused(tmp_path):
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received(D1, range(2), 3))
    tmp = tmp_path / "n.tmp"
    write_csv(tmp, [["2026-08-04 09:00:00", 1, 2]], header=["timestamp", "x", "y"])
    with pytest.raises(rds.MergeAborted, match="헤더"):
        rds.merge_file(str(tmp), str(t), log=lambda m: None)


def test_backup_is_taken_before_replacing_and_rotated(tmp_path):
    t, bk = tmp_path / "a.csv", tmp_path / "bk"
    merge(tmp_path, t, received(D1, range(2), 3), backup_dir=str(bk))
    assert not bk.exists()                                                     # 최초 생성엔 백업할 것이 없다
    orig = t.read_bytes()
    import time as _t
    for n in range(4):
        _t.sleep(1.05)
        merge(tmp_path, t, received(D2, range(n + 1), 2), backup_dir=str(bk), keep_backups=2)
    files = sorted(os.listdir(bk))
    assert len(files) == 2                                                     # 최근 2개만 유지
    assert (bk / files[0]).read_bytes() != orig                                # 가장 오래된 것은 밀려남


# ── 원장 + 전체 검증 ─────────────────────────────────────────────────────────
def test_ledger_records_every_day_and_verify_matches(tmp_path):
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received(D1, range(4), 5) + received(D2, range(4), 5))
    led = rds.load_ledger(str(t))
    assert set(led["days"]) == {D1, D2} and led["days"][D1]["rows"] == 20
    v = rds.verify_file(str(t))
    assert v["valid"] and all(d["ledger"] == "일치" for d in v["days"].values())


def test_verify_detects_silent_modification_of_a_sealed_day(tmp_path):
    """엔진을 거치지 않은 수정(사람이 편집기로 고침 등)도 날짜 해시로 드러난다."""
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received(D1, range(4), 5) + received(D2, range(4), 5))
    text = t.read_text().replace("100002.0", "100999.0", 1)
    t.write_text(text)
    v = rds.verify_file(str(t))
    assert not v["valid"] and v["days"][D1]["ledger"] == "변경됨" and v["days"][D2]["ledger"] == "일치"
    logs = []
    tmp = tmp_path / "n.tmp"
    write_csv(tmp, received(D3, range(2), 3))
    rds.merge_file(str(tmp), str(t), log=logs.append)
    assert any("외부에서 바뀐 날짜" in m for m in logs)


def test_verify_flags_doubled_unsorted_and_reference_mismatch_by_date(tmp_path):
    good = received(D1, range(4), 5)[::-1]
    t = tmp_path / "a.csv"
    write_csv(t, good + received(D2, range(4), 5)[::-1] + received(D2, range(4), 5))          # D2 는 [오름][내림] 이중 기록
    ref = {D1: 20.0, D2: 20.0}
    v = rds.verify_file(str(t), reference=ref)
    assert v["days"][D1]["valid"] and not v["days"][D2]["valid"] and not v["valid"]
    joined = " ".join(v["days"][D2]["issues"])
    assert "이중 기록" in joined and "기준 원천 대비 거래량 2.000배" in joined and "흩어져" not in " ".join(v["days"][D1]["issues"])
    assert v["summary"]["invalid_days"] == [D2]


# ── 복구 ─────────────────────────────────────────────────────────────────────
def _doubled_file(path, days_rows):
    """구 코드가 만든 [오름차순][내림차순] 이중 기록 파일"""
    asc = [r for rows in days_rows for r in rows]
    write_csv(path, asc + asc[::-1])


def test_repair_undoes_doubling_for_ticks_using_the_reference_volume(tmp_path):
    t = tmp_path / "t.csv"
    d1, d2 = minute_rows(D1, 0, 6) + minute_rows(D1, 1, 6), minute_rows(D2, 0, 6) + minute_rows(D2, 1, 6)
    _doubled_file(t, [d1, d2])
    ref = {D1: 12.0, D2: 12.0}
    rep = rds.repair_file(str(t), kind="tick", reference=ref, backup_dir=str(tmp_path / "bk"), apply=True, log=lambda m: None)
    _, rows = read_rows(t)
    assert rep["applied"] and rep["valid_after"] and len(rows) == 24 and ascending_ok(rows)
    assert all(rep["plan"][d]["action"] == "이중 기록→절반" for d in (D1, D2))
    assert os.listdir(tmp_path / "bk")                                                       # 복구 전 원본 백업


def test_repair_keeps_genuinely_repeated_fills_when_halving(tmp_path):
    same = tick(D1, 0, 0)
    base = [same, same] + minute_rows(D1, 0, 5)[1:] + minute_rows(D1, 1, 4)
    t = tmp_path / "t.csv"
    _doubled_file(t, [base])
    vol = float(len(base))
    rds.repair_file(str(t), kind="tick", reference={D1: vol}, apply=True, log=lambda m: None)
    _, rows = read_rows(t)
    assert len(rows) == len(base) and sum(1 for r in rows if r == [str(v) for v in same]) == 2


def test_repair_leaves_a_healthy_day_alone_and_does_not_guess_without_a_reference(tmp_path):
    t = tmp_path / "t.csv"
    write_csv(t, minute_rows(D1, 0, 5) + minute_rows(D2, 0, 5) + minute_rows(D2, 0, 5))      # D2 만 중복
    rep = rds.repair_file(str(t), kind="tick", reference={D1: 5.0}, apply=False, log=lambda m: None)
    assert rep["plan"][D1]["action"] == "그대로(정렬)" and D1 not in rep["changed_days"]
    assert "기준 원천 없음" in rep["plan"][D2]["note"] and rep["plan"][D2]["rows_after"] == rep["plan"][D2]["rows_before"]
    before = t.read_bytes()
    assert t.read_bytes() == before and not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]      # apply=False 는 아무것도 쓰지 않는다


def test_repair_candles_dedupes_by_timestamp_and_sorts(tmp_path):
    """일봉/1분봉: 시각이 유일 키 — [오름][내림] 이중 기록은 정렬 + ts 중복 제거로 복구된다."""
    def dayrow(i): return [f"2026-08-{i:02d} 00:00:00", 100 + i, 110 + i, 90 + i, 105 + i, 1000 + i, 5000 + i, 0]
    asc = [dayrow(i) for i in range(1, 11)]
    t = tmp_path / "d.csv"
    write_csv(t, asc + asc[::-1])
    rep = rds.repair_file(str(t), kind="candle", apply=True, backup_dir=str(tmp_path / "bk"), log=lambda m: None)
    _, rows = read_rows(t)
    assert rep["applied"] and len(rows) == 10 and ascending_ok(rows)
    assert rds.verify_file(str(t))["valid"]


def test_repair_imports_a_better_day_from_a_backup_source(tmp_path):
    """08-11 유형: 현재 파일에는 희소하게 남은 날이 백업에는 온전하게 있다 → 기준 거래량에 가까운 쪽을 가져온다."""
    t, src = tmp_path / "t.csv", tmp_path / "bak.csv"
    write_csv(t, minute_rows(D1, 0, 10) + minute_rows(D2, 0, 2) + minute_rows(D2, 1, 2))
    write_csv(src, minute_rows(D1, 0, 10)[:3] + minute_rows(D2, 0, 10) + minute_rows(D2, 1, 10))
    ref = {D1: 10.0, D2: 20.0}
    rep = rds.repair_file(str(t), kind="tick", reference=ref, sources=[str(src)], apply=True, log=lambda m: None)
    _, rows = read_rows(t)
    assert rep["plan"][D2]["action"].startswith("소스 bak.csv") and rep["plan"][D2]["ref_after"] == 1.0
    assert rep["plan"][D1]["action"] == "그대로(정렬)" and len(rows) == 30
    assert rds.verify_file(str(t), reference=ref)["valid"]


def test_repair_refuses_to_swap_when_the_result_is_still_structurally_broken(tmp_path, monkeypatch):
    t = tmp_path / "t.csv"
    write_csv(t, minute_rows(D1, 0, 3))
    monkeypatch.setattr(rds, "verify_file", lambda *a, **k: {"valid": False, "file_issues": ["강제 실패"], "days": {}, "summary": {}})
    before = t.read_bytes()
    rep = rds.repair_file(str(t), kind="tick", apply=True, log=lambda m: None)
    assert rep["applied"] is False and t.read_bytes() == before


def test_reference_volumes_ignore_a_doubled_reference_file(tmp_path):
    """기준 원천(1분봉)이 이중 기록이어도 거래량 기준이 두 배로 부풀지 않는다."""
    r = tmp_path / "m.csv"
    bars = [[f"{D1} 09:{m:02d}:00", 1, 1, 1, 1, 10.0 + m, 1, 0] for m in range(1, 6)]
    write_csv(r, bars + bars[::-1])
    assert rds.reference_volumes_from_csv(str(r)) == {D1: sum(10.0 + m for m in range(1, 6))}


def test_unchanged_sync_neither_rewrites_nor_backs_up(tmp_path):
    t, bk = tmp_path / "a.csv", tmp_path / "bk"
    merge(tmp_path, t, received(D1, range(4), 5), backup_dir=str(bk))
    inode = os.stat(t).st_ino
    merge(tmp_path, t, received(D1, range(4), 5), backup_dir=str(bk))
    assert os.stat(t).st_ino == inode and not bk.exists()


def test_backup_survives_the_replace(tmp_path):
    """하드링크 백업이라도 교체(os.replace)는 새 파일을 만들므로 백업 내용은 병합 전 그대로다."""
    t, bk = tmp_path / "a.csv", tmp_path / "bk"
    merge(tmp_path, t, received(D1, range(3), 4))
    before = t.read_bytes()
    merge(tmp_path, t, received(D2, range(3), 4), backup_dir=str(bk))
    (b,) = os.listdir(bk)
    assert (bk / b).read_bytes() == before and t.read_bytes() != before


# ── 수집기 배선: creon_collector.merge_and_save_data 가 엔진을 통해 병합/거부한다 ───────────────
def _collector(tmp_path, monkeypatch):
    from CAiModelBaker.core import creon_collector as cc
    monkeypatch.setattr(cc, "_DATA_VAULT_ROOT", str(tmp_path / "vault"))
    return cc


def test_collector_merge_uses_the_day_engine_and_warns_on_reference_mismatch(tmp_path, monkeypatch):
    cc = _collector(tmp_path, monkeypatch)
    logs = []
    (tmp_path / "vault").mkdir(exist_ok=True)
    target, ref = tmp_path / "vault" / "A1_tick.csv", tmp_path / "A1_1m.csv"
    write_csv(ref, [[f"{D1} 09:{m:02d}:00", 1, 1, 1, 1, 100.0, 1, 0] for m in range(1, 4)])          # 하루 거래량 300
    new = tmp_path / "n.tmp"
    write_csv(new, received(D1, range(0, 3), 5))                                                    # 틱 15건(거래량 15) — 기준의 5%
    out = cc.merge_and_save_data(str(new), str(target), local_log=logs.append, reference_csv=str(ref))
    assert out == str(target) and rds.load_ledger(str(target))["days"][D1]["rows"] == 15
    assert any("거래량이 맞지 않는 날짜" in m for m in logs)          # 부실 수신 경고
    _, rows = read_rows(target)
    assert ascending_ok(rows)


def test_collector_rejects_an_unsorted_target_keeps_it_and_quarantines_the_new_data(tmp_path, monkeypatch):
    cc = _collector(tmp_path, monkeypatch)
    logs = []
    (tmp_path / "vault").mkdir(exist_ok=True)
    target = tmp_path / "vault" / "A1_1d.csv"
    write_csv(target, received(D1, range(5), 4))                                                    # 정렬 안 된 옛 파일
    before = target.read_bytes()
    new = tmp_path / "n.tmp"
    write_csv(new, received(D1, range(3, 7), 4))
    assert cc.merge_and_save_data(str(new), str(target), local_log=logs.append) is None
    assert target.read_bytes() == before and any("원본은 그대로입니다" in m for m in logs)
    kept = os.listdir(tmp_path / "vault" / "quarantine" / "rejected_merges")
    assert len(kept) == 1 and not new.exists()                                                       # 신규 수신분은 버리지 않고 보관


def test_equal_rows_and_volume_new_wins_only_on_the_latest_day(tmp_path):
    """형성 중인 마지막 봉의 값 갱신은 받아들이고(최신 날짜), 봉인된 과거 날짜의 충돌은 기존 값을 지킨다."""
    t = tmp_path / "a.csv"
    def bar(day, hm, close):
        return [f"{day} {hm}:00", 1, 1, 1, close, 10.0, 1, 0]
    write_csv(t, [bar(D1, "09:01", 100), bar(D1, "09:02", 101), bar(D2, "09:01", 200), bar(D2, "09:02", 201)])
    r = merge(tmp_path, t, [bar(D1, "09:02", 999), bar(D2, "09:02", 777)])
    _, rows = read_rows(t)
    close = {r_[0]: r_[4] for r_ in rows}
    assert close[f"{D2} 09:02:00"] == "777"          # 최신 날짜: 갱신 채택
    assert close[f"{D1} 09:02:00"] == "101" and r["conflicts"] == 1      # 과거 날짜: 기존 유지 + 충돌 기록


# ── 재구성(assemble) — 새로 받은 원천 우선, 옛 사본으로 부족분 채움 ───────────────────
def _asm(tmp_path, fresh_rows, sources_rows, kind, reference=None, fresh_header=H):
    fresh = None
    if fresh_rows is not None:
        fresh = str(tmp_path / "fresh.csv"); write_csv(fresh, fresh_rows, fresh_header)
    srcs = []
    for k, rows in enumerate(sources_rows):
        p = str(tmp_path / f"src{k}.csv"); write_csv(p, rows); srcs.append(p)
    out = str(tmp_path / "out" / "a.csv")
    rep = rds.assemble_file(fresh, srcs, out, kind=kind, reference=reference, log=lambda m: None)
    return rep, read_rows(out)[1]


def test_assemble_tick_prefers_complete_fresh_day_and_fills_older_days_from_scrambled_backup(tmp_path):
    fresh = minute_rows(D2, 0, 10) + minute_rows(D3, 0, 10)
    # 옛 사본: 역순 + 이중 기록([오름][내림]) — D1 은 새 파일에 없음, D2 는 희소
    old = minute_rows(D1, 0, 8) + minute_rows(D2, 0, 3)
    scrambled = old + list(reversed(old))
    rep, rows = _asm(tmp_path, fresh, [scrambled], "tick", reference={D1: 8.0, D2: 10.0, D3: 10.0})
    assert ascending_ok(rows)
    assert rep["plan"][D1]["origin"].startswith("src:") and rep["plan"][D1]["variant"] == "이중 기록→절반"
    assert rep["plan"][D2]["origin"] == "fresh" and rep["plan"][D3]["origin"] == "fresh"
    assert len(rows) == 8 + 10 + 10 and rep["valid"]


def test_assemble_tick_keeps_backup_day_when_fresh_is_sparser(tmp_path):
    rep, rows = _asm(tmp_path, minute_rows(D1, 0, 2), [minute_rows(D1, 0, 10)], "tick", reference={D1: 10.0})
    assert rep["plan"][D1]["origin"].startswith("src:") and rep["plan"][D1]["fresh_ratio"] == 0.2
    assert len(rows) == 10


def test_assemble_candle_fills_missing_minutes_of_the_boundary_day_and_counts_conflicts(tmp_path):
    fresh = [tick(D2, m, 0) for m in range(3, 6)]
    old = [tick(D2, m, 0) for m in range(0, 5)]
    old[3] = tick(D2, 3, 7)                     # 같은 시각, 다른 값 → 새 파일 값 유지 + 충돌 집계
    rep, rows = _asm(tmp_path, fresh, [list(reversed(old))], "candle")
    assert [r[0][11:16] for r in rows] == ["09:00", "09:01", "09:02", "09:03", "09:04", "09:05"]
    assert rows[3][1] == str(100000.0) and rep["conflicts"] == 1 and rep["plan"][D2]["filled_ts"] == 3


def test_assemble_daily_never_mixes_adjusted_prices_inside_the_fresh_range(tmp_path):
    fresh = [[f"{D2} 00:00:00", 1, 1, 1, 1, 1, 1, 1], [f"{D3} 00:00:00", 1, 1, 1, 1, 1, 1, 1]]
    old = [[f"{D1} 00:00:00", 2, 2, 2, 2, 2, 2, 2], [f"{D2} 00:00:00", 2, 2, 2, 2, 2, 2, 2], ["2026-08-06 00:00:00", 2, 2, 2, 2, 2, 2, 2]]
    rep, rows = _asm(tmp_path, fresh, [old], "daily")
    assert [r[0][:10] for r in rows] == [D1, D2, D3]
    assert rows[1][1] == "1" and "note" in rep["plan"]["2026-08-06"]


def test_assemble_skips_a_source_with_a_different_header(tmp_path):
    other = str(tmp_path / "other.csv"); write_csv(other, minute_rows(D1, 0, 5), header=H[:-1] + ["x"])
    fresh = str(tmp_path / "fresh.csv"); write_csv(fresh, minute_rows(D2, 0, 5))
    out = str(tmp_path / "o.csv")
    rep = rds.assemble_file(fresh, [other], out, kind="tick", log=lambda m: None)
    assert list(rep["plan"]) == [D2]


def test_assemble_candle_float_formatting_differences_are_not_conflicts(tmp_path):
    fresh = [[f"{D1} 09:01:00", "432.79998779296875", 1, 1, 1, 1, 1, 1]]
    old = [[f"{D1} 09:01:00", "432.7999877929688", 1, 1, 1, 1, 1, 1]]
    rep, rows = _asm(tmp_path, fresh, [old], "candle")
    assert rep["conflicts"] == 0 and rows[0][1] == "432.79998779296875"


# ── 수신 순서와 무관한 병합 (2026-09-25) ────────────────────────────────────────
def _chron(days_minutes, n):
    return [r for d, ms in days_minutes for m in ms for r in minute_rows(d, m, n)]


@pytest.mark.parametrize("order", ["newest_first", "oldest_first", "days_up_rows_down", "days_down_rows_up"])
def test_merge_result_is_identical_whatever_order_the_rows_arrive_in(tmp_path, order):
    """같은 체결을 어떤 순서로 받든 결과 파일은 같다 — 같은 분 안의 체결 순서까지."""
    plan = [(D1, range(3)), (D2, range(2))]
    chron = _chron(plan, 4)
    if order == "newest_first":
        rows = list(reversed(chron))
    elif order == "oldest_first":
        rows = chron
    elif order == "days_up_rows_down":
        rows = [r for d, ms in plan for r in reversed(_chron([(d, ms)], 4))]
    else:
        rows = [r for d, ms in reversed(plan) for r in _chron([(d, ms)], 4)]
    t = tmp_path / "a.csv"
    merge(tmp_path, t, rows)
    _, got = read_rows(t)
    assert got == [[str(x) for x in r] for r in chron]


def test_sealed_day_supplement_is_applied_but_different_data_goes_to_review(tmp_path):
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received(D1, range(3), 3) + received(D2, range(2), 3))
    # D1 09:01 에 체결 2개 추가(보강), D1 09:02 는 값이 다른 데이터(충돌)
    sup = list(reversed(minute_rows(D1, 1, 5)))
    diff = list(reversed([tick(D1, 2, i, vol=9.0) for i in range(3)]))
    r = merge(tmp_path, t, diff + sup)                                       # 크레온처럼 최신→과거
    assert r["replaced_groups"] == 1 and r["conflicts"] == 1
    _, rows = read_rows(t)
    assert sum(1 for x in rows if x[0] == f"{D1} 09:01:00") == 5            # 보강 반영
    assert all(float(x[5]) == 1.0 for x in rows if x[0] == f"{D1} 09:02:00")  # 충돌은 기존 유지
    pend = rds.pending_conflicts(str(t))
    assert len(pend) == 1 and pend[0]["day"] == D1 and len(pend[0]["new"]) == 3
    v = rds.verify_file(str(t))
    assert not v["valid"] and "사용자 확인" in " ".join(v["days"][D1]["issues"])
    # 같은 충돌이 다시 들어와도 목록은 하나
    merge(tmp_path, t, diff)
    assert len(rds.pending_conflicts(str(t))) == 1


def test_resolving_a_conflict_with_the_new_data_replaces_only_that_time(tmp_path):
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received(D1, range(3), 3) + received(D2, range(2), 3))
    merge(tmp_path, t, list(reversed([tick(D1, 2, i, vol=9.0) for i in range(3)])))
    rep = rds.resolve_conflicts(str(t), D1, use="new", log=lambda m: None)
    assert rep["resolved"] == 1
    _, rows = read_rows(t)
    assert [float(x[5]) for x in rows if x[0] == f"{D1} 09:02:00"] == [9.0, 9.0, 9.0]
    assert sum(1 for x in rows if x[0].startswith(D1)) == 9 and ascending_ok(rows)
    v = rds.verify_file(str(t))
    assert v["valid"] and v["pending_conflicts"] == 0


def test_resolving_with_old_just_closes_the_item(tmp_path):
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received(D1, range(3), 3) + received(D2, range(2), 3))
    before = t.read_bytes()
    merge(tmp_path, t, list(reversed([tick(D1, 2, i, vol=9.0) for i in range(3)])))
    rds.resolve_conflicts(str(t), use="old", log=lambda m: None)
    assert t.read_bytes() == before and rds.pending_conflicts(str(t)) == []


def test_unrecoverable_ranges_survive_merges_and_are_labelled_in_verify(tmp_path):
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received(D1, range(2), 2) + received("2026-08-10", range(2), 2))
    rds.record_unrecoverable(str(t), f"{D1} 09:01:00", "2026-08-10 09:00:00", "test")
    merge(tmp_path, t, received("2026-08-11", range(2), 2))                  # 원장 재작성 후에도 유지
    assert rds.unrecoverable_ranges(str(t)) == [(D1, "2026-08-10")]
    tdays = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11"]
    v = rds.verify_file(str(t), trading_days=tdays)
    assert set(v["missing_days"]) == {"2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"}
    assert all(r.startswith("영구 결손") for r in v["missing_days"].values())
    assert not v["complete"]


def test_collector_gap_rewind_skips_recorded_unrecoverable_days(tmp_path):
    from CAiModelBaker.core import creon_collector as cc
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received("2026-09-01", range(2), 2) + received("2026-09-08", range(2), 2) + received("2026-09-09", range(2), 2))
    assert cc.get_existing_last_timestamp(str(t), local_log=lambda m: None).startswith("2026-09-01")   # 09-02~07 결측 → 되감기
    rds.record_unrecoverable(str(t), "2026-09-01 09:01:00", "2026-09-08 09:00:00", "test")
    assert cc.get_existing_last_timestamp(str(t), local_log=lambda m: None) == "2026-09-09 09:01:00"   # 영구 결손은 건너뜀


def test_ingest_records_a_source_floor_beyond_the_checkpoint(tmp_path):
    """원천이 줄 수 있는 데이터를 다 받았는데 기록 지점에 닿지 못하면 그 사이 거래일을 영구 결손으로 기록한다."""
    t = tmp_path / "a.csv"
    merge(tmp_path, t, received("2026-09-01", range(2), 2))
    lost = lambda a, b: ["2026-09-02", "2026-09-03"]
    logs = []

    def ing(rows, **kw):
        tmp = str(tmp_path / "in.tmp"); write_csv(tmp, rows)
        return rds.ingest_file(tmp, str(t), quarantine_dir=str(tmp_path / "q"), lost_days_fn=lost, log=logs.append, **kw)

    ing(received("2026-09-04", range(2), 2), requested_after="2026-09-01 09:01:00", source_exhausted=True)
    assert rds.unrecoverable_ranges(str(t)) == [("2026-09-01", "2026-09-04")] and any("영구 결손" in m for m in logs)
    # 기록 지점에 닿았거나(requested_after 없음) 원천이 소진되지 않았으면(오류로 중단) 기록하지 않는다
    t2 = tmp_path / "b.csv"; t = t2
    merge(tmp_path, t2, received("2026-09-01", range(2), 2))
    ing(received("2026-09-04", range(2), 2), requested_after=None, source_exhausted=True)
    ing(received("2026-09-05", range(2), 2), requested_after="2026-09-01 09:01:00", source_exhausted=False)
    assert rds.unrecoverable_ranges(str(t2)) == []


def test_ingest_quarantines_bars_off_the_interval_grid(tmp_path):
    """미완성 봉(예: 21:02:25)처럼 봉 간격 격자에 맞지 않는 시각은 저장하지 않고 격리한다."""
    t = tmp_path / "a.csv"
    tmp = str(tmp_path / "in.tmp")
    write_csv(tmp, [tick(D1, 1, 0), tick(D1, 2, 1), [f"{D1} 09:02:25", 1, 1, 1, 1, 1, 1, 1]])
    rds.ingest_file(tmp, str(t), quarantine_dir=str(tmp_path / "q"), interval_seconds=60, log=lambda m: None)
    _, rows = read_rows(t)
    assert [r[0][11:] for r in rows] == ["09:01:00", "09:02:00"]
    assert os.listdir(tmp_path / "q" / "misaligned")
    v = rds.verify_file(str(t), interval_seconds=60)
    assert v["valid"]


def test_ingest_aligns_columns_and_widens_the_header(tmp_path):
    """신규 수신분의 열 순서가 달라도 대상 순서로 맞추고, 새 열은 기존 행을 빈 값으로 두고 뒤에 붙인다."""
    t = tmp_path / "a.csv"
    write_csv(str(t), [[f"{D1} 09:01:00", 1, 2]], header=["timestamp", "open", "close"])
    tmp = str(tmp_path / "in.tmp")
    write_csv(tmp, [[5, f"{D1} 09:02:00", 3, 9]], header=["close", "timestamp", "open", "trades"])
    rep = rds.ingest_file(tmp, str(t), quarantine_dir=str(tmp_path / "q"), log=lambda m: None)
    h, rows = read_rows(t)
    assert h == ["timestamp", "open", "close", "trades"]
    assert rows == [[f"{D1} 09:01:00", "1", "2", ""], [f"{D1} 09:02:00", "3", "5", "9"]] and rep["added_rows"] == 1


def test_epoch_timestamps_are_grouped_by_utc_day(tmp_path):
    """바이낸스처럼 첫 열이 epoch(ms)여도 같은 절차로 정렬·날짜 구분한다 (행 내용은 그대로)."""
    t = tmp_path / "a.csv"
    tmp = str(tmp_path / "in.tmp")
    rows = [["1790208000000.0", 1, 1], ["1790121600000.0", 2, 2], ["1790121660000.0", 3, 3]]   # 09-24 00:00, 09-23 00:00, 09-23 00:01 UTC
    write_csv(tmp, rows, header=["timestamp", "close", "volume"])
    rds.ingest_file(tmp, str(t), quarantine_dir=str(tmp_path / "q"), interval_seconds=60, log=lambda m: None)
    _, got = read_rows(t)
    assert [r[0] for r in got] == ["1790121600000.0", "1790121660000.0", "1790208000000.0"]
    assert sorted(rds.load_ledger(str(t))["days"]) == ["2026-09-23", "2026-09-24"]


def test_interval_grid_allows_half_hour_session_offsets_but_not_snapshots():
    assert rds.is_aligned("2024-07-03 22:30:00", 3600)          # 장 시작(9:30 ET)에 맞춘 시간봉
    assert not rds.is_aligned("2024-07-03 22:30:05", 3600)
    assert rds.is_aligned("2026-08-21 18:25:00", 300) and not rds.is_aligned("2026-08-21 18:28:00", 300)
    assert not rds.is_aligned("2026-08-21 15:33:10", 60)         # 진행 중 봉 스냅샷
    assert rds.is_aligned("2026-09-25 00:00:00", 86400 * 2)      # 일봉 이상은 검사하지 않음
