# -*- coding: utf-8 -*-
"""연구 저장소(DataVault/staging/<stage>)용 Creon 수집 드라이버 (Windows 32-bit Python, 표준 라이브러리만).

연구 데이터는 종목 원장(symbols_registry)·동기화와 분리한다 — CTRADE_DATA_VAULT_ROOT 를 staging/<stage> 로 돌려,
수집기의 공통 저장 절차(일자 저장소·봉인·격리)를 그 안에서 그대로 쓴다. 모든 단계는 증분·멱등(받은 건 건너뜀).
대시보드의 '연구 데이터셋' 패널(shared.data_library.kr_equity.runner)이 이 파일을 실행한다.

  py -3.10-32 universe_driver.py universe                       # 상장 보통주 스냅샷 → reference/listed_snapshot.csv
  py -3.10-32 universe_driver.py daily --codes-file ../../DataVault/staging/kr_equity/targets_creon.txt
                                                                # 수정 일봉 증분(처음엔 상장 이후 전체). --full: 지우고 다시(기업행위 반영)
  py -3.10-32 universe_driver.py daily_unadj --codes-file ...   # 무수정 일봉 증분
  py -3.10-32 universe_driver.py daily_ext --codes-file ...     # 주식수·시총·외국인·기관(일별) 증분. --full: 전체 재수집
  py -3.10-32 universe_driver.py index                          # 지수 일봉(U### 128개) 증분
  py -3.10-32 universe_driver.py minute --codes-file t.txt      # 1분봉(받은편지함 → Linux ingest_inbox.py)
  py -3.10-32 universe_driver.py minute --refresh --codes-file t.txt   # 이미 받은 종목도 마지막 시점 이후 증분(없는 종목은 전체)
  --stage <이름>  staging 하위 데이터셋(기본 kr_equity) — 수집기 import 전에 읽는다
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
_STAGE_NAME = sys.argv[sys.argv.index("--stage") + 1] if "--stage" in sys.argv else "kr_equity"
STAGE = os.path.join(REPO, "DataVault", "staging", _STAGE_NAME)
os.environ["CTRADE_DATA_VAULT_ROOT"] = STAGE          # 수집기 import 전에 지정해야 한다
sys.path.insert(0, HERE)

import creon_collector as cc  # noqa: E402

SNAPSHOT_CSV = os.path.join(STAGE, "reference", "listed_snapshot.csv")
INBOX = os.path.join(STAGE, "inbox_1m")
LOG_PATH = os.path.join(STAGE, "driver_log.txt")
STOCK_KINDS = {1: "STOCK", 6: "DR", 13: "FOREIGN"}   # CpCodeMgr.GetStockSectionKind — ETF(10)/ETN(17)/리츠(3) 등 제외
DAILY_HEADER = ["timestamp", "upbit_open", "upbit_high", "upbit_low", "upbit_close", "upbit_volume", "upbit_trade_price"]
EXT_FIELDS = [(0, "date"), (5, "close_adj"), (12, "shares_listed"), (13, "market_cap"), (14, "foreign_limit_qty"),
              (16, "foreign_hold_qty"), (17, "foreign_hold_pct"), (18, "adj_date"), (19, "adj_ratio"),
              (20, "inst_net_qty"), (21, "inst_net_cum")]       # Creon 은 요청 순서와 무관하게 필드 번호 오름차순으로 돌려준다
INDEX_HEADER = ["date", "open", "high", "low", "close", "volume", "value"]


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    os.makedirs(STAGE, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------- 상장 스냅샷
def build_universe():
    import win32com.client
    mgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")
    rows = []
    for mkt, label in [(1, "KOSPI"), (2, "KOSDAQ")]:
        for code in mgr.GetStockListByMarket(mkt):
            kind = mgr.GetStockSectionKind(code)
            if kind not in STOCK_KINDS:
                continue
            name = mgr.CodeToName(code)
            rows.append({
                "code": code, "name": name, "market": label, "section": STOCK_KINDS[kind],
                "is_preferred": int(code[-1] != "0"),
                "is_spac": int("스팩" in name),
                "control_kind": mgr.GetStockControlKind(code),          # 0 정상, 1 주의, 2 경고, 3 위험예고, 4 위험
                "supervision_kind": mgr.GetStockSupervisionKind(code),  # 0 일반, 1 관리
                "status_kind": mgr.GetStockStatusKind(code),            # 0 정상, 1 거래정지, 2 거래중단
                "listed_date": mgr.GetStockListedDate(code),
                "snapshot_date": datetime.now().strftime("%Y-%m-%d"),
            })
    os.makedirs(os.path.dirname(SNAPSHOT_CSV), exist_ok=True)
    tmp = SNAPSHOT_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, SNAPSHOT_CSV)
    by = {}
    for r in rows:
        by[(r["market"], r["section"])] = by.get((r["market"], r["section"]), 0) + 1
    log(f"[universe] {len(rows)} 종목 저장 → {SNAPSHOT_CSV} {by} — 다음: Linux kind 단계로 universe.csv 재생성")


# ---------------------------------------------------------------- 공통
def _chart_rows(code, fields, adj, count, kind=ord("D")):
    """StockChart 기간 요청을 Continue 로 끝까지(최신→과거). fields 는 필드 번호 목록."""
    import win32com.client
    cp = win32com.client.Dispatch("CpUtil.CpCybos")
    ch = win32com.client.Dispatch("CpSysDib.StockChart")
    ch.SetInputValue(0, code)
    ch.SetInputValue(1, ord("2"))
    ch.SetInputValue(4, count)
    ch.SetInputValue(5, fields)
    ch.SetInputValue(6, kind)
    ch.SetInputValue(9, ord(adj))
    rows = []
    while len(rows) < count:
        if cp.GetLimitRemainCount(1) <= 2:
            time.sleep(max(0.5, cp.GetLimitRemainTime(1) / 1000.0 + 0.1))
        if ch.BlockRequest() != 0:
            break
        k = ch.GetHeaderValue(3)
        for i in range(k):
            rows.append([ch.GetDataValue(j, i) for j in range(len(fields))])
        if k <= 0 or not ch.Continue:
            break
    return rows


def _last_key(out):
    """기존 파일의 마지막(최대) 첫 열 값 — 날짜 오름차순 파일이라 마지막 줄."""
    if not os.path.exists(out):
        return None
    last = None
    with open(out, encoding="utf-8") as f:
        next(f, None)
        for ln in f:
            if ln.strip():
                last = ln.split(",", 1)[0]
    return last


def _incremental_count(last_key, full):
    """마지막 날짜 이후 필요한 봉 수(여유 10) — 전체면 서버 끝까지."""
    if full or not last_key:
        return 99999999
    d = str(last_key)[:10].replace("-", "")
    try:
        days = (datetime.now() - datetime.strptime(d[:8], "%Y%m%d")).days
    except ValueError:
        return 99999999
    return max(10, days + 10)


def _merge_csv(out, header, rows, full=False):
    """연구용 보조 원본(날짜 오름차순, 읽기 전용): 새 날짜 행만 뒤에 붙인다. full 이면 통째로 다시 쓴다."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    old = []
    if os.path.exists(out) and not full:
        with open(out, encoding="utf-8") as f:
            r = csv.reader(f)
            next(r, None)
            old = [row for row in r if row]
    have = {row[0] for row in old}
    new = [[str(v) for v in row] for row in rows if str(row[0]) not in have]
    if old and not new:
        return out, 0
    merged = sorted(old + new, key=lambda r: r[0])
    tmp = out + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(merged)
    if os.path.exists(out):
        os.chmod(out, 0o644)
    os.replace(tmp, out)
    os.chmod(out, 0o444)
    return out, len(new)


