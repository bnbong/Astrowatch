# Astrowatch 프론트엔드

HTML · CSS · JavaScript (프레임워크 없음). 백엔드 API(`/api/*`)만 호출한다.

| 파일 | 화면 |
|---|---|
| `index.html` | 대화 화면 + 실행 타임라인 (F3018 · F3019) |
| `plans.html` | 계획 관리 (F3015 · F3016) |
| `logs.html` | 실행 로그 조회 · 삭제 (F3014) |
| `js/api.js` | fetch 래퍼 |
| `js/timeline.js` | 단계 · Tool 호출 · 재계획 지점 표시 |

실행: 백엔드가 정적 파일로 서빙한다 → http://127.0.0.1:8000/
