import json, os, sys, re, glob, html
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics_all as M
D = os.path.dirname(os.path.abspath(__file__)); CAP = 9000
idx = json.load(open(M.ROOT + "/reference/index.json")); docs = []
for t in ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"]:
    r = [x for x in idx if x["ticker"] == t and x["form"] == "10-K"]; r.sort(key=lambda x: x["filed"]); r = r[-1]
    raw = open(f"{M.ROOT}/raw/{t}/{r['file']}", "rb").read().decode("utf-8", "ignore"); rl, txt = M.text_of(f"{M.ROOT}/raw/{t}/{r['file']}"); s = M.sections(txt)
    body = s.get("7", ""); snip_raw = ""; snip_txt = ""
    for off in range(800, 4000, 400):               # 변환문 일부가 원본에 연속으로 남아 있는 지점을 찾아 원본 조각을 보여줌
        key = re.sub(r"\s+", " ", body[off:off + 28]).strip()
        j = raw.find(key[:24]) if len(key) >= 24 else -1
        if j > 0: snip_raw = raw[max(0, j - 500): j + 700]; snip_txt = body[max(0, off - 120): off + 520]; break
    docs.append(dict(t=t, form=r["form"], filed=r["filed"], raw_kb=rl // 1000, txt_kb=len(txt) // 1000, secs={k: len(v) // 1000 for k, v in s.items() if k in ("1", "1A", "7", "7A", "8")},
        s={k: s.get(k, "")[:CAP] + ("\n\n… (이하 생략, 전체는 저장 파일에서) …" if len(s.get(k, "")) > CAP else "") for k in ("1A", "7")}, snip_raw=snip_raw, snip_txt=snip_txt, path=f"DataVault/staging/us_filings_sec/text/{t}/{r['file']}.txt"))
data = json.dumps(docs, ensure_ascii=False).replace("</", "<\\/")
page = '''<title>SEC 공시 변환 확인</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
:root{--bg:#f6f7f9;--fg:#1c2330;--mut:#5d6779;--line:#d9dee7;--card:#fff;--acc:#1f5fbf;--chip:#eaf0fa;--code:#f0f2f6;--font:"IBM Plex Sans KR","Noto Sans KR",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#12161d;--fg:#e4e8ef;--mut:#9aa5b8;--line:#2a3140;--card:#181d27;--acc:#7aa7ee;--chip:#1e2a40;--code:#1d2330;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#12161d;--fg:#e4e8ef;--mut:#9aa5b8;--line:#2a3140;--card:#181d27;--acc:#7aa7ee;--chip:#1e2a40;--code:#1d2330;color-scheme:dark}
body{background:var(--bg);color:var(--fg);font-family:var(--font);line-height:1.6;padding-inline:max(16px,env(safe-area-inset-left,0px)) max(16px,env(safe-area-inset-right,0px));padding-block:24px 48px}
main{max-width:900px;margin:0 auto;display:flex;flex-direction:column;gap:20px}
h1{font-size:1.5rem;margin:0 0 4px;text-wrap:balance} h2{font-size:1rem;margin:0 0 8px} p{margin:0 0 8px;max-width:70ch}.mut{color:var(--mut);font-size:.88rem}
.bar{display:flex;flex-wrap:wrap;gap:6px}
button{font:inherit;font-size:.88rem;padding:5px 12px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:4px;cursor:pointer}
button[aria-pressed="true"]{background:var(--acc);border-color:var(--acc);color:var(--bg);font-weight:600}
button:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.stats{display:flex;flex-wrap:wrap;gap:6px 18px;font:.82rem var(--mono);color:var(--mut)} .stats b{color:var(--fg)}
.cmp{display:grid;grid-template-columns:1fr 1fr;gap:12px} @media (max-width:640px){.cmp{grid-template-columns:1fr}}
.cmp>div{min-width:0}
pre{margin:0;background:var(--code);border:1px solid var(--line);border-radius:6px;padding:12px;font:.8rem/1.55 var(--mono);white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto}
pre.long{max-height:560px;font-family:var(--font);font-size:.88rem}
</style>
<main>
<header><h1>SEC 공시 변환 확인</h1><p class=mut>원본 HTML을 글로 바꾼 결과입니다. 종목별 최신 10-K를 골라 비교하고, 301건 전체는 파일로 저장했습니다.</p></header>
<div class=bar id=tk role=group aria-label="종목"></div>
<div class=stats id=st></div>
<section><h2>같은 위치의 원본 → 변환</h2><div class=cmp><div><p class=mut>원본 HTML(태그·스타일·숨은 XBRL 포함)</p><pre id=raw></pre></div><div><p class=mut>변환 후(글만)</p><pre id=txt></pre></div></div></section>
<section><div class=bar id=sec role=group aria-label="섹션"></div><h2 id=sh style="margin-top:12px"></h2><pre class=long id=body></pre></section>
<p class=mut id=path></p>
</main>
<script>
const DOCS=__DATA__;let cur=DOCS[0],sec="7";
const $=i=>document.getElementById(i),NAME={"1A":"Item 1A 위험요인","7":"Item 7 경영진 논의(MD&A)"};
function btns(el,items,sel,fn){el.innerHTML="";items.forEach(([k,l])=>{const b=document.createElement("button");b.textContent=l;b.setAttribute("aria-pressed",k===sel);b.onclick=()=>fn(k);el.appendChild(b)})}
function draw(){btns($("tk"),DOCS.map(d=>[d.t,d.t]),cur.t,t=>{cur=DOCS.find(d=>d.t===t);draw()});
btns($("sec"),Object.keys(NAME).map(k=>[k,NAME[k]]),sec,k=>{sec=k;draw()});
const d=cur;$("st").innerHTML=`<span>${d.form} 접수 <b>${d.filed}</b></span><span>원본 <b>${d.raw_kb.toLocaleString()}KB</b> → 글 <b>${d.txt_kb.toLocaleString()}KB</b> (${Math.round(d.txt_kb/d.raw_kb*100)}%)</span>`+Object.entries(d.secs).map(([k,v])=>`<span>Item ${k} <b>${v}KB</b></span>`).join("");
$("raw").textContent=d.snip_raw||"(이 문서는 원본에서 같은 위치를 자동으로 찾지 못했습니다)";$("txt").textContent=d.snip_txt||"";
$("sh").textContent=NAME[sec]+" · 앞 9,000자";$("body").textContent=d.s[sec];$("path").textContent="전체 변환 파일: "+d.path}
draw();
</script>
'''.replace("__DATA__", data)
open(f"{D}/text_view.html", "w").write(page); print(len(page) // 1000, "KB")
