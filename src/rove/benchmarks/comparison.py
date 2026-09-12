"""Conservative paired comparisons of frozen campaign conditions.

Model/component ablations may change the candidate system. Cases, assessment,
environment, runtime and planned attempt budgets must remain attributable and
equal. Report descriptive observations; do not imply statistical significance or
new physical performance from grading previously recorded episodes.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from typing import Any

from rove.benchmarks.metrics import summarize
from rove.benchmarks.models import fingerprint
from rove.models.config import StrategyConfig
from rove.trials.snapshots import sanitize

_COSMETIC = {"display_name", "description", "notes", "tags", "availability"}
_SECRET = re.compile(
    r"(?:^|_)(?:api_?key|secrets?|password|passwd|credentials?|authorization|access_token|"
    r"refresh_token|bearer_token|private_key|connection_string|sas_token|token|headers|env|auth)(?:$|_)",
    re.IGNORECASE,
)


class _InvalidConditions(ValueError):
    """An authored, safe explanation of a missing comparison prerequisite."""


def _configuration(value: Any, *, cosmetic: bool = False) -> Any:
    """Ignore presentation and credentials, preserving behavior-affecting values."""
    if isinstance(value, dict):
        return {
            key: _configuration(item)
            for key, item in value.items()
            if (not cosmetic or key not in _COSMETIC)
            and not _SECRET.search(re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).replace("-", "_"))
        }
    if isinstance(value, list):
        return [_configuration(item) for item in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return sanitize(value)
    return value


def _endpoint(campaign: dict, reference: str | None) -> dict | None:
    if reference is None:
        return None
    endpoint = campaign["config"]["endpoints"][reference]
    identity = dict(campaign.get("model_versions", {}).get(reference, {}))
    # prepare() historically hashes the entire endpoint including display labels
    # and secrets. Replace that derived hash with normalized configuration; retain
    # an independently supplied fingerprint, which may identify changed weights.
    if identity.get("identity_hash") == fingerprint(endpoint):
        identity.pop("identity_hash")
    result = {
        "configuration": _configuration(endpoint, cosmetic=True),
        "identity": _configuration(identity, cosmetic=True),
    }
    if endpoint.get("adapter") == "local_verifier":
        evaluator_version = campaign.get("local_evaluators", {}).get(reference)
        if not evaluator_version:
            raise _InvalidConditions("Local evaluator source identity is missing")
        result["evaluator_version"] = evaluator_version
    return result


def _system(campaign: dict, strategy_id: str) -> tuple[dict, dict, dict]:
    if strategy_id not in campaign["spec"]["strategies"]:
        raise _InvalidConditions("Selected strategy is not part of the campaign plan")
    definition = campaign["config"]["strategies"][strategy_id]
    strategy = StrategyConfig.model_validate(definition)
    stages = {}
    for name in ("perceive", "plan", "act", "verify"):
        options = strategy.stage_options(name)
        if options is None:
            stages[name] = None
            continue
        normalized = options.model_dump(mode="json")
        normalized["endpoint"] = _endpoint(campaign, options.endpoint)
        if name == "verify":
            normalized["mode"] = options.mode if options.mode != "auto" else strategy.verify_mode
            normalized["checks"] = [
                {**check.model_dump(mode="json"), "endpoint": _endpoint(campaign, check.endpoint)}
                for check in options.checks
            ]
        stages[name] = normalized
    defaults = _configuration(campaign["config"].get("defaults", {}))
    # Campaign strategies resolve explicit endpoint references. These unused
    # legacy fallback names may reference endpoints not frozen into the campaign.
    for name in ("sim", "grounding"):
        defaults.pop(name, None)
    environment = {
        "sim": _endpoint(campaign, strategy.sim),
        "defaults": defaults,
        "environment": campaign.get("environment"),
    }
    grading = {"verify": stages.pop("verify"), "compute_dynamics": strategy.compute_dynamics}
    # Unknown future strategy options remain visible and affect identity. Known
    # endpoint aliases are replaced by their resolved stage configurations above.
    settings = {
        key: value
        for key, value in definition.items()
        if key not in {*stages, "verify", "verify_mode", "sim", "compute_dynamics"}
    }
    system = {"stages": stages, "settings": _configuration(settings, cosmetic=True)}
    return system, grading, environment


def _case_key(task: dict) -> str:
    return str(task.get("case_revision_id") or task["id"])


def _cases(campaign: dict) -> dict[str, dict]:
    tasks = campaign["spec"]["tasks"]
    if not tasks:
        raise _InvalidConditions("Campaign has no planned cases")
    cases = {_case_key(task): task for task in tasks}
    if len(cases) != len(tasks) or len({task["id"] for task in tasks}) != len(tasks):
        raise _InvalidConditions("Campaign case identities are not unique")
    return cases


def _case_definition(task: dict) -> dict:
    # A display alias may change when an immutable case revision identifies it.
    return {
        key: value for key, value in task.items() if key != "id" or not task.get("case_revision_id")
    }


def _public(value: Any) -> Any:
    def redact(item):
        if isinstance(item, dict):
            return {
                key: "[REDACTED]"
                if _SECRET.search(re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).replace("-", "_"))
                else redact(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [redact(child) for child in item]
        return item

    return sanitize(redact(value))


def _differences(before: Any, after: Any, path: str = "") -> list[dict]:
    if before == after:
        return []
    key = path.rsplit(".", 1)[-1]
    sensitive = _SECRET.search(re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).replace("-", "_"))
    if isinstance(before, dict) and isinstance(after, dict) and not sensitive:
        output = []
        for key in sorted(before.keys() | after.keys()):
            output.extend(_differences(before.get(key), after.get(key), f"{path}.{key}".strip(".")))
        return output
    return [
        {
            "path": path,
            "baseline": _public({key: before})[key],
            "candidate": _public({key: after})[key],
        }
    ]


def _planned_records(
    campaign: dict, trials: list[dict], strategy_id: str
) -> tuple[dict, dict, list[str]]:
    cases = _cases(campaign)
    seeds = campaign["spec"]["seeds"]
    if not seeds or len(set(seeds)) != len(seeds):
        raise _InvalidConditions("Campaign repeat seeds must be nonempty and unique")
    by_task = {task["id"]: key for key, task in cases.items()}
    allowed = {(task_id, seed) for task_id in by_task for seed in seeds}
    slots: dict[tuple, dict] = {}
    problems = []
    for row in trials:
        if row.get("strategy_id") != strategy_id:
            continue
        key = row.get("task_id"), row.get("seed")
        if key not in allowed:
            problems.append("Trial records exist outside the selected campaign plan")
            continue
        if key in slots:
            if slots[key] != row:
                problems.append("Conflicting records exist for one planned attempt")
                slots[key] = {
                    "task_id": key[0],
                    "seed": key[1],
                    "strategy_id": strategy_id,
                    "outcome": "unknown",
                    "execution": "conflicting_records",
                }
            continue
        slots[key] = copy.deepcopy(row)
    cleaned = []
    outcomes = {}
    for task_id, seed in sorted(allowed):
        row = slots.get((task_id, seed))
        outcome = "unknown"
        if row is not None:
            if row.get("execution") == "completed" and row.get("outcome") in {"pass", "fail"}:
                outcome = row["outcome"]
            row = {**row, "outcome": outcome, "execution": row.get("execution", "unknown")}
            cleaned.append(row)
        outcomes[by_task[task_id], seed] = outcome
    selected = copy.deepcopy(campaign)
    selected["spec"]["strategies"] = [strategy_id]
    # k is report configuration, not an experimental condition. Callers receive
    # each campaign's declared curve; paired comparisons below always use seeds.
    selected["spec"].setdefault("ks", [1])
    report = summarize(selected, cleaned)
    return report, outcomes, problems


def _has_recorded_episode(cases: dict[str, dict]) -> bool:
    return any(
        bool((task.get("example") or {}).get("extras", {}).get("episode"))
        or bool(task.get("episode"))
        for task in cases.values()
    )


def _physical_contract(contract: Any) -> bool:
    """Recognize explicit physical claims; never infer them from the task text."""
    if isinstance(contract, dict):
        return any(
            (
                key in {"requires_fresh_execution", "physical_success", "observed_episode"}
                and value is True
            )
            or _physical_contract(value)
            for key, value in contract.items()
        )
    if isinstance(contract, list):
        return any(_physical_contract(value) for value in contract)
    return isinstance(contract, str) and contract.lower() in {
        "physical_success",
        "robot_task_success",
        "episode_success",
        "observed_episode",
        "physical_execution",
        "robot_execution",
        "observed_robot_success",
        "physical_completion",
    }


def compare_campaigns(
    baseline: dict,
    baseline_trials: list,
    candidate: dict,
    candidate_trials: list,
    baseline_strategy_id: str,
    candidate_strategy_id: str,
) -> dict:
    """Compare matched planned attempts without changing or regrading their records."""
    reasons: list[str] = []
    differences: list[dict] = []
    case_comparisons = []
    result = {
        "comparable": False,
        "reasons": reasons,
        "differences": differences,
        "baseline": None,
        "candidate": None,
        "case_comparisons": case_comparisons,
        "scope": "output_assessment",
        "metric_scope": baseline.get("metric_scope", "configured_verification"),
        "evidence_note": "Compares configured output assessments; it does not establish fresh physical robot performance. Model revisions are supplied identities, not independently verified weights. Paired counts are descriptive; no significance claim or task-macro confidence interval is computed.",
    }
    try:
        base_system, base_grader, base_environment = _system(baseline, baseline_strategy_id)
        new_system, new_grader, new_environment = _system(candidate, candidate_strategy_id)
        base_cases, new_cases = _cases(baseline), _cases(candidate)
        if not baseline.get("runtime") or baseline.get("runtime") != candidate.get("runtime"):
            reasons.append(
                "Runtime identity is missing or changed; the effect cannot be attributed to the strategy"
            )
        if base_grader != new_grader:
            reasons.append("Grader, required checks or assessment settings changed")
        if base_environment != new_environment:
            reasons.append("Environment or execution defaults changed")
        for field in ("suite_version", "grading", "timeout_s"):
            if baseline["spec"].get(field) != candidate["spec"].get(field):
                reasons.append(f"Campaign {field} changed")
        if set(baseline["spec"]["seeds"]) != set(candidate["spec"]["seeds"]):
            reasons.append("Planned repeat seeds or attempt budget changed")
        for field in ("dataset_revision_id", "contract_id", "contract"):
            if baseline.get(field) != candidate.get(field):
                reasons.append(f"Frozen {field} changed")
        if baseline.get("metric_scope", "configured_verification") != candidate.get(
            "metric_scope", "configured_verification"
        ):
            reasons.append("Metric outcome source changed")
        if set(base_cases) != set(new_cases) or any(
            _case_definition(task) != _case_definition(new_cases[key])
            for key, task in base_cases.items()
            if key in new_cases
        ):
            reasons.append("Case revisions, inputs or supplied evidence changed")
        differences.extend(
            _differences(
                {
                    "system": base_system,
                    "grading": base_grader,
                    "environment": base_environment,
                    "runtime": baseline.get("runtime"),
                    "contract": baseline.get("contract"),
                    "cases": base_cases,
                    "dataset_revision_id": baseline.get("dataset_revision_id"),
                    "contract_id": baseline.get("contract_id"),
                    "seeds": sorted(baseline["spec"]["seeds"]),
                    "timeout_s": baseline["spec"].get("timeout_s"),
                },
                {
                    "system": new_system,
                    "grading": new_grader,
                    "environment": new_environment,
                    "runtime": candidate.get("runtime"),
                    "contract": candidate.get("contract"),
                    "cases": new_cases,
                    "dataset_revision_id": candidate.get("dataset_revision_id"),
                    "contract_id": candidate.get("contract_id"),
                    "seeds": sorted(candidate["spec"]["seeds"]),
                    "timeout_s": candidate["spec"].get("timeout_s"),
                },
            )
        )
        if _has_recorded_episode(base_cases) or _has_recorded_episode(new_cases):
            result["scope"] = "recorded_evidence_regrade"
            result["evidence_note"] += (
                " Both systems receive recorded episode evidence. Regrading that evidence cannot show whether changed actions or a changed strategy would succeed in a fresh episode."
            )
            if _physical_contract(baseline.get("contract")) or _physical_contract(
                candidate.get("contract")
            ):
                reasons.append(
                    "A physical-outcome contract requires fresh execution evidence; static episode replay cannot supply it"
                )
        elif _physical_contract(baseline.get("contract")) or _physical_contract(
            candidate.get("contract")
        ):
            reasons.append(
                "Fresh robot execution evidence is not established by these campaign records"
            )
        if not baseline.get("contract"):
            result["evidence_note"] += (
                " No explicit frozen success contract is present; legacy verify-stage judgments are the only comparison scope."
            )
            if result["metric_scope"] != "configured_verification":
                reasons.append("Review-based outcomes require an explicit frozen success contract")
        result["baseline"], base_outcomes, base_problems = _planned_records(
            baseline, baseline_trials, baseline_strategy_id
        )
        result["candidate"], new_outcomes, new_problems = _planned_records(
            candidate, candidate_trials, candidate_strategy_id
        )
        result["baseline"]["metric_scope"] = baseline.get("metric_scope", "configured_verification")
        result["candidate"]["metric_scope"] = candidate.get(
            "metric_scope", "configured_verification"
        )
        reasons.extend(base_problems + new_problems)
        reasons[:] = list(dict.fromkeys(reasons))
        comparable = not reasons
        pairs = Counter(
            dict.fromkeys(
                ("pass_to_pass", "pass_to_fail", "fail_to_pass", "fail_to_fail", "unknown"), 0
            )
        )
        seeds = sorted(set(baseline["spec"]["seeds"]) & set(candidate["spec"]["seeds"]))
        for key in sorted(base_cases.keys() & new_cases.keys()):
            counts = Counter({name: 0 for name in pairs})
            slots = []
            for seed in seeds:
                before = base_outcomes[key, seed]
                after = new_outcomes[key, seed]
                change = f"{before}_to_{after}" if "unknown" not in {before, after} else "unknown"
                counts[change] += 1
                slots.append({"seed": seed, "baseline": before, "candidate": after})
            pairs.update(counts)
            if not comparable:
                status = "not_comparable"
            elif counts["pass_to_fail"] and counts["fail_to_pass"]:
                status = "mixed"
            elif counts["pass_to_fail"]:
                status = "regression"
            elif counts["fail_to_pass"]:
                status = "improvement"
            elif counts["unknown"]:
                status = "inconclusive"
            else:
                status = "unchanged"
            case_comparisons.append(
                {
                    "case_id": key,
                    "baseline_task_id": base_cases[key]["id"],
                    "candidate_task_id": new_cases[key]["id"],
                    "status": status,
                    "paired_counts": dict(counts),
                    "planned_pairs": len(seeds),
                    "paired_coverage": (len(seeds) - counts["unknown"]) / len(seeds)
                    if seeds
                    else 0,
                    "attempts": slots,
                }
            )
        result["comparable"] = comparable
        result["paired_counts"] = dict(pairs) if comparable else None
        planned_pairs = len(base_cases) * len(baseline["spec"]["seeds"])
        result["paired_coverage"] = (
            (planned_pairs - pairs["unknown"]) / planned_pairs if comparable else None
        )
    except _InvalidConditions as exc:
        reasons.append(str(exc))
    except (KeyError, TypeError, ValueError) as exc:
        # Malformed or old records must not accidentally earn a comparison claim.
        reasons.append(f"Campaign conditions could not be validated: {type(exc).__name__}")
    return result
