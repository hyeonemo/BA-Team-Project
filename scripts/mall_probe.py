"""[시험용] 정해 둔 판매몰별로 같은 제품의 판매 페이지를 찾고, 가격을 읽어 최저가 순위를 만든다.
- 다루는 몰만 아래 MALLS 에 있다. 제외: 화해·글로우픽(정보 사이트, 판매 아님), 무신사·SSF·신세계V(robots 금지), 티몬·위메프(서비스 종료).
- 순서: 네이버 웹문서에서 '제품명 + 몰 이름'으로 검색 → 그 몰 주소만 남김 → 우리 코드가 읽어 보고, 막히면 Gemini가 읽음.
- 같은 제품 판단: 상품명에 MUST 단어가 모두 있고, 용량이 VOL 이고, 세트·기획·증정 표시가 없는 것만.
- Gemini가 읽은 가격은 참고용이다(원문 일치 검사만 함).
"""
import os, re, time, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor
import price_probe as pp
import gemini_probe as gp

MALLS = {  # 표시 이름: 주소에 들어 있어야 하는 말
    "올리브영": "oliveyoung.co.kr", "쿠팡": "coupang.com", "네이버 스마트스토어": "smartstore.naver.com",
    "네이버 브랜드스토어": "brand.naver.com", "11번가": "11st.co.kr", "G마켓": "gmarket.co.kr", "옥션": "auction.co.kr",
    "SSG닷컴": "ssg.com", "롯데온": "lotteon.com", "마켓컬리": "kurly.com", "다나와": "danawa.com", "에누리": "enuri.com",
    "카카오 선물하기": "gift.kakao.com", "공식몰": "aestura.com",
}
PRODUCT = os.environ.get("PROBE_QUERY", "에스트라 에이시카365 리페어 크림 60ml").strip()
MUST = [w for w in os.environ.get("PROBE_MUST", "리페어,크림").split(",") if w]
VOL = os.environ.get("PROBE_VOL", "60").strip()
PER_MALL = int(os.environ.get("PROBE_PER_MALL", "5"))
BAD = ("세트", "기획", "증정", "듀오", "트리플", "+", "리필", "미니", "샘플")

def collect():
    found = {}
    for mall, dom in MALLS.items():
        items = []
        for q in (f"{PRODUCT} {mall}", f"{PRODUCT} {dom}"):
            try: items += pp.search(q, 30)
            except Exception: pass
            time.sleep(0.3)
        seen, keep = set(), []
        for it in items:
            u = it["link"]
            if dom in urllib.parse.urlparse(u).netloc + urllib.parse.urlparse(u).path and u not in seen:
                seen.add(u); keep.append(it)
        # 제목에 꼭 있어야 하는 말이 들어 있는 페이지를 먼저 읽는다(다른 제품 페이지에 호출을 낭비하지 않기 위해)
        keep.sort(key=lambda it: not all(w in pp.strip_tags(it['title']).replace(' ', '') for w in MUST))
        found[mall] = keep[:PER_MALL]
    return found

def read(mall, it):
    url = it["link"]; o, op = gp.ours(url); title = pp.strip_tags(it["title"])[:40]
    row = {"mall": mall, "title": title, "ours": o, "url": url, "name": "", "vol": "", "price": None, "check": "-", "src": "-"}
    if op: row.update(price=op, src="우리 코드", check="페이지 직접 읽음")
    try:
        d, st = gp.gemini(url)
        row["name"] = str(d.get("product_name") or ""); row["vol"] = str(d.get("volume") or "")
        price = d.get("price_krw"); quote = d.get("price_quote") or ""
        if price:
            ok = str(int(float(price))) in re.sub(r"[,\s]", "", quote)
            if not row["price"]: row.update(price=float(price), src="Gemini", check="원문 일치" if ok else "원문 불일치⚠")
            elif abs(float(price) - row["price"]) > 1: row["check"] = "우리 코드와 Gemini 가격 다름⚠"
    except Exception as e: row["check"] = f"Gemini 실패({type(e).__name__})"
    return row

def same(r):
    n = (r["name"] or r["title"]).replace(" ", "")
    return all(w in n for w in MUST) and VOL in (r["vol"] + r["title"] + r["name"]).replace(" ", "") and not any(b in (r["name"] or r["title"]) for b in BAD)

def main():
    found = collect(); jobs = [(m, it) for m, its in found.items() for it in its]
    with ThreadPoolExecutor(3) as ex: rows = list(ex.map(lambda a: read(*a), jobs))
    out = [f"## 판매몰별 가격 시험: {PRODUCT}", "", "### 1. 몰별 검색 노출", "", "| 판매몰 | 검색에 나온 페이지 수 |", "|---|---|"]
    out += [f"| {m} | {len(its)} |" for m, its in found.items()]
    out += ["", "### 2. 읽은 결과", "", "| 판매몰 | 상품명(페이지) | 용량 | 가격 | 출처 | 검증 | 같은 제품 | 우리 접속 |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['mall']} | {(r['name'] or r['title'])[:34]} | {r['vol']} | {'%d원' % r['price'] if r['price'] else '-'} | {r['src']} | {r['check']} | {'✅' if same(r) and r['price'] else '-'} | {r['ours']} |")
    ok = sorted([r for r in rows if same(r) and r["price"] and "⚠" not in r["check"] and "실패" not in r["check"]], key=lambda r: r["price"])
    best = {}
    for r in ok: best.setdefault(r["mall"], r)
    out += ["", f"### 3. 같은 제품·용량({VOL}) 최저가 순위 (몰별 1개)", "", "| 순위 | 판매몰 | 가격 | 링크 |", "|---|---|---|---|"]
    out += [f"| {i} | {r['mall']} | {r['price']:,.0f}원 | {r['url']} |" for i, r in enumerate(sorted(best.values(), key=lambda r: r["price"]), 1)]
    out += ["", f"**가격이 확인된 몰: {len(best)}곳 / 시험한 몰 {len(MALLS)}곳 (최종 모델 `{gp.MODEL}`)**"]
    text = "\n".join(out); print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"): open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8").write(text + "\n")

if __name__ == "__main__": main()
