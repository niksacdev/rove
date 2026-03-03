# Copilot Instructions for ROVE

## Changelog Requirement

When creating or preparing a pull request, always update `CHANGELOG.md`:

1. Add or update entries under `## Unreleased` at the top of the file
2. Each entry needs a `###` heading (short capability title) and 1-3 sentences describing what the user/system can now do
3. Focus on capabilities, not implementation details — no file-by-file bullet lists
4. Include the PR number at the end: `*(PR #N)*`
5. Entries are ordered newest-first; `## Unreleased` is always at the top
6. When the PR merges, the `## Unreleased` entries should be moved under a dated `## YYYY-MM-DD` header

### Example entry

```markdown
## Unreleased

### Forward Kinematics Verification
Added physics-based verification of VLA action outputs using MuJoCo forward kinematics. FK sanity checks override VLM plausibility scores with objective measurements. *(PR #11)*
```

Refer to `CLAUDE.md` for full project conventions, architecture, and terminology.
