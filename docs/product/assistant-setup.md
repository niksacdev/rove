# Choose an evaluation assistant

**Status:** Provider selection and authentication support implemented with controlled
regression coverage. Authenticated GitHub Copilot and Azure model inference
are not claimed by this documentation update.
**Updated:** 2026-09-15. [Decision: ADR-035](../architecture/ADR-035-assistant-provider-selection.md).

The assistant drafts success criteria, explains recorded outcomes and prepares
reviewable workflow operations. It does not replace the strategies under evaluation,
rewrite scores or manufacture expert judgments. Models chosen for the assistant are
independent of the models selected in a campaign's strategy table.

Open **Settings → Assistant** from the gear or a campaign's Configure assistant link.
Choose a provider, save it, explicitly test the connection, then return to the campaign.

| Choice | What to provide | Authentication |
| --- | --- | --- |
| GitHub Copilot | Use the default model or select an available model | Sign in through ROVE's explicit GitHub device flow |
| Existing endpoint | A configured `copilot_agent` endpoint, such as the local assistant | Existing server-side provider configuration |
| Microsoft Foundry | Azure model resource URL and deployment name | Azure CLI login on the machine running ROVE |
| Disabled | No provider | No assistant inference; manual workflow and saved results remain available |

A **new setup with no saved selection** defaults to GitHub Copilot. Existing saved
local selections and explicitly disabled settings are preserved. A default choice is
not proof of authentication or a working model connection. An explicit host endpoint
or `ROVE_ASSISTANT_ENDPOINT` overrides saved settings and appears read-only.

## GitHub Copilot

Use **Sign in** to start a device flow, follow the returned verification address and
code, and let ROVE report its result. Authentication uses a dedicated ROVE Copilot
profile; signing into GitHub elsewhere, including `gh`, is not automatically reused.
Cancel stops the pending ROVE login operation. It does not log other applications out.

Checking sign-in and discovering available models are explicit operations. Leaving
model selection at default lets Copilot choose its default rather than silently
substituting a local endpoint or an invented model ID. Save then Test connection
before requesting an explanation. SDK/CLI presence, authentication, model discovery
and successful test inference are separate checks.

With ROVE running locally:

```sh
rove assistant configure --provider copilot
rove assistant login
rove assistant login-status LOGIN_OPERATION_ID
rove assistant auth-status
rove assistant models
rove assistant test
```

Use `rove assistant configure --provider copilot --model MODEL_ID` for an explicitly
selected available model. `rove assistant cancel-login LOGIN_OPERATION_ID` cancels a
pending sign-in. Add `--url http://127.0.0.1:5010` to each command when that is the
running app's address; the default is port 5001.

## Existing local or custom endpoint

Select an existing Copilot adapter endpoint from `rove.yaml`. This retains your
configured provider URL/model and server-side credentials; it does not create a new
model server. The CLI equivalent is:

```sh
rove assistant configure --endpoint local-assistant
rove assistant test
```

The chosen endpoint must exist in the running server's configuration and use the
`copilot_agent` adapter. A local OpenAI-compatible server needs to be running and
serving the configured model. A previous successful local Qwen test does not prove
that every custom endpoint or model supports the required structured responses.

## Microsoft Foundry model deployment

Supply the **model resource** endpoint and **deployment name**, not a project URL or
Foundry Agent identifier. Supported public Azure host patterns are
`https://RESOURCE.services.ai.azure.com` and `https://RESOURCE.openai.azure.com`;
ROVE normalizes the model API path to `/openai/v1/`. URLs with credentials, query
strings, unrelated hosts or project/agent paths are rejected.

Sign into Azure CLI on the ROVE host using `az login` with an account allowed to call
the deployment. ROVE uses `AzureCliCredential` for a short-lived bearer token at
execution time. The browser does not accept an Azure password or access token, and
assistant settings store the endpoint/deployment rather than the token. Installing
dependencies or signing into Azure does not provision a resource or grant permission.

```sh
rove assistant configure --provider foundry \
  --foundry-endpoint https://YOUR_RESOURCE.services.ai.azure.com \
  --deployment YOUR_DEPLOYMENT
rove assistant test
```

This is a model-provider path through the Copilot runtime. It is distinct from a
campaign strategy that invokes an existing Foundry Agent using its project and agent
configuration. Cloud permissions and actual model inference still require validation
against the customer's resource.

## What saving and testing do

Save changes only the local provider selection, keyed by configuration path. It does
not start inference or alter old campaigns. **Test connection** sends a fixed
synthetic JSON request without case images, tools or private trial data. It succeeds
only after a valid response; a later outage or unsupported workload can still fail.

`rove assistant settings` reads the saved/effective selection. `rove assistant
disable` persists Disabled across restarts. Provider/authentication selection does not
authorize assistant-proposed case edits or campaign execution: those still require
the [exact operation confirmation](named-baselines-and-assistant.md).

For outcome summaries, the assistant receives bounded saved evidence and permitted
references. Raw trial prompts, action arrays and private journals are not automatically
forwarded. A generated explanation is interpretation, not a new measurement or a
physical-success judgment. See [traces and measurements](traces-and-measurements.md).
