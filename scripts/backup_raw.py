#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/backup_raw.py — DataVault/raw 외부(로컬 드라이브) 증분 백업
==================================================================
  python scripts/backup_raw.py --set-target /mnt/d/CTradeBackup    # 대상 드라이브 지정 (DataVault/config/backup.json)
  python scripts/backup_raw.py                                     # 증분 백업 (기본: 설정된 모든 대상)
  python scripts/backup_raw.py --dry-run                           # 무엇을 복사할지만 출력
  python scripts/backup_raw.py --verify                            # 백업본 전체 해시 재검증(스크러빙)
  python scripts/backup_raw.py --target /mnt/e/X                   # 설정 없이 1회 지정

대상 구조:  <대상>/raw/<원본과 같은 상대경로>,  <대상>/_versions/...(교체 전 이전 판),  <대상>/backup_manifest.json
규칙:
  1. 일자별 봉인본(tick/A{sym}/YYYY-MM-DD.csv·.meta.json)은 불변 — 한 번 복사하고, 이후 원본과 내용이 다르면 덮어쓰지 않고 CONFLICT 로 보고한다.
  2. 그 외 파일(통합 CSV·1분봉·일봉 등)은 바뀌면 새 판으로 교체하되 이전 판을 _versions 에 keep_versions 개까지 보관한다.
  3. 복사는 .part 로 쓰고 sha256 검증 후 원자적으로 교체한다. 복사 중 원본이 바뀌면 그 파일은 건너뛰고 다음 실행에 맡긴다.
  4. 대상에서 파일을 지우지 않는다(원본이 사라져도 백업은 남는다). 대상이 raw 와 같은 디스크/하위 경로면 거부한다.
USB 가 빠져 있으면(경로 없음) 아무것도 만들지 않고 실패한다 — 마운트되지 않은 빈 경로에 쓰는 사고를 막기 위해 대상 폴더는 미리 존재해야 한다.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_ROOT = os.path.join(REPO_ROOT, "DataVault", "raw")
CONFIG = os.path.join(REPO_ROOT, "DataVault", "config", "backup.json")
MANIFEST = "backup_manifest.json"
SEALED_RE = re.compile(r"(^|/)(tick|candle_1m)/[^/]+/\d{4}-\d{2}-\d{2}\.(csv|meta\.json)$")
SKIP_RE = re.compile(r"(\.tmp$|\.part$|\.lock$|(^|/)\.merge_quarantine/)")
CHUNK = 8 * 1024 * 1024


def load_config():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"targets": [], "keep_versions": 3}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def is_sealed(rel):
    return bool(SEALED_RE.search(rel))


def iter_source(src_root):
    for dp, dns, fns in os.walk(src_root):
        for fn in sorted(fns):
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, src_root).replace(os.sep, "/")
            if not SKIP_RE.search(rel):
                yield rel, full


def load_manifest(target):
    try:
        with open(os.path.join(target, MANIFEST), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"files": {}}


def save_manifest(target, man):
    p = os.path.join(target, MANIFEST)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(man, f, indent=1, ensure_ascii=False)
    os.replace(p + ".tmp", p)


class BackupError(Exception):
    """대상 폴더 미연결·겹침·공간 부족 등 — 아무것도 쓰지 않고 중단"""


def normalize_path(p):
    """윈도우식 경로(D:\\Backup)를 WSL/리눅스에서 /mnt/d/Backup 으로 바꾼다."""
    p = (p or "").strip().strip('"')
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if m and os.name != "nt":
        return "/mnt/%s/%s" % (m.group(1).lower(), m.group(2).replace("\\", "/"))
    return p


def check_target(target, src_root, log=print):
    if not os.path.isdir(target):
        raise BackupError(f"대상 폴더가 없습니다(드라이브 미연결?): {target}")
    t, s = os.path.realpath(target), os.path.realpath(src_root)
    if t == s or t.startswith(s + os.sep) or s.startswith(t + os.sep):
        raise BackupError("백업 대상이 raw 폴더와 겹칩니다.")
    try:
        if os.stat(t).st_dev == os.stat(s).st_dev:
            log("⚠️  대상이 원본과 같은 디스크입니다 — 디스크 장애는 막지 못합니다 (경고만).")
    except OSError:
        pass


