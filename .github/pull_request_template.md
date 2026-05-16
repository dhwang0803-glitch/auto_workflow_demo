## 📝 Summary of changes

<!-- Briefly describe what changed and why -->

### Change type
- [ ] New feature
- [ ] Bug fix
- [ ] Refactor
- [ ] DB schema change
- [ ] Docs / config update

### Key changes
<!-- Bullet the modified files and core logic -->
-
-

---

## 📊 Impact Assessment

<!-- Paste the IMPACT_ASSESSOR agent output -->
<!-- Branch agent location: {branch-name}/agents/IMPACT_ASSESSOR.md -->

### Scope
- **Layer(s)**: <!-- DB / API / ML / Frontend / docs -->
- **Files changed**:
- **Change type**:

### Impact per layer

| Item | Result | Details |
|------|--------|---------|
| Folder structure rules | ✅ Compliant | |
| DB schema | ➖ N/A | |
| API contract | ➖ N/A | |
| ML pipeline | ➖ N/A | |
| Frontend | ➖ N/A | |

### Risk level
<!-- 🔴 HIGH / 🟡 MEDIUM / 🟢 LOW -->

**Rationale**:

### Rollback plan
- [ ] Migration DOWN script ready
- [ ] Previous version tag exists
- [ ] No DB snapshot needed (no schema change)

### Follow-ups required
- [ ] None

---

## 🔒 Security review

<!-- Paste the SECURITY_AUDITOR agent output -->
<!-- Branch agent location: {branch-name}/agents/SECURITY_AUDITOR.md -->

| Item | Result |
|------|--------|
| S01 Hardcoded credentials | ✅ None |
| S02 `getenv` defaults exposing infra info | ✅ None |
| S03 Direct `.env` references | ✅ None |
| S04 DB-connection command rules | ✅ Compliant |
| S05 `.gitignore` verified | ✅ Verified |

**Scope reviewed**: <!-- list of files or paths inspected -->

---

## ✅ Test results

```
pytest {branch-name}/tests/ -v

PASSED  N
FAILED  N
SKIPPED N
```

---

## 📋 Checklist

- [ ] Merging via PR rather than pushing directly to `main`
- [ ] Impact assessment completed (IMPACT_ASSESSOR run)
- [ ] Security review completed (SECURITY_AUDITOR run)
- [ ] Tests pass
- [ ] Related docs updated (if applicable)
- [ ] Reviewers assigned
