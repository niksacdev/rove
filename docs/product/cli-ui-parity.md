# Script the robotics evaluation journey

**Status:** Implemented command surface, with isolated service/CLI regression tests.
**Updated:** 13 September 2026. Commands below run from this source checkout; do not
assume an older published package includes them.

Compare the same task, image and robot description across chosen strategies, inspect
the separate saved trials, then retain cases and success criteria in campaigns to
measure improvement over time. Browser and command workflows share configuration
resolution, execution, assessment, comparison and SQLite records.

| Persona | Job | Supported workflow |
| --- | --- | --- |
| ML Engineer on a Manipulation Team | Find useful model or agent candidates on customer inputs | Multi-strategy `trial run`, inspect outputs, seed a campaign, agree criteria and repeat |
| Robotics Researcher | Isolate a component change and explain an improvement | Immutable cases/strategies, baseline revisions, ablation previews, paired comparison and iteration timeline |
| Platform / AI Infrastructure Team | Automate reliable evaluation and evidence delivery | Config validation, explicit assistant confirmation, headless runs, report gates and portable study artifacts |

## Commands by capability

`FILE` is a JSON/YAML request unless stated otherwise. Trial, campaign, baseline and
data leaf commands accept `--store PATH` and `--config PATH`. Use `--help` on the leaf
command for request options. Read commands emit JSON; progress and errors use stderr.

| Operation | Commands | Behavior and limits |
| --- | --- | --- |
| Immediate comparison | `rove trial run --task TEXT --image FILE --urdf FILE --strategy A --strategy B` | One saved trial per strategy with the same supplied inputs; URDF optional. Use `--case REV` for a saved case |
| Inspect trials | `rove trial list`, `show ID`, `events ID`, `trace ID`, `evidence ID` | Reads retained records and available evidence; no execution on inspection |
| Reuse a trial | `rove trial seed ID --operation-id KEY`, `promote ID --operation-id KEY` | Preserves one trial's case/strategy context. Add other strategies explicitly to the campaign request |
| Cases and robot assets | `rove data case create --file FILE --image IMAGE`, `revise ID --file FILE --expected-head REV`, `list`, `show ID`; `rove data asset create --file URDF --media-type TYPE`, `show DIGEST`, `export DIGEST DEST` | Managed bytes, immutable revisions and conflict checks; asset export validates the digest and does not overwrite |
| Import case collections | `rove data case import --file MANIFEST`, `import-samples DIRECTORY` | JSON/JSONL/YAML case lists with relative image paths; sample import uses `manifest.json`. Imports are bounded and validate input before mutation |
| Agree success rules | `rove campaign metrics-draft FILE`; `rove data contract create --file FILE`, `list`, `show ID` | Drafting is separate from saving and execution. AI unavailable/failure uses a labelled template; criteria need review |
| Expert assessment | `rove data review record --file FILE`, `revise ID --file PATCH --expected-revision N`, `list`, `show ID` | Draft edits or immutable corrections to final judgments; previous evidence remains available |
| Save case collections | `rove data dataset preview --file FILE`, `freeze --file FILE --preview-hash HASH`, `list`, `show ID`, `export ID` | Exact membership revision and preview required. Export returns metadata references; use exchange for asset bytes |
| Prepare and execute campaigns | `rove campaign preview FILE`, `run FILE --operation-id KEY --revision REV`, `resume ID`, `watch ID`, `cancel ID --url URL` | Shared planning/execution and stable identities. Cancel contacts the owning local server; it does not blindly change SQLite |
| Inspect results | `rove campaign list`, `show ID`, `report ID --output DIR`, `summary ID` | Assessed HTML/JSON/CSV reports; summary is interpretation, with honest assistant/template provenance |
| Baselines and improvement | `rove baseline list`, `save FILE`, `show ID`, `revisions ID`, `revise ID FILE`; `rove campaign improve ID`, `compare ID --strategy CANDIDATE` | Exact frozen baseline assessment revisions; later reviews do not rewrite the reference |
| Controlled ablations | `rove campaign ablation-preview ID FILE`, `ablation ID FILE` | Existing comparison validator checks changed conditions before a controlled delta claim |
| Track iterations | `rove campaign timeline ID` | Connected, validated source/baseline lineage; case/criteria/strategy changes are visible, unrelated studies stay separate |
| Strategy revisions | `rove strategy list`, `show ID`, `revisions`, `preview FILE`, `save FILE` | Uses `--config PATH`; preview and parent fingerprints protect immutable variant creation |
| Configuration | `rove config show`, `models`, `validate` | Uses `--config PATH`; sanitized configuration discovery and validation, not live endpoint health |
| Optional assistant | `rove assistant status`, `ask FILE --proposal-output FILE`, `confirm FILE --confirmed` | Uses the existing local server via `--url`; proposal files are private, and execution requires explicit confirmation |
| Recent dashboard history | `rove history list`, `show ID`, `remove ID` | Uses the local server via `--url`; removal affects recent dashboard history, not durable trial evidence |
| Portable study state | `rove exchange export BUNDLE.zip --store STORE`, `import BUNDLE.zip --store NEW_STORE` | Relational records and managed assets, validated on import |
| Existing entry points | `rove serve`; `rove benchmark run MANIFEST`, `resume ID`, `report ID`, `list` | Retained compatibility; benchmark reports use the same assessed projection and configuration loader |

Data and ordinary trial/campaign read commands accept `--output JSON_FILE` where shown
by help. For campaign `run`, `resume`, `report` and `ablation`, `--output DIR` instead
writes the report bundle. Strategy/configuration commands emit JSON to stdout.
Mutation requests retain their service-level operation IDs, expected revisions and
preview hashes. The CLI does not silently approve generated rules or bypass conflicts.

## Reports in continuous integration

`rove campaign run FILE --revision "$GITHUB_SHA" --output reports --require-targets`
produces reports before returning a failed exit status for unmet, undeclared or
unresolved targets. `--require-success-rate 0.8` optionally requires the observed rate
for every selected strategy. Unknown planned outcomes fail enabled gates. These are
evaluation acceptance gates, not proof of physical success or statistical superiority.

The [manual workflow example](../../examples/ci/README.md) restores an explicitly
approved exchange artifact before running, then retains reports and updated study
state separately. A fresh runner has no historical store until restoration. Revision
metadata records the declared checkout; it does not freeze remote model weights.

## Verification and boundaries

Command help and dispatch are exercised, alongside mock execution, shared assessment,
case/review/dataset lifecycles, immutable strategy conflicts, assistant confirmation,
baseline comparison and report/gate tests. Relevant suites are
[`test_workflow_cli.py`](../../tests/test_workflow_cli.py),
[`test_data_cli.py`](../../tests/test_data_cli.py),
[`test_configuration_cli.py`](../../tests/test_configuration_cli.py),
[`test_assistant_cli.py`](../../tests/test_assistant_cli.py),
[`test_cli_entrypoints.py`](../../tests/test_cli_entrypoints.py) and
[`test_campaign_trajectory.py`](../../tests/test_campaign_trajectory.py).

Browser preferences do not need a command equivalent. Dashboard history and ephemeral
assistant confirmation intentionally require the owning server; evaluation and durable
evidence workflows run headlessly. Configuration discovery does not provision provider
accounts. The example GitHub workflow and live provider/hardware operation have not
been executed as part of this validation.