def copy_verified(src, dst):
    """src → dst.part (해시 계산하며 복사) → 재읽기 검증 → 원자 교체. 원본이 복사 중 바뀌면 None."""
    st0 = os.stat(src)
    h = hashlib.sha256()
    part = dst + ".part"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, "rb") as fi, open(part, "wb") as fo:
        for b in iter(lambda: fi.read(CHUNK), b""):
            h.update(b)
            fo.write(b)
        fo.flush()
        os.fsync(fo.fileno())
    st1 = os.stat(src)
    if (st0.st_size, st0.st_mtime_ns) != (st1.st_size, st1.st_mtime_ns):
        os.remove(part)
        return None
    digest = h.hexdigest()
    if sha256_of(part) != digest:
        os.remove(part)
        raise IOError(f"복사본 검증 실패(대상 드라이브 이상?): {dst}")
    os.replace(part, dst)
    return digest, st1


def backup_one(target, src_root, keep, dry_run, log=print, progress=None, cancel=None):
    check_target(target, src_root, log)
    man = load_manifest(target)
    files = man.setdefault("files", {})
    rep = {"copied": [], "updated": [], "unchanged": 0, "conflicts": [], "skipped": [], "bytes": 0}
    need = 0
    todo = []
    for rel, full in iter_source(src_root):
        st = os.stat(full)
        e = files.get(rel)
        if e and e["size"] == st.st_size and e["mtime_ns"] == st.st_mtime_ns:
            rep["unchanged"] += 1
            continue
        todo.append((rel, full, st, e))
        need += st.st_size
    free = shutil.disk_usage(target).free
    if need * 1.05 > free and not dry_run:
        raise BackupError(f"대상 여유 공간 부족: 필요 {need/1e9:.2f}GB, 여유 {free/1e9:.2f}GB")

    done_bytes = 0
    for rel, full, st, e in todo:
        if cancel and cancel():
            log("⏹️ 사용자 중지 — 지금까지 복사분은 유지됩니다.")
            break
        if progress:
            progress(done_bytes, need, rel)
        done_bytes += st.st_size
        dst = os.path.join(target, "raw", rel)
        sealed = is_sealed(rel)
        if dry_run:
            log(f"[dry] {'SEALED ' if sealed else ''}{rel} ({st.st_size/1e6:.1f}MB)")
            rep["copied" if not e else "updated"].append(rel)
            continue
        if sealed and os.path.exists(dst):
            if sha256_of(dst) == sha256_of(full):          # 내용 같음 — mtime 만 다른 경우
                files[rel] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha256_of(dst), "backed_at": e["backed_at"] if e else time.strftime("%Y-%m-%dT%H:%M:%S")}
                rep["unchanged"] += 1
                continue
            rep["conflicts"].append(rel)
            log(f"❌ CONFLICT(봉인본이 백업본과 다름, 덮어쓰지 않음): {rel}")
            continue
        if os.path.exists(dst):                             # 가변 파일 — 이전 판 보관
            vdir = os.path.join(target, "_versions", rel)
            os.makedirs(vdir, exist_ok=True)
            shutil.copy2(dst, os.path.join(vdir, time.strftime("%Y%m%d_%H%M%S")))
            old = sorted(os.listdir(vdir))
            for v in old[:-keep] if keep > 0 else []:
                os.remove(os.path.join(vdir, v))
        res = copy_verified(full, dst)
        if res is None:
            rep["skipped"].append(rel)
            log(f"⏭️  복사 중 원본이 바뀜 — 다음 실행에 재시도: {rel}")
            continue
        digest, st1 = res
        if sealed:
            os.chmod(dst, 0o444)
        files[rel] = {"size": st1.st_size, "mtime_ns": st1.st_mtime_ns, "sha256": digest, "backed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        rep["updated" if e else "copied"].append(rel)
        rep["bytes"] += st1.st_size
        log(f"✅ {rel} ({st1.st_size/1e6:.1f}MB)")
        save_manifest(target, man)                          # 파일마다 저장 — 중간에 USB 가 빠져도 이어서 진행
    if not dry_run:
        man["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_manifest(target, man)
    return rep


def verify_target(target, log=print, progress=None, cancel=None):
    check_target(target, RAW_ROOT, log)
    man = load_manifest(target)
    bad, n = [], 0
    items = list(man.get("files", {}).items())
    for i, (rel, e) in enumerate(items):
        if cancel and cancel():
            log("⏹️ 사용자 중지")
            break
        if progress:
            progress(i, len(items), rel)
        p = os.path.join(target, "raw", rel)
        n += 1
        if not os.path.exists(p):
            bad.append((rel, "MISSING"))
        elif os.path.getsize(p) != e["size"] or sha256_of(p) != e["sha256"]:
            bad.append((rel, "CORRUPT"))
    log(f"검증 {n}개 파일, 이상 {len(bad)}개")
    for rel, why in bad:
        log(f"  ❌ {why}: {rel}")
    man["last_verify"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    man["last_verify_bad"] = len(bad)
    save_manifest(target, man)
    return bad


def target_status(target, src_root=None):
    """UI 표시용 — 연결 여부·여유 공간·마지막 실행·미백업 파일 수(stat 만 비교, 빠름)"""
    src_root = src_root or RAW_ROOT
    st = {"path": target, "connected": os.path.isdir(target), "error": None}
    if not st["connected"]:
        st["error"] = "폴더 없음(드라이브 미연결?)"
        return st
    try:
        check_target(target, src_root, lambda m: None)
    except BackupError as e:
        st["error"] = str(e)
        return st
    man = load_manifest(target)
    files = man.get("files", {})
    pend = pend_bytes = 0
    for rel, full in iter_source(src_root):
        s_ = os.stat(full)
        e = files.get(rel)
        if not (e and e["size"] == s_.st_size and e["mtime_ns"] == s_.st_mtime_ns):
            pend += 1
            pend_bytes += s_.st_size
    du = shutil.disk_usage(target)
    st.update({"free_bytes": du.free, "total_bytes": du.total, "backed_files": len(files),
               "pending_files": pend, "pending_bytes": pend_bytes,
               "last_run": man.get("last_run"), "last_verify": man.get("last_verify"),
               "last_verify_bad": man.get("last_verify_bad")})
    return st


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set-target", metavar="PATH", help="백업 대상 폴더를 설정에 추가(이미 있는 폴더여야 함)")
    ap.add_argument("--target", metavar="PATH", help="설정 대신 이 폴더로 1회 실행")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="백업본 해시 전수 재검증")
    ap.add_argument("--source", default=RAW_ROOT, help=argparse.SUPPRESS)
    a = ap.parse_args()
    try:
        return _cli(a)
    except BackupError as e:
        print(f"❌ {e}")
        return 2


def _cli(a):

    cfg = load_config()
    if a.set_target:
        p = os.path.abspath(normalize_path(a.set_target))
        check_target(p, a.source)
        if p not in cfg["targets"]:
            cfg["targets"].append(p)
        save_config(cfg)
        print(f"✅ 대상 등록: {p}  (전체 대상: {cfg['targets']})")
        return 0
    targets = [a.target] if a.target else cfg.get("targets", [])
    if not targets:
        print("대상이 없습니다. 먼저 --set-target PATH 로 지정하세요.")
        return 2
    rc = 0
    for t in targets:
        print(f"\n=== 백업 대상: {t} ===")
        if a.verify:
            rc |= 1 if verify_target(t) else 0
            continue
        rep = backup_one(t, a.source, int(cfg.get("keep_versions", 3)), a.dry_run)
        print(f"신규 {len(rep['copied'])} · 갱신 {len(rep['updated'])} · 변화없음 {rep['unchanged']} · 충돌 {len(rep['conflicts'])} · 건너뜀 {len(rep['skipped'])} · {rep['bytes']/1e6:.1f}MB")
        if rep["conflicts"]:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