# ---------------------------------------------------------------- 단계
def download_daily(code, full=False, local_log=print):
    """수정 일봉 — 수집기 공통 절차(증분 upsert). --full: 수정주가는 기업행위 뒤에 과거가 통째로 바뀌므로 파일을 지우고 다시 받는다."""
    out = os.path.join(STAGE, "raw", "creon", "candle_1d", f"{code}_1d.csv")
    if full:
        for suffix in ("", ".manifest.json", ".days.json"):
            try:
                os.remove(out + suffix)
            except OSError:
                pass
    return cc.download_daily_data(code, count=99999999, local_log=local_log)


def download_daily_unadjusted(code, full=False, local_log=print):
    """무수정 일봉(불변) — 마지막 날짜 이후만 받아 공통 절차(merge_and_save_data)로 붙인다."""
    import win32com.client
    out = os.path.join(STAGE, "raw", "creon", "candle_1d_unadj", f"{code}_1d.csv")
    count = _incremental_count(_last_key(out), full)
    cp = win32com.client.Dispatch("CpUtil.CpCybos")
    ch = win32com.client.Dispatch("CpSysDib.StockChart")
    ch.SetInputValue(0, code)
    ch.SetInputValue(1, ord("2"))
    ch.SetInputValue(4, count)
    ch.SetInputValue(5, [0, 2, 3, 4, 5, 8, 9])
    ch.SetInputValue(6, ord("D"))
    ch.SetInputValue(9, ord("0"))             # 0: 무수정
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp_unadj"
    n = 0
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(DAILY_HEADER)
        while n < count:
            if cp.GetLimitRemainCount(1) <= 2:
                time.sleep(max(0.5, cp.GetLimitRemainTime(1) / 1000.0 + 0.1))
            if ch.BlockRequest() != 0:
                break
            k = ch.GetHeaderValue(3)
            for i in range(k):
                d = str(ch.GetDataValue(0, i))
                w.writerow([f"{d[:4]}-{d[4:6]}-{d[6:8]} 00:00:00"] + [ch.GetDataValue(j, i) for j in range(1, 7)])
            n += k
            if k <= 0 or not ch.Continue:
                break
    if n == 0:
        os.remove(tmp)
        return None
    return cc.merge_and_save_data(tmp, out, subset_keys=["timestamp"], local_log=local_log)


