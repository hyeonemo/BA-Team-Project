# 에스트라 크림 검색 신호 알림 (MVP)

매일 아침 네이버 데이터랩 검색량으로 제품 총점수를 계산해 슬랙으로 알리고, `docs/index.html`(공개 페이지)을 갱신한다.

## 구조
- `config.json` : 키워드 풀, 제품별 연결 키워드·연결 강도, 기준(1.0배), 사건 묶음(14일)
- `scripts/collect.py` : 데이터랩 일 단위 검색량 수집 -> `data/search_daily.csv`
- `scripts/compute.py` : z, 시장 공통 제거, 총점수, 1.0배 판정 -> `data/latest.json`, `data/alert_log.csv`
- `scripts/report.py` : 독립 실행 HTML 생성 -> `docs/index.html`
- `scripts/notify.py` : 슬랙 메시지 만들기·발송(중복 방지 `data/sent.json`), `--dry` 는 화면 출력만, `--weekly` 는 주간 브리핑 포함
- `.github/workflows/daily.yml` : 매일 07:30 KST 자동 실행

## 깃허브에서 한 번만 할 설정
1. Settings > Secrets and variables > Actions > **Secrets**: `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `SLACK_WEBHOOK_URL`
2. Settings > Pages: Branch `main`, Folder `/docs`
3. (선택) Variables 에 `PAGE_URL` = 공개 페이지 주소 -> 슬랙 메시지에 링크가 붙는다
4. Actions 탭에서 `daily-alert` 를 수동 실행(Run workflow)해서 한 번 시험

## 로컬 시험
```
export NAVER_CLIENT_ID=... NAVER_CLIENT_SECRET=...
python scripts/collect.py && python scripts/compute.py && (cd scripts && python report.py) && python scripts/notify.py --dry
```
**키와 Webhook 주소는 절대 파일에 적거나 커밋하지 않는다.**
