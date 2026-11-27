# 에스트라 크림 검색 신호 알림 (MVP)

매일 아침 네이버 데이터랩 검색량으로 제품 총점수를 계산해 슬랙으로 알리고, `docs/index.html`(공개 페이지)을 갱신한다.

## 구조
- `config.json` : 키워드 풀, 제품별 연결 키워드·연결 강도, 기준(1.0배), 사건 종료 기준(연속 3일, event_end_days)
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


## 여러 기업이 쓰게 하기 (자동 온보딩)

기업이 브랜드·제품·경쟁사·슬랙 주소만 입력하면 다음 실행부터 알림이 가는 구조.

1. 입력 폼에서 받은 내용(JSON)을 `python scripts/tenant.py add 폼.json` 으로 넣으면 `tenants/<기업>/config.json` 이 만들어진다.
2. 다음 `daily-alert` 실행 때 `scripts/tenant.py run` 이 그 기업의 과거 검색 흐름을 받아 기준선을 만들고, 연결 강도(상관계수)를 계산하고, 슬랙에 "연결 완료" 메시지를 보낸다. 그날부터 매일 알림.
3. 기업마다 설정·데이터·보낸 알림 기록이 따로(`tenants/<기업>/`)라서 섞이지 않는다. 한 기업이 실패해도 다른 기업은 계속 돈다.
4. 네이버 API 키는 운영자 것 하나(`NAVER_CLIENT_ID/SECRET`)를 공유한다. 슬랙 주소는 저장소에 올리지 않고 `TENANT_WEBHOOKS`(JSON) 시크릿으로 받는다.
5. 알림 문구에서 에스트라 전용 가격 줄은 기업 실행에서 빠진다.

한계(시연용): 입력은 파일로 받고, 기업 수가 늘면 네이버 호출 한도·실행 시간이 병목이 된다. 실제 서비스는 DB와 비밀 저장소가 필요하다.

### 등록 웹 서버 (시연)

```
python scripts/server.py --demo     # 키 없이 저장소 데이터로 시연  → http://127.0.0.1:8000
python scripts/server.py            # 네이버 키(NAVER_CLIENT_ID/SECRET)가 있으면 실제 수집
```

- 폼 화면(`docs/onboard.html`)에서 [등록하기]를 누르면 서버가 입력을 검증하고(키워드 8개 이상, 슬랙 주소 형식, 길이 제한) 기업 폴더를 만든 뒤, 백그라운드로 과거 데이터 받기 → 연결 강도 계산 → 기준선·알림 계산 → 슬랙 발송까지 한 번에 돌린다. 진행 상태와 결과는 화면에 나온다.
- 슬랙 주소는 `tenants/.webhooks.json`(git 제외, 권한 600)에 저장된다. 주소를 비우면 알림은 화면에만 보인다.
- 로그인이 없어서 내 컴퓨터(127.0.0.1)에서만 쓴다. 인터넷에 열려면 인증, 요청 횟수 제한, 데이터베이스를 먼저 붙여야 한다.

### 시범 배포 (발표용, 클라우드에 올리기)

서버를 내 컴퓨터가 아니라 인터넷에 올려 두는 작은 시범 단계다. 저장소의 `Dockerfile` 로 컨테이너를 돌릴 수 있는 곳이면 어디든 된다.

1. 클라우드 서비스에 이 저장소를 연결하고 `Dockerfile` 로 배포한다.
2. 환경변수를 넣는다.
   - `ACCESS_CODE` : 접속 코드 (**필수**. 없으면 서버가 시작되지 않는다)
   - `SERVER_ARGS` : 시연이면 `--demo` (키 없이 저장소 데이터로 계산). 실수집이면 비우고 아래 키를 넣는다.
   - `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` : 실수집일 때
3. `/app/tenants` 에 영구 저장소(볼륨)를 연결한다. 연결하지 않으면 재시작할 때 등록한 기업이 사라진다.
4. 실수집 모드에서는 서버가 매일 07:30(한국 시간)에 모든 기업을 자동 실행한다 (`--daily-at HH:MM` 으로 바꿈, `--no-schedule` 로 끔). 화면의 [지금 자동 실행 해 보기]로 바로 돌려 볼 수 있다.

시범 수준의 보호: 접속 코드 하나, IP당 시간당 등록 5회, 입력 검증. **기업별 로그인, 데이터베이스, 슬랙 앱 연결, 네이버 호출 한도·약관 확인은 아직 없다.** 발표용으로만 쓰고, 실제 기업 데이터는 올리지 않는다.
