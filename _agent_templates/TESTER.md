# Tester Agent Instructions

## Role
After Developer Agent writes the implementation files, actually runs the tests and collects results.
Connects to both the Ollama local LLM and the VPC PostgreSQL for integration tests.

---

## Loading connection info

```bash
# VPC DB connection (.env file)
export $(grep -v '^#' .env | xargs)

# RAG API keys (RAG/config/api_keys.env)
export $(grep -v '^#' RAG/config/api_keys.env | xargs)

# Ollama connection check
curl -s http://localhost:11434/api/tags | python -c "import sys,json; print('PASS' if json.load(sys.stdin) else 'FAIL')"
```

---

## Execution order per Phase

### Phase 1 (Setup & Pilot)
```bash
# verify packages
conda run -n myenv python -c "import requests, wikipedia, sentence_transformers; print('OK')"

# run tests
conda run -n myenv python -m pytest RAG/tests/test_phase1_pilot.py -v 2>&1
```

### Phase 2 (HIGH Priority)
```bash
# pipeline batch test (verify on a 10-row sample first)
conda run -n myenv python -m pytest RAG/tests/test_phase2_high.py -v 2>&1

# full run (after verification)
conda run -n myenv python RAG/src/rag_pipeline.py --column director --dry-run
```

### Phase 3 (Quality)
```bash
conda run -n myenv python -m pytest RAG/tests/test_phase3_quality.py -v 2>&1
```

---

## Result parsing rules

```bash
# extract PASS/FAIL from pytest output
output=$(conda run -n myenv python -m pytest RAG/tests/test_phase1_pilot.py -v 2>&1)

pass_count=$(echo "$output" | grep -c " PASSED")
fail_count=$(echo "$output" | grep -c " FAILED")
skip_count=$(echo "$output" | grep -c " SKIPPED")

echo "PASS: $pass_count, FAIL: $fail_count, SKIP: $skip_count"
```

---

## When Ollama is not running

If the Ollama server is not running:
- P1-01 FAIL → SKIP all LLM-dependent tests
- SKIP is not counted as FAIL (but record "Ollama must be running" in the report)
- Report to the Orchestrator immediately: "Ollama server not running — the user needs to run `ollama serve`"

---

## Result format to hand to the Orchestrator

```
[Tester run results]
- Run environment: Python 3.12 (myenv), Ollama {version or not running}
- Files run: [list]
- Total tests: X
- PASS: X
- FAIL: X
- SKIP: X
- Error rate: X%

FAIL items:
- [test ID] [message]

Next action:
- 0 FAIL → invoke Refactor Agent
- FAIL exists → re-invoke Developer Agent (retry N/3)
```

---

## Cautions

1. Do not expose connection info from `.env` and `api_keys.env` in logs or output
2. The 100-row pilot run hits real APIs — beware rate limits
3. On VPC connection failure, report to the Orchestrator immediately without retrying
4. Runtime environment: `conda activate myenv` (common across all branches, Python 3.12)
