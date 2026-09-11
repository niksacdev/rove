# Security and data handling

ROVE is a single-user local development tool, without authentication or tenant
isolation. The supported launcher binds to `127.0.0.1`; the app rejects foreign
Host headers and cross-origin browser requests. Do not expose it through a public
proxy or tunnel. Local processes and users can still access it.

Inputs, uploaded files and evaluation history are stored under `data/output/`.
The current app mounts `data/` for local file serving, including runtime output.
This release does not separate that storage from the static mount. Review or
remove sensitive local output before sharing a workspace or enabling access.

Model adapters can send images, prompts and metadata to the endpoints you choose.
The default mock workflow needs no model API credentials. The frontend loads
third-party CDN scripts/fonts, so browsing the dashboard is not fully offline.
Keep secrets in environment variables, never in `rove.yaml` or committed reports.
Machine hostname is omitted from newly generated provenance by default.

Before publication, inspect every branch/tag and retained GitHub log, attachment
and artifact. Deleting a file in the latest tree does not erase it from history.
Prefer a clean release snapshot when old history contains private material.
The project's source cleanup is not a claim that older repository history is safe.

For a vulnerability, use GitHub's private vulnerability reporting if enabled.
Do not include credentials, customer data or exploit payloads containing private
files in a public issue. Rotate exposed credentials before attempting history cleanup.

## Optional LeRobot environment

The frozen default development/demo environment (core, dev, data, kinematics)
was audited without known advisories on 2026-09-11. The optional `smolvla` extra
is **not security-cleared**: upstream LeRobot constraints retain older Torch and
setuptools, The old Python 3.11 LeRobot stack is no longer resolved; the optional
inference extra requires Python 3.12+. Do not treat `--all-extras` as the supported setup. Updating those
pins requires a separately validated adapter/upstream compatibility change.
CI's required dependency audit covers the supported default extras only.
