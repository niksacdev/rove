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
| Cases → Configure → Run → Review & improve | Follow the work rather than assemble storage entities |
| Gallery dialog with image/task cards | Recognize cases and select many without an unbounded dropdown |
| Draft expected outcome with source and missing-evidence notice | Help prepare assessment without manufacturing expert labels |
| Settings on the right; assistant inside Configure | Separate reusable setup from the current evaluation |
| Show case × strategy × repetition and live trial identities | Explain how pipeline activity contributes to the report |
| Save case collection; new revision on extension | Make reuse understandable while preserving provenance |
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
