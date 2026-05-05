# document_parser — 실측 발견사항 (2026-05-07)

> W3-2 fixture 검증 단계 (`tests/fixtures/gitlab_handbook_excerpt.pdf`,
> GitLab handbook L&R page, 18.5 KB raw md → 8-page PDF, 256 KB) 에서
> 실제 PDF 를 파서에 통과시켜 확인된 사실들. **고도화 시점에 다시 본다.**

## 채택 결정 (옵션 A)

현 단계는 **노이즈 사후 필터링 없음** — chromium 의 print 배너가 chunk 본문에
박혀 들어와도 그대로 둔다. 이유:

- 노이즈 비율 ~5% (fixture 1종 실측, 자동 게이트 < 10% 로 보호됨)
- LLM 단계 (`policy_extract`) 가 "Licensing & Renewals" 같은 짧은 영문 라인은
  자연 무시할 가능성 높음. 검증 전에 휴리스틱 추가 시 fixture 1종에 과적합 위험
- 다른 PDF (스캔본·multi-column·표) 를 만나봐야 진짜 휴리스틱 형태가 보임
- fixture 1종으로 sanitize 룰 잡으면 다른 문서 본문을 오히려 손상시킬 위험이 큼

## 핵심 발견

### 1. chromium print 배너가 본문 안에 박힘

Headless chromium 의 `--print-to-pdf` 는 **기본적으로 페이지 헤더/푸터 ON**.
페이지 상단에 `5/5/26, 6:40 PM Licensing & Renewals` (날짜 + 페이지 제목),
하단에 `file:///C:/Users/.../tmpXXXXX.html N/8` (URL + 페이지 번호) 가
들어간다. pypdf 의 텍스트 추출은 이 배너를 본문과 구분 못 하고 그대로
긁어옴 → chunk #2/#4/#5/#7 등에 박혀 있음.

**현재 가드**: `test_real_handbook_browser_print_noise_is_bounded` —
`file:///` 출현 횟수 × 80자 추정으로 노이즈 비율 < 10% 강제.

**고도화 안**:
- 페이지 헤더/푸터 패턴 (`<date> <page_title>` / `<url> N/M`) regex 사후 strip
- pypdf 의 `mediabox` 좌표 활용해서 페이지 상하단 ~50pt 영역 텍스트는 따로 분리
- `--no-pdf-header-footer` 로 fixture 재생성하고 노이즈 없는 ground truth 따로 두기
  (real-world fixture vs. clean fixture 두 트랙)

### 2. 호스트 OS 로케일이 fixture 에 누수

ko-KR 로케일 호스트에서 chromium 을 그냥 실행하면 배너 날짜가
`26. 5. 5. 6:36` + `���` (한국어 "년 월 일" → 비-ASCII 글리프 → pypdf
decode 실패) 로 박힘. 이 fixture 는 Kaggle 영어 환경 시뮬레이션이 목적이라
**`--lang=en-US` + `LANG=en_US.UTF-8` env override** 로 강제 해결
(`scripts/generate_handbook_fixture.py` 참조).

**고도화 안**:
- Modal 등 빌드 환경에서 fixture 생성하면 호스트 로케일 영향 0. CI/Modal
  one-shot 스크립트로 빌드 단계 옮기기
- 사용자 실제 PDF 도 ko-KR 로케일에서 인쇄하면 같은 깨짐 발생할 수 있음 →
  업로드 시점에 PDF 의 `/Producer` 메타데이터·텍스트 디코드 확인해서 로케일
  손상 케이스 사전 경고

### 3. markdown 링크 URL 이 PDF 단계에서 사라짐

`[a decision was made](https://gitlab.com/.../issues/96)` 가 chromium PDF 에선
"a decision was made" 만 본문에 남고 URL 은 hover-only 로 사라짐. pypdf
추출 결과에도 URL 없음. 즉 `policy_extract` 가 출처 URL 을 받지 못한다.

**현 정책**: link text 가 본문에 살아 있으니 의미 손실은 작다. URL 은
ADR-022 `source_kind` = `policy_doc` 의 fixture-level 인용으로 충분히
복원 가능 (chunk N → 원문 페이지 URL 자체).

**고도화 안**:
- markdown → HTML 단계에서 `<a href>` 의 URL 을 본문에 inline 으로 박기
  (예: "a decision was made (https://...)") — markdown extension 으로 처리 가능
- 단, real-world 핸드북 PDF 는 보통 워드/구글 docs 에서 export 되므로 URL inline
  여부는 원본 환경 의존. 일반화 어려움

### 4. paragraph snap 이 잘 작동, 단 chunk 경계가 인용문 가운데로 끊기는 케이스 있음

대부분 chunk 는 빈 줄 (`\n\n`) 경계에 깔끔하게 끊기지만, 800자 윈도우의
마지막 1/4 안에 빈 줄이 없으면 char-level 로 자르며 단어 중간이 잘리는
경우도 있음 (예: chunk #1 시작 `l priorities prevented...` — 이전 chunk 의
"business-critica" 잔여).

**현 정책**: 100자 overlap carry-over 가 단어 손상을 retrieval 단계에서
보완. embedding 은 어차피 chunk 단위라 의미 손상 작음.

**고도화 안**:
- snap 윈도우를 1/4 → 1/3 으로 늘리거나, 빈 줄이 없으면 마침표/문장경계
  (`. `) 로 차선 snap
- BPE-aware chunking (BGE-M3 토크나이저 기준) — 토큰 경계 정확히 떨어뜨리기

## 고도화 트리거 조건

아래 중 하나가 발생하면 본 문서 다시 펼치고 위 항목들 우선순위 재평가:

1. policy_extract LLM 의 출력에 배너 텍스트 (`Licensing & Renewals` 같은
   페이지 타이틀 reuse) 가 skill description 으로 추출되는 사례 발견
2. 사용자 업로드 실제 PDF (스캔본 / multi-column / 표 위주 / non-Latin) 에서
   현 chunking 이 무너지는 케이스
3. retrieval recall@5 가 fixture 기준에서도 60% 아래 → chunking 자체 의심
4. langgraph 전환 (PLAN_13) 에서 "skill 재추출" 회로가 노이즈 chunk 로 인해
   잘못된 회귀 트리거 발생

## 참조

- `scripts/generate_handbook_fixture.py` — fixture 빌드 (chromium en-US 강제)
- `scripts/dump_handbook_parse.py --noise` — 배너가 박힌 chunk 만 골라서 dump
- `tests/test_document_parser.py` — 7개 real-handbook assertion (위 트레이드오프 보호)
- `tests/fixtures/NOTICE.md` — fixture 출처/라이선스 (MIT)
- ADR-022 `source_kind = policy_doc` — 출처 URL 복원 정책
