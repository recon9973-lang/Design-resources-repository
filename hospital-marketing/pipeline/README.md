# 0단계 데이터 파이프라인 (검증용)

HIRA 병원 마스터 + 네이버 지역 검색 API로 반경 내 경쟁 병원과
마케팅 경쟁력 점수(0-100)를 계산합니다. 표준 라이브러리만 사용합니다.

## 실행

```bash
cd hospital-marketing/pipeline

# 목업 모드 (API 키 불필요 — fixtures 데이터 사용)
python3 run_report.py

# 반경 변경 / 파일 저장
python3 run_report.py --radius 500 --out metrics.json

# 테스트
python3 -m unittest discover -s tests

# 0단계 실데이터 검증 (.env에 실키 필요)
python3 validate_stage0.py --name 라메스피부과의원 --lat 37.5079 --lng 127.0382
```

## 실데이터 모드

`.env.example`을 `.env`로 복사하고 키를 채우면 자동으로 실 API를 호출합니다.

| 키 | 발급처 |
| --- | --- |
| `DATA_GO_KR_SERVICE_KEY` | [공공데이터포털](https://www.data.go.kr) → 병원정보서비스 활용신청 |
| `NAVER_CLIENT_ID/SECRET` | [네이버 개발자센터](https://developers.naver.com) → 애플리케이션 등록 |
| `SGIS_CONSUMER_KEY/SECRET` | [SGIS 개발자센터](https://sgis.kostat.go.kr/developer/) → 인증키 신청 |
| `NAVER_AD_API_KEY` · `NAVER_AD_SECRET_KEY` · `NAVER_AD_CUSTOMER_ID` | [네이버 검색광고](https://searchad.naver.com) → 도구 > API 사용 관리 (절대 검색량·연관검색어) |

검색량·연관검색어는 오픈 API(비로그인)에 없고 **검색광고 키워드도구 API에서만** 제공된다.
위 3종 키를 채우면 리포트에 `월 검색량` 열과 `검색 수요·연관 검색어` 섹션이 자동 표시되고,
키가 없으면 두 요소는 렌더 단계에서 완전히 생략된다(기존 리포트와 동일). 광고 집행·과금 없이
키만 발급해도 조회 가능. 서명은 HMAC-SHA256(`{ts}.GET./keywordstool`).

2026-07-02 실키 검증 완료: HIRA 반경검색(거리 교차검증 0건 불일치), 네이버
노출 감지(노출/미노출 양방향 확인), SGIS 인구·상권 지표(강남구) 정상.

주의: 네이버 지역 검색 API는 결과가 최대 5건이라 "미노출"은 "공식 API 상위
5위 밖"을 의미한다. 고객 화면 문구도 이 기준으로 표기한다. SGIS 구(舊) 도메인
sgisapi.kostat.go.kr은 sgisapi.mods.go.kr로 리다이렉트된다(방화벽 허용 필요).

## 구조

```
medirank/
  config.py            # .env 로딩, 키 유무로 mock/실데이터 자동 전환
  geo.py               # 하버사인 거리, 반경 필터 (PostGIS 도입 전 검증용)
  scoring.py           # 점수 엔진 — 노출 40 / 경쟁밀도 25 / 수요입지 20 / 플레이스 15
  report.py            # 월간 지표 JSON 빌더 (고객 안전 요약값만 포함)
  connectors/hira.py   # 건강보험심사평가원 병원정보서비스
  connectors/naver_local.py  # 네이버 지역 검색 API (display 최대 5, 한도 준수)
  connectors/naver_content.py # 통합검색 6영역(블로그·카페·웹·뉴스·이미지·지식iN) 노출 판정
  connectors/sgis.py   # SGIS 인구·상권 (인구가중 체감밀도 포함)
  connectors/searchad.py # 네이버 검색광고 키워드도구 (절대 검색량·연관검색어, HMAC 서명)
fixtures/              # 목업 데이터 (강남 샘플 병원 12곳, 키워드 10개)
tests/                 # 단위 테스트
```

## 준수 사항

- 네이버 API는 공식 오픈 API만, 호출 한도 내 저빈도로 사용. 화면 크롤링 없음
- 원본 API payload는 고객 결과 JSON에 포함하지 않음 (관리자단 증빙 분리)
- 이름 매칭은 초기 휴리스틱 — 0단계 성공 기준은 샘플 3개 지역 매칭 정확도 85%
- 점수 계수는 초기 캘리브레이션 값으로, 실데이터 분포 확인 후 조정
