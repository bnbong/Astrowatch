# Astrowatch 프론트엔드

HTML · CSS · JavaScript (프레임워크 없음). 백엔드 API(`/api/*`)만 호출한다.
디자인: 방향 B "Almanac" — 밝은 종이 바탕(`--bg #F6F1E7`) + 남색 잉크(`--ink #1B2A4A`) + 호박 강조. 토큰은 `css/style.css` 맨 위 `:root`.

| 파일 | 화면 |
|---|---|
| `index.html` | 대화 화면: 조건 스트립 · 추천 카드(밤 타임바) · 실행 패널(타임라인/근거 데이터) (F3018 · F3019) |
| `plans.html` | 계획 관리: 날짜별 목록 · 상세 서랍 · 수정/삭제 · 캘린더 등록 (F3015 · F3016 · F3023) |
| `logs.html` | 실행 로그 조회 · 유형 필터 · 삭제, `logs.html#<session_id>` 로 바로 열기 (F3014) |
| `js/api.js` | fetch 래퍼 + 표시용 포맷 (`window.Api`, `window.Fmt`) |
| `js/markdown.js` | 안전한 Markdown 부분집합 렌더러 (`window.Markdown`) |
| `js/ui.js` | 아이콘(inline SVG) · 판정 배지 · 밤 타임바 · 레일 연결 상태 (`window.UI`) |
| `js/timeline.js` | 단계 · Tool 호출 · 재계획 지점 표시 (`window.Timeline`) |

반응형: 1080px 이하에서 실행 패널은 아래 시트, 640px 이하에서 좌측 레일 대신 하단 탭.
폰트는 Google Fonts(IBM Plex Sans KR · Noto Serif KR · Space Grotesk)를 `<link>` 로 불러오고, 실패하면 시스템 폰트로 대체된다.
캐시 버스터: CSS/JS 를 바꾸면 HTML 의 `?v=` 값을 올린다.

실행: 백엔드가 정적 파일로 서빙한다 → http://127.0.0.1:8000/
