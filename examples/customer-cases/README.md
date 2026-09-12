# Synthetic customer onboarding cases

These three original ROVE illustrations exercise customer image/task intake and
plan assessment. They are synthetic graphics created for this project, not camera
observations, research-dataset samples or measured robot outcomes. The illustrations
and `cases.json` are covered by the repository's [MIT license](../../LICENSE).

| Case | Intended assessment |
| --- | --- |
| Clear target | Identify the red block and describe a placement plan |
| Missing target | Recognize missing task evidence rather than invent the target |
| Blocked path | Account for an obstacle and the requirement to leave the blue block undisturbed |

Start ROVE, open **Cases**, and choose **Try robotics sample cases**. The service imports
each PNG as a private managed asset and creates an immutable task/input revision.
Select a plan-quality success contract and a supported strategy, then preview a
small campaign. A human review can assess the proposed output; no robot was run.

There are no approved labels or measured episode outcomes in this pack. Reviewers
must supply explicit annotations and independently rate each candidate output.
The separate [configured verification examples](../verification/campaign.json) exercise
static synthetic outcome evidence; regrading those recordings does not establish
improved physical performance by a new strategy.
