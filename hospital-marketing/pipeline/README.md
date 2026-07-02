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
```

## 실데이터 모드

`.env.example`을 `.env`로 복사하고 키를 채우면 자동으로 실 API를 호출합니다.

| 키 | 발급처 |
| --- | --- |
| `DATA_GO_KR_SERVICE_KEY` | [공공데이터포털](https://www.data.go.kr) → 병원정보서비스 활용신청 |
| `NAVER_CLIENT_ID/SECRET` | [네이버 개발자센터](https://developers.naver.com) → 애플리케이션 등록 |

## 구조

```
medirank/
  config.py            # .env 로딩, 키 유무로 mock/실데이터 자동 전환
  geo.py               # 하버사인 거리, 반경 필터 (PostGIS 도입 전 검증용)
  scoring.py           # 점수 엔진 — 노출 40 / 경쟁밀도 25 / 수요입지 20 / 플레이스 15
  report.py            # 월간 지표 JSON 빌더 (고객 안전 요약값만 포함)
  connectors/hira.py   # 건강보험심사평가원 병원정보서비스
  connectors/naver_local.py  # 네이버 지역 검색 API (display 최대 5, 한도 준수)
fixtures/              # 목업 데이터 (강남 샘플 병원 12곳, 키워드 10개)
tests/                 # 단위 테스트
```

## 준수 사항

- 네이버 API는 공식 오픈 API만, 호출 한도 내 저빈도로 사용. 화면 크롤링 없음
- 원본 API payload는 고객 결과 JSON에 포함하지 않음 (관리자단 증빙 분리)
- 이름 매칭은 초기 휴리스틱 — 0단계 성공 기준은 샘플 3개 지역 매칭 정확도 85%
- 점수 계수는 초기 캘리브레이션 값으로, 실데이터 분포 확인 후 조정
