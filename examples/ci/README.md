# Evaluate a saved robotics study in GitHub Actions

`campaign-evaluation.yml` is a manual workflow example, not an enabled repository
workflow. It restores an explicitly selected study, evaluates a candidate strategy,
attaches HTML/JSON/CSV reports and exits unsuccessfully if declared campaign targets
are missing, unresolved or unmet. A fresh GitHub runner has no ROVE history until the
store is restored.

The example installs this ROVE checkout with `uv sync --frozen`; it does not install
an older published CLI package. A customer repository should check out an explicitly
pinned ROVE revision containing these commands and adapt configuration/data paths.
Each workflow attempt creates a fresh candidate campaign with an attempt-specific
operation ID; repeated calls with that same ID are idempotent.

Before copying it into `.github/workflows/`:

1. Prepare a study with saved cases, a success contract containing reviewed campaign
   targets, a completed campaign and an exact named baseline revision. Use automatic
   graders for unattended acceptance; unresolved human ratings fail the gate honestly.
2. Export the store with `rove exchange export rove-state.zip --store STUDY_STORE` and
   upload that file as a **rove-state** artifact from a trusted, approved workflow run.
3. Supply that exact run ID, source campaign, baseline revision and a configured candidate
   strategy. The first example run does not invent a baseline or auto-select the newest
   artifact. Later runs can explicitly select a prior run's retained state artifact.
4. Keep configuration in the repository and credentials in the runner environment.
   The example config needs no provider credentials for mock evaluation; live provider
   and hardware integration are outside this example. Strategy catalog files are not
   part of relational exchange: use a checked-in candidate definition or restore its
   reviewed immutable revision through the strategy CLI.

The workflow uses `rove campaign improve`, `preview`, `run --revision "$GITHUB_SHA"
--output reports --require-targets`, `timeline` and `compare`. The commit is a declared
revision label, not proof of immutable remote model weights. Runtime or evaluator
changes can make baseline conditions noncomparable; the comparison artifact states
that explicitly. The absolute target gate does not silently become a statistical
regression gate or assert physical success.

Reports are uploaded with `if: always()` even when a gate fails. A separate relational
exchange artifact retains the connected campaign history and managed assets. Restoring
an earlier explicitly approved artifact preserves baseline revisions; a mutable cache
or “latest artifact” lookup would not provide that guarantee. Artifact retention still
limits how long a future run can restore it.

Only the `reports/` directory and generated exchange ZIP are uploaded. Do not upload
`rove.yaml`, `.env`, credentials or assistant proposal files. The exchange bundle and
reports can contain customer images, tasks, outputs and annotations: use repository
and artifact access appropriate to that dataset. Credential sanitization is not a
customer-data anonymizer.

Local command and service tests exercise these paths; this example has not been
executed on GitHub Actions or against a live provider. The upload action uses the
[verified v4.6.2 release](https://github.com/actions/upload-artifact/releases/tag/v4.6.2);
artifact behavior is documented by [GitHub](https://github.com/actions/upload-artifact).