def download_daily_ext(code, full=False, local_log=print):
    """일별 상장주식수·시가총액·외국인 보유·기관 순매수(수정 기준). 값이 과거까지 재조정되는 열이라 --full 로 가끔 전체 재수집."""
    out = os.path.join(STAGE, "raw", "creon", "daily_ext", f"{code}_1d_ext.csv")
    count = _incremental_count(_last_key(out), full)
    rows = _chart_rows(code, [f for f, _ in EXT_FIELDS], "1", count)
    if not rows:
        local_log("수신 없음")
        return None
    return _merge_csv(out, [n for _, n in EXT_FIELDS], rows, full=full)[0]


def index_codes():
    """차트용 지수 코드(U###) — CpCodeMgr.GetIndustryList 는 새 KRX 형식(KGG01P …)을 돌려주는데 StockChart 는
    예전 U### 만 받는다(2026-10-13 실측). U001~U399 중 이름이 있는 코드만(KOSPI·업종·K200 계열·KOSDAQ·업종·150 계열)."""
    import win32com.client
    mgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")
    codes = {}
    for i in range(1, 400):
        c = "U%03d" % i
        try:
            name = mgr.GetIndustryName(c)
        except Exception:
            name = ""
        if name:
            codes[c] = name
    return codes


def download_index(code, full=False, local_log=print):
    out = os.path.join(STAGE, "raw", "creon", "index_1d", f"{code}_1d.csv")
    count = _incremental_count(_last_key(out), full)
    rows = _chart_rows(code, [0, 2, 3, 4, 5, 8, 9], "1", count)
    if not rows:
        local_log("수신 없음")
        return None
    return _merge_csv(out, INDEX_HEADER, rows, full=full)[0]


