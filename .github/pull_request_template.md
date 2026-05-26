## What does this PR do?

<!-- One or two sentences explaining the change. Focus on WHY, not what — the diff shows what. -->

## Type of change

- [ ] Bug fix
- [ ] New provider (LLM / cloud / git / alerting)
- [ ] New feature
- [ ] Refactor / internal improvement
- [ ] Docs / tests only

## Checklist

- [ ] `pytest tests/` passes locally
- [ ] `flake8 sentinel/ services/ tests/ --max-line-length=120` clean
- [ ] `isort --check-only sentinel/ services/ tests/` clean
- [ ] Tests added or updated for the changed code
- [ ] No secrets, tokens, or personal email addresses in the diff
- [ ] `CHANGELOG.md` updated (for user-visible changes)

## Related issues

<!-- Closes #XX, Fixes #XX, or "N/A" -->

## Testing notes

<!-- How did you test this? What edge cases did you check?
     If this affects a Lambda handler, confirm the relevant test class in
     tests/test_lambda_handlers.py covers the new behaviour. -->
