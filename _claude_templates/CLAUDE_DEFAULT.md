# {BRANCH} — Claude Code 브랜치 지침

> 루트 `CLAUDE.md` 보안 규칙과 함께 적용된다.
> **이 파일은 기본 템플릿입니다. 브랜치 역할에 맞게 수정하세요.**

## 모듈 역할

TODO: 이 브랜치가 담당하는 기능을 한 문장으로 서술하세요.

## 파일 위치 규칙 (MANDATORY)

```
{BRANCH}/
├── src/       ← import 전용 라이브러리 (직접 실행 X)
├── scripts/   ← 직접 실행 스크립트 (python scripts/xxx.py)
├── tests/     ← pytest
├── config/    ← yaml, .env.example
└── docs/      ← 설계 문서, 리포트
```

| 파일 종류 | 저장 위치 |
|-----------|-----------|
| import되는 모듈, 유틸 함수 | `src/` |
| `python scripts/run_xxx.py`로 실행 | `scripts/` |
| pytest | `tests/` |
| `.yaml`, `.env.example` | `config/` |
| 문서, 리포트 | `docs/` |

**`{BRANCH}/` 루트 또는 프로젝트 루트에 `.py` 파일 직접 생성 금지.**

## 기술 스택

TODO: 주요 라이브러리를 기입하세요.

```python
import psycopg2
from dotenv import load_dotenv
```

## import 규칙

```python
# scripts/ 에서 src/ 모듈 import 방법
ROOT = Path(__file__).resolve().parents[2]  # scripts/는 parents[2]가 ROOT
_SRC = ROOT / "{BRANCH}" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
import my_module
```

## 인터페이스

- **업스트림**: TODO (어떤 데이터/결과를 받는지)
- **다운스트림**: TODO (어떤 데이터/결과를 내보내는지)
