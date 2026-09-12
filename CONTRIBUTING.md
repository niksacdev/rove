# Contributing

Run the frozen Python 3.12 setup and tests in README.md before opening a PR.
Keep changes small enough to review, and add regression cases for changed behavior.
Use mock adapters for default tests; cloud calls and model downloads must be explicit.

For feature work, update the relevant [product specification](docs/README.md),
architecture decision and diagram in the same PR. State whether behavior is
implemented or proposed, and link implementation claims to source and tests.
Preserve earlier decisions and add a superseding note when a design changes.

Evaluation claims must distinguish predicted actions, modeled diagnostics,
VLM judgments and observed episode outcomes. Unknown evidence must remain unknown
through prompts, summaries and the dashboard. Attribution is a hypothesis unless
validated against labeled failures. Document action units, frames and assumptions.

Do not commit local output, credentials, workplace/customer material or screenshots
without checking their content and redistribution rights. Use a GitHub noreply email
if you do not want a personal or work address in public commit metadata. See the
third-party notices before adding research assets.
