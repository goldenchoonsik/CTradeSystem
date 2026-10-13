"""AutoLab 실행 감시 → 텔레그램 보고 (IDE 와 무관하게 setsid 로 실행). 사용: autolab_reporter.py <run_dir> <max_trials>"""
import json, os, re, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tg import send

run_dir, max_trials = sys.argv[1], int(sys.argv[2])
log = os.path.join(run_dir, "train_run.log")
tag = os.path.basename(os.path.dirname(os.path.dirname(run_dir.rstrip('/')))) + "/" + os.path.basename(os.path.dirname(run_dir.rstrip('/')))
pat = re.compile(r"WFA 견고성\] (.*?) →(.*)")
sent = 0
send(f"🔁 [{tag}] AutoLab 재시작 감시 시작 {time.strftime('%H:%M')} (트라이얼 {max_trials}개, 약 13분/개)")


def status():
    try:
        with urllib.request.urlopen("http://localhost:8000/api/auto-research/status", timeout=5) as r:
            return json.load(r)
    except Exception:
        return None


dead = 0
while True:
    lines = open(log, encoding="utf-8", errors="ignore").read().splitlines() if os.path.exists(log) else []
    hits = [l for l in lines if "WFA 견고성]" in l]
    for i, l in enumerate(hits[sent:], start=sent + 1):
        m = pat.search(l)
        ok = "✅" if "✅" in l else "⛔"
        send(f"{ok} Trial {i}/{max_trials}: {m.group(1) if m else l[-80:]}")
    sent = len(hits)
    st = status()
    dead = dead + 1 if st is None else 0
    if dead >= 5:
        send("⚠️ 대시보드 서버 응답 없음 5분 — AutoLab 중단된 것으로 보입니다."); break
    lb = os.path.join(run_dir, "leaderboard.json")
    if os.path.exists(lb) or (st and not st.get("running") and st.get("status") in ("done", "failed", "rejected", "error", "aborted")):
        time.sleep(20)
        msg = [f"🏁 [{tag}] AutoLab 완료 {time.strftime('%H:%M')}"]
        try:
            res = json.load(open(os.path.join(run_dir, "result.json")))
            msg.append(f"판정: {res.get('status')} | 최종 Test: {res.get('final_test', '-')}")
            L = json.load(open(lb))
            for c in L.get("leaderboard", [])[:3]:
                msg.append(f"Rank{c['rank']} T{c['trial']} {len(c['features'])}지표 | WFA {c.get('wfa_oos_total_pct', 0):+.1f}% | "
                           f"선택구간 {c.get('val_profit', 0):+.2f}%/{c.get('val_trades', 0)}건 | 최종 {c.get('final_profit', 0):+.2f}%/{c.get('final_trades', 0)}건 | {c.get('status')}")
            if not L.get("leaderboard"):
                msg.append("견고성 통과 후보 없음 → 최종 Test 미실시")
        except Exception as e:
            msg.append(f"(요약 읽기 실패: {e})")
        msg.append("상세 해석은 Claude 가 정리해 보고합니다.")
        send("\n".join(msg)); break
    time.sleep(60)