def _defer_candle_save(tmp_out, key, last_ts, reached_existing_point, server_exhausted, collected_at, local_log=print):
    """1분봉 일자 저장(파일 ~1,000개)을 WSL 경로에 Windows 에서 쓰면 종목당 ~65s 걸린다 — 수신 원본만 받은편지함에 두고,
    병합·봉인은 Linux 의 ingest_inbox.py 가 같은 함수(cc._candle_day_save)로 한다."""
    os.makedirs(INBOX, exist_ok=True)
    dst = os.path.join(INBOX, f"{key}.csv")
    os.replace(tmp_out, dst)
    meta = {"key": key, "last_ts": last_ts, "reached_existing_point": reached_existing_point,
            "server_exhausted": server_exhausted, "collected_at": collected_at.isoformat()}
    with open(dst + ".json.tmp", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    os.replace(dst + ".json.tmp", dst + ".json")       # .json 이 있으면 수신 완료
    return dst


def load_codes(codes_file):
    with open(codes_file, encoding="utf-8") as f:
        if codes_file.endswith(".txt"):
            return [ln.strip() for ln in f if ln.strip()]
        return [r["code"] for r in csv.DictReader(f)]


def run_batch(kind, codes, full, count):
    quiet = []
    ok = fail = 0
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        quiet.clear()
        try:
            if kind == "daily":
                res = download_daily(code, full=full, local_log=quiet.append)
            elif kind == "daily_unadj":
                res = download_daily_unadjusted(code, full=full, local_log=quiet.append)
            elif kind == "daily_ext":
                res = download_daily_ext(code, full=full, local_log=quiet.append)
            elif kind == "index":
                res = download_index(code, full=full, local_log=quiet.append)
            else:
                res = cc.download_minute_data(code, interval=1, count=count, local_log=quiet.append)
        except Exception as e:  # 한 종목 실패가 배치를 멈추지 않게
            res = None
            quiet.append(f"예외: {e}")
        if res:
            ok += 1
        else:
            fail += 1
            log(f"[{kind}] 실패 {code}: {' | '.join(str(q) for q in quiet[-3:])}")
        if i % 50 == 0 or i == len(codes):
            el = time.time() - t0
            log(f"[{kind}] {i}/{len(codes)} 성공 {ok} 실패 {fail} 경과 {el/60:.1f}분 (종목당 {el/i:.2f}s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["universe", "daily", "daily_unadj", "daily_ext", "index", "minute"])
    ap.add_argument("--codes-file", default=None, help="종목 목록(.txt 한 줄 하나 / .csv code 열). 기본 targets_creon.txt")
    ap.add_argument("--count", type=int, default=99999999, help="minute 전용")
    ap.add_argument("--limit", type=int, default=None, help="앞에서 N 종목만 (실측용)")
    ap.add_argument("--refresh", action="store_true", help="minute 전용: 이미 받은 종목도 건너뛰지 않고 증분 수집(수집기가 마지막 시점부터 이어 받는다)")
    ap.add_argument("--full", action="store_true", help="증분 대신 전체 재수집(daily: 지우고 다시, daily_ext/index: 덮어쓰기)")
    ap.add_argument("--stage", default="kr_equity", help="staging 하위 데이터셋 이름 (모듈 로드 때 이미 반영)")
    a = ap.parse_args()
    ok, msg = cc.check_creon_connection()
    if not ok:
        print(msg)
        sys.exit(2)
    os.makedirs(STAGE, exist_ok=True)
    if a.step == "universe":
        build_universe()
        return
    if a.step == "index":
        names = index_codes()
        with open(os.path.join(STAGE, "index_codes.csv"), "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows([["code", "name"]] + sorted(names.items()))
        codes = sorted(names)
    else:
        codes = load_codes(a.codes_file or os.path.join(STAGE, "targets_creon.txt"))
    if a.limit:
        codes = codes[: a.limit]
    if a.step == "minute":
        cc._candle_day_save = _defer_candle_save
        day_root = os.path.join(STAGE, "raw", "creon", "candle_1m")
        before = len(codes)
        codes = [c for c in codes if (a.refresh or not os.path.isdir(os.path.join(day_root, c)))
                 and not os.path.exists(os.path.join(INBOX, f"{c}.csv.json"))]
        log(f"[minute] 이미 받은 {before - len(codes)} 종목 건너뜀")
    log(f"[{a.step}] 시작 {len(codes)} 종목 full={a.full} vault={STAGE}")
    run_batch(a.step, codes, a.full, a.count)
    if a.step == "minute" and not a.limit:
        os.makedirs(INBOX, exist_ok=True)
        open(os.path.join(INBOX, "DONE"), "w").close()       # ingest_inbox --watch 종료 신호


if __name__ == "__main__":
    main()
