"""제품 알림에 붙일 '판매처별 최저가' 만들기.
- 에누리 가격비교 페이지 1개(제품당)의 공개 HTML 안에 있는 판매처 목록(판매처 이름·판매가·배송비)을 우리 코드가 직접 읽는다. AI 호출 없음.
- 지키는 것: robots.txt 허용 확인 · 정직한 UA · 제품당 하루 1번 요청 · 막히면(403 등) 우회하지 않고 '못 가져옴'으로 둔다.
- 순위는 배송비를 더한 총액 기준이다. 가격은 참고용이며 정확한 금액은 판매처에서 확인해야 한다.
- 결과는 data/prices.json 에 저장되고, notify.py 가 알림 메시지에 붙인다.
"""
import os, re, json, time, datetime, urllib.request
import price_probe as pp
 
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "prices.json")
TOP = 5
LIMIT = 4_000_000      # 에누리 페이지는 바이트로 600KB 를 넘어서 넉넉하게 읽는다(잘리면 목록 뒤쪽이 빠진다)
 
# 제품 이름(config.json 과 같게) -> 에누리에서 찾을 조건. url 을 적으면 검색 없이 그 페이지를 쓴다.
# 4개 제품 모두 올영 상품명·용량 기준으로 채웠다(아토베리어 80ml · 에이시카 60ml · 테라크네 60ml · 리제덤 캡슐크림 50ml).
ITEMS = {
    "아토베리어365 크림": {"query": "에스트라 아토베리어365 크림 80ml", "must": ["아토베리어365", "크림", "80ml"], "url": ""},
    "에이시카365": {"query": "에스트라 에이시카365 리페어 크림 60ml", "must": ["에이시카365", "리페어", "크림", "60ml"], "url": ""},
    "테라크네365": {"query": "에스트라 테라크네365 수딩 액티브 모이스처라이저 60ml", "must": ["테라크네365", "모이스처라이저", "60ml"], "url": ""},
    "리제덤365(PDRN 캡슐크림)": {"query": "에스트라 리제덤365 밀도탄력 캡슐크림 50ml", "must": ["리제덤365", "캡슐크림", "50ml"], "url": ""},
}
BAD = ("기획", "세트", "증정", "듀오", "리필", "미니", "샘플", "+")
 
OFFER = re.compile(r'\\?"shopName\\?":\\?"([^"\\]+)\\?",\\?"deliveryText\\?":\\?"([^"\\]*)\\?",\\?"plno\\?":\\?"(\d+)\\?",\\?"price\\?":(\d+)')
 
def ship_cost(txt):
    if "무료" in txt: return 0
    m = re.search(r"([\d,]+)\s*원", txt)
    return int(m.group(1).replace(",", "")) if m else None
 
def parse_sellers(page):
    seen, out = set(), []
    for m in OFFER.finditer(page):
        shop, dtxt, plno, price = m.groups()
        key = (shop, plno, price)
        if key in seen: continue
        seen.add(key); price = int(price); sh = ship_cost(dtxt)
        after = re.sub(r'[\\\s]+', " ", page[m.end(): m.end() + 200].split("shopName")[0])   # 다음 판매 항목 앞에서 자른다 # 이 판매 항목 뒤에 붙은 원본 글자(카드·쿠폰 같은 조건이 있는지 확인용)
        out.append({"shop": shop, "price": price, "ship_text": dtxt or "-", "ship": sh, "total": price + (sh or 0), "ship_known": sh is not None,
                    "card": (re.search(r'cardNameText\s*"\s*:\s*"\s*([^"]*?)\s*"', after) or [None, ""])[1] if "cardNameText" in after else "", "after": after[:160]})
    return sorted(out, key=lambda x: (x["total"], x["price"]))
 
def get(url):
    if not pp.allowed(url): raise PermissionError("robots 금지")
    time.sleep(1)
    req = urllib.request.Request(url, headers={"User-Agent": pp.UA, "Accept": "text/html", "Accept-Language": "ko"})
    r = urllib.request.urlopen(req, timeout=20)
    raw = r.read(LIMIT)
    return raw.decode(r.headers.get_content_charset() or "utf-8", "ignore"), len(raw) >= LIMIT
 
