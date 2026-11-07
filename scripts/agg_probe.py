"""[시험용] 다나와·에누리 상품 페이지에 판매처별 가격 목록이 들어 있는지, 우리 코드가 직접(Gemini 없이) 읽을 수 있는지 확인한다.
- 접속은 robots.txt 허용·정직한 UA·1회 요청이다(price_probe 의 allowed/fetch 사용). 막히면 우회하지 않는다.
- 페이지 글자에서 판매처 이름 근처의 가격 숫자를 찾아 보여 준다. 목록이 자바스크립트로 나중에 채워지는 페이지면 여기서는 안 보인다.
"""
import os, re, sys
import price_probe as pp

URLS = [u for u in os.environ.get("PROBE_URLS", "").split() if u] or [
    "https://prod.danawa.com/info/?pcode=94975556",
    "https://price.enuri.com/catalog/136695232",
]
MALLS = ["쿠팡", "11번가", "G마켓", "지마켓", "옥션", "SSG", "롯데온", "올리브영", "네이버", "스마트스토어", "인터파크", "위메프", "티몬", "컬리", "카카오", "아모레", "공식", "화해", "GS샵", "CJ온스타일", "현대Hmall", "이마트", "홈플러스", "하이마트"]

def main():
    out = ["## 가격비교 페이지 직접 읽기 시험", ""]
    for u in URLS:
        out += [f"### {u}", ""]
        if not pp.allowed(u): out += ["robots.txt 금지 → 읽지 않음", ""]; continue
        try: page = pp.fetch(u)
        except Exception as e: out += [f"접속 실패: {type(e).__name__} {e}", ""]; continue
        text = re.sub(r"\s+", " ", pp.strip_tags(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)))
        price, how, title = pp.extract(page)
        out += [f"- 페이지 크기: {len(page):,}자 / 보이는 글자: {len(text):,}자", f"- 페이지가 밝힌 가격: {f'{price:,.0f}원 ({how})' if price else '없음'}", f"- 제목: {title[:60]}"]
        cnt = {m: text.count(m) for m in MALLS if text.count(m)}
        out += [f"- 글자 안에 나온 판매처 이름 횟수: {cnt if cnt else '없음 (목록이 나중에 채워지는 페이지일 가능성)'}", ""]
        shown = 0; rows = []
        for m in MALLS:
            for mt in re.finditer(re.escape(m), text):
                seg = text[max(0, mt.start() - 20): mt.end() + 60]
                if re.search(r"\d{1,3}(?:,\d{3})+\s*원?", seg): rows.append(seg); break
        out += ["| 판매처 이름 근처 글자(앞 20자~뒤 60자) |", "|---|"] + [f"| {r} |" for r in rows[:15]]
        if not rows: out.append("| (판매처 이름 근처에서 가격을 못 찾음) |")
        out += [""]
    text = "\n".join(out); print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"): open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8").write(text + "\n")

if __name__ == "__main__": main()
