"""Assistant drafts are proposals; saved evidence and ordinary scoring remain authoritative."""

import copy
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from rove.api.insights import create_insights_router
from rove.benchmarks.insights import CampaignInsights, MetricsDraftRequest
from rove.benchmarks.models import CampaignSpec
from rove.benchmarks.runner import prepare
from rove.benchmarks.store import CampaignStore
from rove.datasets.service import DatasetService
from rove.models.config import RoveConfig


class Runtime:
    def __init__(self):
        self.calls = []
        self.instances = []
        self.output = None
        self.error = None

    def __call__(self, settings, tools=()):
        self.instances.append((settings, tools))
        return self

    async def health_check(self):
        return True

    async def run_stage(self, stage, image, task, context):
        self.calls.append((stage, image, task, context))
        if self.error:
            raise self.error
        return copy.deepcopy(self.output)


@pytest.fixture
def h(tmp_path, monkeypatch):
    monkeypatch.delenv("ROVE_ASSISTANT_ENDPOINT", raising=False)
    monkeypatch.setattr("rove.benchmarks.runner.runtime_fingerprint", lambda: {"version": "test"})
    config = RoveConfig.model_validate(
        {
            "endpoints": {
                "assistant": {
                    "type": "agent",
                    "adapter": "copilot_agent",
                    "config": {
                        "model": "draft-model",
                        "provider": {
                            "base_url": "https://configured.invalid",
                            "api_key": "test-private-credential",  # pragma: allowlist secret
                        },
                    },
                },
                "mock": {
                    "type": "vlm",
                    "adapter": "mock_vlm",
                    "capabilities": ["task_planning", "verification"],
                },
            },
            "strategies": {"baseline": {"plan": "mock", "verify": "mock"}},
        }
    )
    service = DatasetService(tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    asset = service.trials.save_asset(image.getvalue(), "image/png")
    case = service.import_case(
        {
            "name": "Pick",
            "task": "Pick the red block",
            "reference_data": {
                "expected_subtasks": ["Pick red block", "Place inside bin"],
            },
        },
        asset["sha256"],
    )
    runtime = Runtime()
    app = FastAPI()
    app.include_router(
        create_insights_router(
            tmp_path, config_loader=lambda: config, runtime_factory=runtime, endpoint_id="assistant"
        )
    )
    request = {
        "case_revision_ids": [case["id"]],
        "name": "Pick trial",
        "strategies": ["baseline"],
        "seeds": [0, 1],
    }
    return {
        "root": tmp_path,
        "config": config,
        "runtime": runtime,
        "client": TestClient(app),
        "service": service,
        "case": case,
        "asset": asset,
        "request": request,
    }


def draft(h):
    return h["client"].post("/api/campaigns/metrics-draft", json=h["request"])


def output_for(h):
    # Use a real valid template as a fixture shape, then distinguish model-authored text.
    result = draft(h).json()
    result["contract"]["case_expectations"][h["case"]["id"]] = (
        "Identify the red block and destination; state uncertainty if either is not visible."
    )
    return {
        "contract": result["contract"],
        "rationale": "Drafted acceptance based on the selected task and reference instructions.",
    }


def make_campaign(h, *, human=False):
    spec = CampaignSpec(
        name="Saved run",
        suite_version="one",
        revision="one",
        strategies=["baseline"],
        seeds=[0, 1],
        ks=[1, 2],
        tasks=[
            {"id": h["case"]["id"], "task": h["case"]["task"], "image_asset": h["asset"]["sha256"]}
        ],
    )
    payload = prepare(spec, h["config"])
    if human:
        contract = h["service"].create_contract(output_for(h)["contract"])
        payload.update(
            contract=contract, contract_id=contract["id"], metric_scope="success_contract"
        )
    store = CampaignStore(h["root"])
    cid = store.create(payload)
    for seed in (0, 1):
        trial_id = h["service"].trials.begin(
            source="campaign",
            task={"task": "Pick", "case_revision_id": h["case"]["id"]},
            strategy={"id": "baseline"},
            config={},
            campaign_id=cid,
            seed=seed,
        )
        result = {"stages": [], "success": True}
        h["service"].trials.finish(trial_id, status="completed", result=result)
        store.save_trial(
            cid,
            {
                "trial_id": trial_id,
                "task_id": h["case"]["id"],
                "strategy_id": "baseline",
                "seed": seed,
                "outcome": "pass" if seed == 0 else "unknown",
                "execution": "completed",
                "latency_ms": 12,
                "result": result,
            },
        )
    store.status(cid, "completed")
    return cid


def summary(h, cid):
    return h["client"].post(f"/api/campaigns/{cid}/outcome-summary")


def summary_output(h, cid):
    tid = CampaignStore(h["root"]).trials(cid)[0]["trial_id"]
    return {
        "headline": "Review unresolved evidence before drawing conclusions",
        "findings": [
            {
                "text": "This recorded trial can be inspected alongside unresolved outcomes.",
                "trial_ids": [tid],
                "evidence_refs": ["summary", f"trial:{tid}"],
            }
        ],
        "next_steps": ["Complete outstanding output reviews before comparing strategies."],
    }


@pytest.mark.asyncio
async def test_unconfigured_template_is_honest_and_never_calls_provider_or_writes(h):
    service = CampaignInsights(
        h["root"], config_loader=lambda: h["config"], runtime_factory=h["runtime"]
    )
    result = await service.draft(MetricsDraftRequest(**h["request"]))
    assert result["source"] == "template" and not result["assistant_available"]
    assert result["assistant_configured"] is False
    assert result["requires_confirmation"] and result["observation_basis"] == "task_and_annotations"
    assert "source annotations" in result["rationale"]
    assert "Place inside bin" in result["contract"]["case_expectations"][h["case"]["id"]]
    assert h["runtime"].instances == []
    assert h["service"].list_contracts() == []
    assert h["service"].list_reviews() == []
    assert CampaignStore(h["root"]).list() == []


def test_ai_draft_has_all_cases_private_reference_boundary_and_no_mutation(h):
    h["runtime"].output = output_for(h)
    explicit = "Require confirmation of the bin location when ambiguous."
    h["request"]["case_expectations"] = {h["case"]["id"]: explicit}
    response = draft(h)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source"] == "ai"
    assert data["assistant_configured"] and data["assistant_available"]
    assert data["contract"]["case_expectations"][h["case"]["id"]] == explicit
    assert h["runtime"].instances[-1][0].role == "assistant"
    assert h["runtime"].instances[-1][1] == ()
    context = h["runtime"].calls[-1][3]
    assert context["cases"][0]["reference_data"]["expected_subtasks"]
    assert "test-private-credential" not in json.dumps(context)
    assert (
        "reference_data" not in h["service"].get_case_revision(h["case"]["id"])["candidate_context"]
    )
    assert h["service"].list_contracts() == [] and h["service"].list_reviews() == []
    before = data["evidence_fingerprint"]
    h["config"].endpoints["mock"].config["temperature"] = 0.6
    assert draft(h).json()["evidence_fingerprint"] != before


@pytest.mark.parametrize("change", ["unknown_case", "missing_case", "physical", "automatic"])
def test_invalid_generated_contract_never_becomes_ai_draft(h, change):
    output = output_for(h)
    if change == "unknown_case":
        output["contract"]["case_expectations"]["invented"] = "Invented expectation"
    elif change == "missing_case":
        output["contract"]["case_expectations"] = {}
    elif change == "physical":
        output["contract"]["scope"] = "episode_outcome"
    else:
        output["contract"]["criteria"][0].update(assessment="configured_verifier", endpoint="mock")
    h["runtime"].output = output
    data = draft(h).json()
    assert data["source"] == "template" and data["warnings"]
    assert data["assistant_configured"] and not data["assistant_available"]
    assert data["contract"]["scope"] == "plan_quality"
    assert all(c["assessment"] == "human_review" for c in data["contract"]["criteria"])


def test_invalid_selection_and_large_context_do_not_start_runtime(h):
    for update in (
        {"case_revision_ids": ["missing"]},
        {"strategies": ["missing"]},
        {"case_expectations": {"not-selected": "No"}},
        {"seeds": [1, 1]},
    ):
        response = h["client"].post("/api/campaigns/metrics-draft", json={**h["request"], **update})
        assert response.status_code in {404, 422}
    h["config"].endpoints["mock"].config["prompt"] = "x" * 256001
    response = draft(h)
    assert response.status_code == 422 and "no cases or evidence" in response.text
    assert h["runtime"].instances == []


def test_summary_persists_across_router_restart_and_invalidates_configuration(h):
    cid = make_campaign(h)
    before = CampaignStore(h["root"]).trials(cid)
    h["runtime"].output = summary_output(h, cid)
    result = summary(h, cid)
    assert result.status_code == 200, result.text
    first = result.json()
    assert first["source"] == "ai" and not first["cached"]
    assert first["assistant_configured"] and first["assistant_available"]
    assert first["interpretation_only"]
    app = FastAPI()
    app.include_router(
        create_insights_router(
            h["root"],
            config_loader=lambda: h["config"],
            runtime_factory=h["runtime"],
            endpoint_id="assistant",
        )
    )
    h["client"] = TestClient(app)
    calls = len(h["runtime"].calls)
    assert summary(h, cid).json()["cached"]
    assert len(h["runtime"].calls) == calls
    h["config"].endpoints["assistant"].config["model"] = "new-model"
    changed = summary(h, cid).json()
    assert (
        not changed["cached"]
        and changed["assistant_config_fingerprint"] != first["assistant_config_fingerprint"]
    )
    assert CampaignStore(h["root"]).trials(cid) == before
    assert h["service"].list_reviews() == []
    with CampaignStore(h["root"]).connect() as db:
        assert db.execute("SELECT count(*) FROM campaign_insights").fetchone()[0] == 2


def test_new_expert_review_invalidates_summary_without_reexecution(h):
    cid = make_campaign(h, human=True)
    h["runtime"].output = summary_output(h, cid)
    first = summary(h, cid).json()
    campaign = CampaignStore(h["root"]).get(cid)
    row = CampaignStore(h["root"]).trials(cid)[0]
    contract = campaign["contract"]
    h["service"].create_review(
        {
            "target_type": "trial_output",
            "trial_id": row["trial_id"],
            "case_revision_id": h["case"]["id"],
            "contract_id": contract["id"],
            "reviewer": "SME",
            "status": "final",
            "decision": "accepted",
            "criteria": {c["id"]: "accepted" for c in contract["criteria"]},
            "rationale": "Reviewed actual output",
        }
    )
    second = summary(h, cid).json()
    assert second["source"] == "ai" and not second["cached"]
    assert first["evidence_fingerprint"] != second["evidence_fingerprint"]
    assert h["service"].trials.count() == 2
    assert len(h["service"].list_reviews()) == 1


@pytest.mark.parametrize("bad", ["trial", "anchor", "missing_anchor", "grade_field", "failure"])
def test_bad_summary_is_labelled_template_and_retryable(h, bad):
    cid = make_campaign(h)
    output = summary_output(h, cid)
    if bad == "trial":
        output["findings"][0]["trial_ids"] = ["invented"]
    elif bad == "anchor":
        output["findings"][0]["evidence_refs"] = ["invented"]
    elif bad == "missing_anchor":
        output["findings"][0]["evidence_refs"] = ["summary"]
    elif bad == "grade_field":
        output["outcome"] = "pass"
    else:
        h["runtime"].error = RuntimeError("provider token must not leak")
    h["runtime"].output = output
    data = summary(h, cid).json()
    assert data["source"] == "template" and not data["cached"]
    assert data["assistant_configured"] and not data["assistant_available"]
    assert "1 planned outcomes remain unresolved" in data["findings"][0]["text"]
    assert "provider token" not in json.dumps(data)
    h["runtime"].error = None
    h["runtime"].output = summary_output(h, cid)
    assert summary(h, cid).json()["source"] == "ai"


def test_running_summary_never_calls_provider_or_writes_cache(h):
    cid = make_campaign(h)
    CampaignStore(h["root"]).status(cid, "running")
    data = summary(h, cid).json()
    assert data["source"] == "template" and "not completed" in data["warnings"][0]
    assert data["assistant_configured"] and not data["assistant_available"]
    assert h["runtime"].calls == []
    assert summary(h, "missing").status_code == 404


def test_frozen_dataset_drafts_exact_included_members_and_rejects_conflicting_selection(h):
    contract = h["service"].create_contract(output_for(h)["contract"])
    other = h["service"].import_case(
        {"name": "Other", "task": "Do not select this"}, h["asset"]["sha256"]
    )
    dataset = h["service"].freeze(
        {
            "name": "Saved inputs",
            "contract_id": contract["id"],
            "members": [
                {"case_revision_id": h["case"]["id"]},
                {
                    "case_revision_id": other["id"],
                    "disposition": "excluded",
                    "reason": "Different task",
                },
            ],
        }
    )
    request = {**h["request"], "case_revision_ids": [], "dataset_revision_id": dataset["id"]}
    result = h["client"].post("/api/campaigns/metrics-draft", json=request)
    assert result.status_code == 200, result.text
    assert set(result.json()["contract"]["case_expectations"]) == {h["case"]["id"]}
    request["case_revision_ids"] = [other["id"]]
    assert h["client"].post("/api/campaigns/metrics-draft", json=request).status_code == 422
    assert h["service"].get_dataset(dataset["id"])["contract_id"] == contract["id"]


@pytest.mark.asyncio
async def test_concurrent_summary_requests_share_one_generation(h):
    import asyncio

    cid = make_campaign(h)
    h["runtime"].output = summary_output(h, cid)
    insights = CampaignInsights(
        h["root"],
        config_loader=lambda: h["config"],
        runtime_factory=h["runtime"],
        endpoint_id="assistant",
    )
    original = h["runtime"].run_stage

    async def yield_once(*args):
        await asyncio.sleep(0)
        return await original(*args)

    h["runtime"].run_stage = yield_once
    results = await asyncio.gather(insights.summary(cid), insights.summary(cid))
    assert len(h["runtime"].calls) == 1
    assert [result["cached"] for result in results] == [False, True]


def test_evidence_changed_during_generation_is_not_cached_or_presented_as_current(h):
    cid = make_campaign(h)
    h["runtime"].output = summary_output(h, cid)
    original = h["runtime"].run_stage

    async def change_evidence(*args):
        output = await original(*args)
        CampaignStore(h["root"]).status(cid, "interrupted")
        return output

    h["runtime"].run_stage = change_evidence
    response = summary(h, cid)
    assert response.status_code == 422 and "evidence changed" in response.text
    with CampaignStore(h["root"]).connect() as db:
        assert db.execute("SELECT count(*) FROM campaign_insights").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_unconfigured_summary_distinguishes_missing_setup_from_generation_failure(h):
    cid = make_campaign(h)
    insights = CampaignInsights(
        h["root"], config_loader=lambda: h["config"], runtime_factory=h["runtime"]
    )
    result = await insights.summary(cid)
    assert result["source"] == "template"
    assert result["assistant_configured"] is False
    assert result["assistant_available"] is False
    assert h["runtime"].instances == []


def test_old_cached_summary_is_enriched_with_configured_status(h):
    cid = make_campaign(h)
    h["runtime"].output = summary_output(h, cid)
    first = summary(h, cid).json()
    assert first["assistant_configured"]
    with CampaignStore(h["root"]).connect() as db:
        row = db.execute("SELECT cache_key,payload FROM campaign_insights").fetchone()
        old_payload = json.loads(row["payload"])
        old_payload.pop("assistant_configured")
        db.execute(
            "UPDATE campaign_insights SET payload=? WHERE cache_key=?",
            (json.dumps(old_payload), row["cache_key"]),
        )
    cached = summary(h, cid)
    assert cached.status_code == 200, cached.text
    assert cached.json()["cached"] and cached.json()["assistant_configured"]


@pytest.mark.parametrize("shape", ["one", "six", "long"])
def test_draft_returns_three_to_five_short_individual_criteria(h, shape):
    generated = output_for(h)
    if shape == "one":
        generated["contract"]["criteria"] = generated["contract"]["criteria"][:1]
    elif shape == "six":
        generated["contract"]["criteria"] = [
            {"id": f"criterion_{i}", "description": "Check target"} for i in range(6)
        ]
    else:
        generated["contract"]["criteria"][0]["description"] = "x" * 401
    h["runtime"].output = generated
    data = draft(h).json()
    assert data["source"] == "template"
    assert 3 <= len(data["contract"]["criteria"]) <= 5
    assert all(len(c["description"]) <= 400 for c in data["contract"]["criteria"])
