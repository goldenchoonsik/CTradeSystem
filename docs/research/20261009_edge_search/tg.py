"""텔레그램 보고/수신 도우미. 사용: tg.py send <파일|-> | tg.py wait
wait: 지정 chat 에서 온 새 메시지 1건을 받을 때까지 롱폴링 후 출력·종료(오프셋은 상태 파일에 저장)."""
import json, os, sys, time, urllib.request, urllib.parse

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
CHAT = os.environ["TELEGRAM_CHAT_ID"].strip()
BASE = f"https://api.telegram.org/bot{TOKEN}"
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg_offset.json")


def call(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"{BASE}/{method}", data=data, timeout=70) as r:
        return json.load(r)


def send(text):
    for i in range(0, len(text), 3900):  # 텔레그램 4096자 제한
        call("sendMessage", chat_id=CHAT, text=text[i:i + 3900])


def wait():
    offset = json.load(open(STATE))["offset"] if os.path.exists(STATE) else None
    if offset is None:  # 처음: 지금까지 쌓인 것은 건너뜀
        res = call("getUpdates", timeout=0)["result"]
        offset = (res[-1]["update_id"] + 1) if res else 0
    while True:
        try:
            res = call("getUpdates", offset=offset, timeout=50)["result"]
        except Exception as e:
            print("poll error:", e, file=sys.stderr); time.sleep(5); continue
        for u in res:
            offset = u["update_id"] + 1
            json.dump({"offset": offset}, open(STATE, "w"))
            m = u.get("message") or u.get("edited_message") or {}
            if str(m.get("chat", {}).get("id")) == CHAT and m.get("text"):
                print(f"[텔레그램 답장 {time.strftime('%H:%M:%S')}] {m['text']}")
                return


if __name__ == "__main__":
    if sys.argv[1] == "send":
        src = sys.argv[2]
        send(sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read())
        print("sent")
    else:
        wait()
