# Optional collector profiles

These profiles connect ROVE's optional monitoring copies to an explicitly selected
collector. ROVE's SQLite journal and managed evidence remain authoritative.
The default profile has no external exporter; hosted forwarding is opt-in.

| File | Destination | Validation status |
| --- | --- | --- |
| [collector-local.yaml](collector-local.yaml) | Basic local debug summaries | YAML/policy checks; collector process not run |
| [collector-grafana.yaml](collector-grafana.yaml) | Explicit Grafana Cloud OTLP endpoint | Source-reviewed template; hosted forwarding unvalidated |
| [collector-azure-monitor.yaml](collector-azure-monitor.yaml) | Explicit Application Insights resource | Source-reviewed managed-identity template; authentication/ingestion unvalidated |
| [rove-observability.yaml](rove-observability.yaml) | ROVE → loopback collector | ROVE schema validated; disabled until explicitly enabled |

## Version and local setup

The collector configuration target is **OpenTelemetry Collector Contrib 0.160.0**,
whose [release](https://github.com/open-telemetry/opentelemetry-collector-contrib/releases/tag/v0.160.0)
and tagged source were reviewed on 12 September 2026. No collector binary/container
was installed or run. YAML parsing and repository policy tests do not replace the
collector's own configuration validation. Before use, obtain that exact release
through your normal verified distribution process and run its local validation:

```sh
otelcol-contrib validate --config=examples/observability/collector-local.yaml
```

To opt into the local debug collector after validation:

```sh
otelcol-contrib --config=examples/observability/collector-local.yaml
```

Merge the ROVE fragment into your existing `rove.yaml`, then set its `enabled` to
`true`. Start a trial and inspect its `telemetry.export.status` and local history.
The debug exporter uses basic summaries, not full trace payload logging. Select
exactly one collector file; these are complete alternatives, not merge overlays.
Stop ROVE monitoring by setting `enabled: false`.

All receivers bind **127.0.0.1:4318**, with HTTP only. Internal collector metrics
are disabled, so no additional metrics listener is intended. There is no health,
profiling, filelog, host-metrics or discovery receiver. Run collector and ROVE in
the same host network namespace. A container's loopback is a different namespace;
these files intentionally do not widen the listener to make Docker port mapping
work. No Docker installation or deployment is part of this example.

The receiver accepts OTLP HTTP JSON from ROVE. The Grafana exporter uses protobuf
for its separate outbound request. The pinned canonical exporter name is
`otlp_http`; `otlphttp` is a deprecated alias. Similarly, `azure_monitor` and
`azure_auth` replace the older compact names. Revalidate names/schema before
changing collector versions. [Receiver source](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.160.0/receiver/otlpreceiver/config.go),
[HTTP exporter metadata](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.160.0/exporter/otlphttpexporter/metadata.yaml),
[HTTP exporter configuration](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.160.0/exporter/otlphttpexporter/config.go).

## Grafana forwarding template

Select `collector-grafana.yaml` only after the owner authorizes hosted forwarding.
Supply the stack's **HTTPS base OTLP endpoint**, instance ID and an appropriately
scoped ingestion token through the three named environment variables. The
collector appends `/v1/traces`; do not duplicate that suffix. Credentials are
referenced with `${env:...}` and are never committed. The registered Basic Auth
extension supplies outbound authorization. Do not disable TLS verification.
[Grafana's official configuration](https://grafana.com/docs/opentelemetry/collector/opentelemetry-collector/),
[pinned Basic Auth extension](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.160.0/extension/basicauthextension/README.md).

The example disables retry and bounds the sending queue. This is a diagnostic
starting profile, not a durable delivery service. Validate credentials, ingestion,
actual trace ancestry, retention and outage behavior before claiming support.

## Azure Monitor forwarding template

The Azure template proposes an outbound `azure_auth` extension with an explicitly
selected system-assigned managed identity and the Monitor scope. For a
user-assigned identity, add its approved `client_id` under `managed_identity`.
It is intended for an Azure host that supplies that identity; running it on a
developer laptop does not create managed identity. Supply the approved Application
Insights connection string via environment, including an HTTPS ingestion endpoint.
That string still identifies the destination when bearer authentication is used.
This is separate from Copilot's model-provider authentication.
[Pinned Azure auth source](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.160.0/extension/azureauthextension/config.go),
[extension guidance](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.160.0/extension/azureauthextension/README.md).

Both Azure components are beta upstream. The exporter's authentication guide
documents an authenticator extension and also retains older proxy guidance; this
template's direct managed-identity path is **not integration-tested by ROVE**.
Validate the actual identity role/scope, resource authorization, disabled-local-auth
policy and stored trace mappings under the live-provider specification. Do not
silently fall back to unauthenticated/local-key ingestion if identity fails.
Azure uses its own `maxbatchsize`, `maxbatchinterval`, `shutdown_timeout` and
`spaneventsenabled` fields; Grafana's `retry_on_failure` field is not copied into it.
[Pinned exporter configuration](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.160.0/exporter/azuremonitorexporter/config.go),
[authentication guidance](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.160.0/exporter/azuremonitorexporter/AUTHENTICATION.md),
[stability and canonical type](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.160.0/exporter/azuremonitorexporter/metadata.yaml).

## Evidence boundary

The local SDK tests use real loopback HTTP endpoints, including failures and
blackholes. They do not run these collector binaries or establish downstream
retention. Hosted readiness requires the
[live-provider/Azure acceptance matrix](../../docs/product/live-provider-validation.md).
Span names and structural IDs can contain organization-specific metadata even with
content capture disabled. Keep destination selection and data policy explicit.
Collector loss affects monitoring copies, never reconstructs or replays evaluations.
See [runtime observability](../../docs/architecture/runtime-observability.md).
