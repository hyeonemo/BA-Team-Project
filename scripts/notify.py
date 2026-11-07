"""latest.json -> 슬랙 메시지 문구 만들기 + Webhook 발송(+중복 방지). 환경변수 SLACK_WEBHOOK_URL 이 없으면 화면에만 출력(드라이런)."""
import os, json, sys, urllib.request
import pandas as pd
 
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
SENT = os.path.join(ROOT, "data", "sent.json")
PAGE_URL = os.environ.get("PAGE_URL", "")
 
def _md(s): return f"{int(s[5:7])}/{int(s[8:10])}"
 
def progress_text(res, p, r, ev, refline):
    """급등 첫날 이후: 급등 확대 / 지속 / 완화 / 종료로 구분하고, 어제·정점과 비교해 보여 준다."""
    thr, now, prev, peak = res["threshold"], r["ratio"], ev.get("prev"), ev["peak"]
    tag = {"확대": "급등 확대", "지속": "급등 지속", "완화": "급등 완화", "종료": "급등 종료"}[ev["phase"]]
    head = f"*[{tag}] {p}* · {_md(ev['start'])} 시작 {ev['day_n']}일째 · 기준일 {res['asof']}"
    chg = f"어제 {prev:.1f}배 → 오늘 {now:.1f}배" if prev is not None else f"오늘 {now:.1f}배"
    pct = f"정점({_md(ev['peak_day'])}) {peak:.1f}배의 {now / peak * 100:.0f}%" if peak else ""
    if ev["phase"] == "확대": l1, l2 = f"• 평소 폭의 *{now:.1f}배*로 정점을 새로 찍었다 ({chg})", "• 판단: 아직 오르는 중 — 지켜볼 구간"
    elif ev["phase"] == "지속": l1, l2 = f"• 평소 폭의 *{now:.1f}배* ({chg})", f"• 판단: 정점 부근에서 높은 수준이 유지되는 중 ({pct})"
    elif ev["phase"] == "완화": l1, l2 = f"• 평소 폭의 *{now:.1f}배*로 내려오는 중 ({chg})", f"• 판단: 정점에서 꺾였지만 기준({thr:.1f}배)보다는 높다 ({pct})"
    else: l1, l2 = f"• 기준({thr:.1f}배) 아래로 내려왔다 ({chg})", f"• 이번 급등: 정점 {peak:.1f}배({_md(ev['peak_day'])}), 시작부터 {ev['day_n']}일"
    lines = [head, l1, l2, f"• 참고: {refline} · 시장 공통 변화 {res['market_common_z']:+.1f}"]
    if PAGE_URL: lines.append(f"• 자세히: {PAGE_URL}")
    return "\n".join(lines)
 
def daily_messages(res):
    msgs = []
    ref = res.get("reference", {})
    refline = " · ".join(f"{k} {v:+.1f}배" for k, v in ref.items())
    for p, r in res["products"].items():
        st = r["status"]; ev = r.get("event") or {}; ph = ev.get("phase", "시작")
        if st in ("알림 없음", "데이터 없음") or ph == "둔화": continue   # 둔화(기준 아래에서 계속 낮음)는 주간 브리핑에서만 다룬다
        if ph != "시작":
            msgs.append((f"{res['asof']}|{p}|{ph}", progress_text(res, p, r, ev, refline))); continue
        label = "새 알림"
        head = f"*[{label}] {p}* · 기준일 {res['asof']}"
        if r["mode"] == "score":
            lines = [head, f"• 총점수 {r['score']:+.2f} → 평소 폭의 *{r['ratio']:.1f}배* (기준 {res['threshold']:.1f}배)"]
            parts = sorted(r["contrib"].items(), key=lambda kv: -kv[1]["part"])
            lines.append("• 연결 키워드 기여: " + ", ".join(f"{k} {v['part']:+.2f}(오른 정도 {v['z_adj']:+.1f}×연결 {v['weight']:.2f})" for k, v in parts))
            lines.append(f"• 제품 자체 검색: 평소 폭의 {r['own_z']:+.1f}배 (점수에는 넣지 않고 참고만 표시)")
        else:
            lines = [head, f"• 제품 자체 검색이 평소 폭의 *{r['ratio']:.1f}배* 올랐다 (기준 {res['threshold']:.1f}배)",
                     "• 연결 키워드 없음: 성분·카테고리 연결이 우연 수준이라 제품 자체 검색만 감지한다"]
        lines.append(f"• 참고: {refline} · 시장 공통 변화 {res['market_common_z']:+.1f}")
        if PAGE_URL: lines.append(f"• 자세히: {PAGE_URL}")
        msgs.append((f"{res['asof']}|{p}|{st}", "\n".join(lines)))
    if not msgs:
        msgs.append((f"{res['asof']}|none", f"*[오늘의 알림 없음]* 기준일 {res['asof']} · 모든 제품이 기준({res['threshold']:.1f}배) 미만입니다. 참고: {refline}"))
    return msgs
 
def weekly_message(res):
    log = pd.read_csv(os.path.join(ROOT, "data", "alert_log.csv"), parse_dates=["date"])
    end = pd.Timestamp(res["asof"]); wk = log[(log.date > end - pd.Timedelta(days=7)) & (log.date <= end)]
    lines = [f"*[주간 브리핑] {(end - pd.Timedelta(days=6)).date()} ~ {res['asof']}*"]
    for p, r in res["products"].items():
        n = int((wk["product"] == p).sum()); tag = "총점수" if r["mode"] == "score" else "제품 자체 검색"
        lines.append(f"• {p}: 지난 7일 새 알림 {n}건 · 현재 {tag} 평소 폭의 {r['ratio']:+.1f}배 ({r['status']})")
    if PAGE_URL: lines.append(f"• 7일 추이 그래프: {PAGE_URL}")
    return (f"{res['asof']}|weekly", "\n".join(lines))
 
def post(url, text):
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=20).status
 
def main(dry=False):
    res = json.load(open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8"))
    sent = json.load(open(SENT)) if os.path.exists(SENT) else []
    url = (os.environ.get("SLACK_WEBHOOK_URL") or "").strip()
    todo = daily_messages(res)
    if "--weekly" in sys.argv: todo.append(weekly_message(res))
    for key, text in todo:
        if key in sent: print("이미 보냄:", key); continue
        if url and not dry:
            print("발송:", key, post(url, text)); sent.append(key)
        else:
            print("--- 드라이런 ---\n" + text + "\n")
    if url and not dry: json.dump(sent, open(SENT, "w"), ensure_ascii=False)
 
if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
