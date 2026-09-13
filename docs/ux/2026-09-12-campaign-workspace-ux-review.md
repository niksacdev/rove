# Campaign workspace UX review

**Status:** Implemented with behavior regression coverage and desktop mock journey verification; 390px gallery verified in light and dark themes.

## Review finding

PR #21 made links more consistent but did not establish a comprehensible evaluation
workflow. The correction must change the interaction: visible selected cases, strategy
selection tied to the existing configuration, understandable criteria, explicit trial
identity and connected evidence review. Restyling the previous forms alone is insufficient.

## Decisions and rationale

| Decision | User need |
| --- | --- |
| Cases → Configure → Success metrics → Run → Results | Separate choosing systems, agreeing evidence and interpreting results |
| Gallery dialog with image/task cards | Recognize cases and select many without an unbounded dropdown |
| Draft expected outcome with source and missing-evidence notice | Help prepare assessment without manufacturing expert labels |
| Settings on the right; assistance tied to the current stage | Keep reusable setup separate and make draft/explanation context clear |
| Show case × strategy × repetition and progress for each strategy | Explain how pipeline activity contributes to the report |
| Charts, optional AI summary, then Inspect trials | Answer outcome questions before exposing the detailed evidence ledger |
| Save case collection; new revision on extension | Make reuse understandable while preserving provenance |
| Create a campaign / Run campaign / Review results | Separate creating, executing and reading without introducing another entity |
| Set as baseline; badge from saved reference only | Make the comparison role explicit and trustworthy |
| Start comparison campaign after preview | Make new execution deliberate; reading or saving results never reruns the agent |
| Neutral/slate/teal palette and generous controls | Improve hierarchy and remove the rejected visual treatment |

## Accessibility and verification status

No comprehensive accessibility certification or independent customer usability study is claimed.
Required checks include contrast in both themes, visible keyboard focus, correctly
labelled inputs and progress, dialog focus containment/Escape/return, hidden-stage tab
order, and usable narrow layouts. Case-picker bounds and status/composer alignment need
visual inspection with realistic content, including many cases and long instructions.

New DOM tests cover gallery selection, case and scoring drafts, collection membership,
Run configuration/trial identity and lazy evidence inspection. Stage details refresh
on expansion or request; campaign progress uses polling rather than token streaming.
The full desktop dark-theme flow was exercised with three isolated mock trials, and
the narrow navigation shell was checked in both themes. Review summary freshness found
during browser use was corrected and added to the regression suite. The final narrow
case gallery was checked in both themes. Detailed evidence is maintained
in the [campaign workspace specification](../product/user-journey.md#acceptance-and-delivery-evidence).
Review sample-derived success suggestions separately from actual image analysis, and
keep physical outcome evidence and attributed expert judgments explicit.

## 13 September refinement

The user found the assessment controls and results still too dense. A separate Success
metrics stage and graph-first Results are accepted corrections. Retain count labels,
unknown outcomes and evidence scope in every comparison; concise AI prose must not
replace the recorded assessment. The refinement was verified with one case, two mock
strategies and three repetitions: all six executions were persisted and remained
unassessed in Results until expert review. Recorded stages appeared per strategy;
trial inspection began collapsed. The 390px Results view had no horizontal overflow.
Fake-runtime tests exercise draft/summary boundaries, and focused UI checks cover
saved-rule edits and honest fallback labels. Live AI or hardware validation is not
claimed. The earlier gallery checks above remain separate evidence.