def find_page(item):
    if item.get("url"): return item["url"]
    must = [m.replace(" ", "").lower() for m in item["must"]]
    for it in pp.search(item["query"] + " 에누리", 30):
        u, t = it["link"], pp.strip_tags(it["title"])
        tn = t.replace(" ", "").lower()
        packs = [int(x) for x in re.findall(r"(\d+)\s*개", t)] or [1]
        if "enuri.com/catalog" in u and all(m in tn for m in must) and max(packs) == 1 and not any(b in t for b in BAD): return u
    return None
 
def run_all():
    res = {"asof": datetime.date.today().isoformat(), "items": {}}
    for name, item in ITEMS.items():
        r = {"ok": False}
        try:
            url = find_page(item)
            if not url: r["error"] = "에누리에서 같은 제품·용량의 1개 상품 페이지를 못 찾음"
            else:
                page, cut = get(url); sellers = parse_sellers(page); price, how, title = pp.extract(page)
                r.update(url=url, title=title, page_cut=cut, n_sellers=len(sellers), sellers=sellers[:14])
                if not sellers: r["error"] = "판매처 목록을 못 읽음(페이지 구조가 바뀌었을 수 있음)"
                else:
                    low = min(s["price"] for s in sellers)
                    r["ok"] = True; r["check"] = "최저 판매가가 페이지가 밝힌 최저가와 일치" if price and abs(price - low) < 1 else "페이지가 밝힌 최저가와 다름(참고만)"
        except Exception as e: r["error"] = f"{type(e).__name__}: {e}"
        res["items"][name] = r
        print(name, "->", "OK" if r["ok"] else r.get("error"))
    os.makedirs(os.path.dirname(OUT), exist_ok=True); json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res
 
def load():
    try: return json.load(open(OUT, encoding="utf-8"))
    except Exception: return {"items": {}}
 
def lines_for(product, data=None, top=TOP):
    """알림 메시지에 붙일 줄들. 가져오지 못했으면 빈 목록(알림 자체는 그대로 나간다)."""
    r = (data or load()).get("items", {}).get(product)
    if not r or not r.get("ok"): return []
    out = [f"• 판매처 최저가 (에누리 기준 · 배송비 포함 순 · {r.get('title', '')[:30]}):"]
    seen, rank = set(), 0
    cards = [s for s in r["sellers"] if s.get("card")]
    for s in r["sellers"]:                       # 같은 판매처가 여러 항목이면 총액이 가장 낮은 것 하나만 보여 준다
        if s.get("card") or s["shop"] in seen: continue      # 특정 카드로 결제해야 되는 가격은 순위에서 빼고 아래에 따로 보여 준다
        seen.add(s["shop"]); rank += 1
        if rank > top: break
        sh = "무료배송" if s["ship"] == 0 else (f"배송비 {s['ship']:,}원" if s["ship"] else "배송비 확인 필요")
        out.append(f"   {rank}) {s['shop']} {s['price']:,}원 ({sh}" + (f" → 총 {s['total']:,}원)" if s["ship"] else ")") + "")
    if cards:
        c = min(cards, key=lambda x: x["total"]); sh = "무료배송" if c["ship"] == 0 else f"배송비 {c['ship']:,}원"
        out.append(f"   ※ {c['card']} 결제 시 {c['shop']} {c['price']:,}원 ({sh}) — 카드 조건이라 순위에는 넣지 않음")
    out.append(f"   출처·상세: {r['url']} (참고용 · 카드·쿠폰 조건에 따라 다를 수 있음)")
    return out
 
if __name__ == "__main__":
    res = run_all(); txt = ["## 판매처 최저가 시험 (알림에 붙을 모양)", ""]
    for name, r in res["items"].items():
        txt += [f"### {name}", ""] + (lines_for(name, res) if r["ok"] else [f"못 가져옴: {r.get('error')}"]) + [""]
        if r["ok"]: txt += ["항목 뒤 원본 글자(조건 확인용):"] + [f"- {s['shop']} {s['price']:,}원: `{s['after']}`" for s in r["sellers"][:6]] + [""]
        if r["ok"]: txt += [f"(읽은 항목 {r['n_sellers']}개 · 서로 다른 판매처 {len({s['shop'] for s in r['sellers']})}곳 · 페이지 잘림 {r['page_cut']} · {r['check']})", ""]
    t = "\n".join(txt); print(t)
    if os.environ.get("GITHUB_STEP_SUMMARY"): open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8").write(t + "\n")
