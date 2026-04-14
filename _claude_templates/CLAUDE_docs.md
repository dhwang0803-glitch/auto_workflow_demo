# docs — Claude Code 브랜치 지침 (위키 편집 전용)

> 이 브랜치는 코드 변경이 금지된다. `docs/context/` 하위 위키 문서만 편집한다.

## 역할

프로젝트의 **공용 지식 베이스(wiki)** 를 관리하는 브랜치.
ADR, 아키텍처 다이어그램, 파일 맵, 설계 결정 배경 등 여러 브랜치가
공통으로 참조하는 문서를 여기서만 갱신한다.

코드 브랜치(`API_Server` / `Database` / `Execution_Engine` / `Frontend`)는
위키를 **읽기 전용**으로 참조하고, 갱신이 필요하면 이 브랜치에서 별도 PR을 만든다.

## 관련 문서

- 전체 아키텍처: [`docs/context/architecture.md`](../docs/context/architecture.md)
- 설계 결정 배경: [`docs/context/decisions.md`](../docs/context/decisions.md)
- 파일 맵: [`docs/context/MAP.md`](../docs/context/MAP.md)

## 편집 범위 (MANDATORY)

허용:
- `docs/context/*.md` — 아키텍처/ADR/MAP 등 공용 지식
- `README.md` — 프로젝트 최상위 설명
- `_claude_templates/*.md` 의 "관련 문서" 섹션 — 상호 참조 링크 정비

금지:
- `.py`, `.ts`, `.tsx`, `.sql` 등 모든 소스 코드
- `API_Server/`, `Database/`, `Execution_Engine/`, `Frontend/` 하위 어떤 파일도
- `.githooks/`, `.github/` 동작 변경 (기능 변경이 아닌 문서 수정은 예외)

## 문서 갱신 원칙

1. **architecture.md**: 4-layer 흐름/경로가 바뀔 때만 수정. 개별 브랜치 내부 구조는 각 브랜치 `CLAUDE.md`에 둔다.
2. **decisions.md**: 기존 결정을 뒤집을 때는 기존 ADR에 *Superseded by ADR-###* 표시 + 새 ADR 추가. 삭제하지 않는다.
3. **MAP.md**: 새 최상위 폴더/브랜치가 생길 때만 갱신. 파일이 늘어날 때마다 갱신하지 않는다 (상위 구조 지도이지 파일 인덱스가 아님).
4. 코드 브랜치 PR에 위키 변경이 섞여 있으면 분리를 요청한다.

## PR 흐름

```
1) docs 브랜치 체크아웃
   git checkout docs && git pull origin main
2) docs/context/ 편집
3) /PR-report 실행 → PR 생성 (base: main)
4) 머지 후, 코드 브랜치는 자연스러운 시점에 main pull로 흡수
```

## 주의

- 위키가 코드와 어긋나면 **코드가 정답**. 위키를 고쳐서 코드와 맞춘다.
- 메모리(`~/.claude/.../memory/`)와 역할이 다르다: 위키는 **팀 공용 지식(git 추적)**,
  메모리는 **Claude 개인 세션 지식(로컬 파일)**. 서로 복사하지 않는다.
