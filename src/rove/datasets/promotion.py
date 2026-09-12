"""Preserve a quick trial as an exploratory reference for newly planned evaluations."""

from __future__ import annotations

import re
import uuid

from rove.datasets.service import ConflictError, DatasetService, _document
from rove.trials.snapshots import content_hash
from rove.trials.store import now

# The old quick-example container mixes public observations and private grading
# inputs. Copy only documented candidate inputs; the original snapshot retains
# the rest for inspection without inserting it into a new candidate's context.
_PUBLIC_EXTRAS = frozenset(
    {
        "proprioception",
        "robot",
        "action_dim",
        "state_dim",
        "control_space",
        "action_space_desc",
        "initial_joint_positions",
        "constraints",
    }
)
_PRIVATE_FIELDS = frozenset(
    {
        "ground_truth",
        "annotations",
        "reference_output",
        "recorded_evidence",
        "episode",
        "expected_subtasks",
        "acceptable_interpretations",
        "eval_qa",
        "reviews",
        "baseline_output",
    }
)


def _public_context(value):
    if isinstance(value, dict):
        return {
            key: _public_context(item) for key, item in value.items() if key not in _PRIVATE_FIELDS
        }
    if isinstance(value, list):
        return [_public_context(item) for item in value]
    return value


def _result(service: DatasetService, row, *, reused: bool) -> dict:
    case = service.get_case_revision(row["case_revision_id"])
    return {
        "operation_id": row["operation_id"],
        "trial_id": row["trial_id"],
        "case_id": case["case_id"],
        "case_revision_id": case["id"],
        "name": case["name"],
        "role": "exploratory_reference",
        "eligible_for_reliability": False,
        "reused": reused,
        "created_at": row["created_at"],
        "input_notes": [
            "Original execution and grading material remain in the source trial.",
            "Campaigns using this case plan fresh trials; the reference is not counted as another attempt.",
        ],
    }


def promote_quick_trial(root, trial_id: str, *, operation_id: str, name: str | None = None) -> dict:
    """Atomically create a reusable input revision and link its existing execution.

    The source trial never changes. No review, label, new execution or campaign
    attempt is created by promotion. Retry the same operation ID after disconnects.
    """
    if not isinstance(operation_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", operation_id
    ):
        raise ValueError("A stable operation_id of 1..128 identifier characters is required")
    service = DatasetService(root)
    trial = service.trials.get(trial_id)
    if trial["source"] != "quick" or trial["status"] == "running":
        raise ValueError(
            "Promote a finished quick trial; running and campaign trials are not eligible"
        )
    task = trial["task"]
    instruction = task.get("task", task.get("instruction"))
    asset = task.get("image_asset")
    if not isinstance(instruction, str) or not instruction.strip() or not isinstance(asset, dict):
        raise ValueError("This trial lacks the task or managed image required to create a case")
    digest = asset.get("sha256")
    if not isinstance(digest, str):
        raise ValueError("This trial does not retain a managed image reference")
    extras = (task.get("example") or {}).get("extras") or {}
    public = {key: value for key, value in extras.items() if key in _PUBLIC_EXTRAS}
    public.update(task.get("candidate_context") or {})
    payload = service._case_payload(
        {
            "name": name if name is not None else instruction[:120],
            "task": instruction,
            "candidate_context": _public_context(public),
            "conditions": {},
            "recorded_evidence": {},
        }
    )
    expected_hash = content_hash({**payload, "image_sha256": digest})

    def existing(db):
        row = db.execute(
            "SELECT * FROM quick_promotions WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is not None:
            case = db.execute(
                "SELECT sha256 FROM case_revisions WHERE id=?", (row["case_revision_id"],)
            ).fetchone()
            if row["trial_id"] != trial_id or case["sha256"] != expected_hash:
                raise ConflictError("Operation ID was already used for a different promotion")
        return row

    with service.trials.connect() as db:
        row = existing(db)
    if row is not None:
        return _result(service, row, reused=True)
    service._image(digest)
    created, case_id, revision_id = now(), uuid.uuid4().hex, uuid.uuid4().hex
    with service.trials.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = existing(db)
        if row is None:
            db.execute("INSERT INTO cases VALUES (?,NULL,?)", (case_id, created))
            db.execute(
                "INSERT INTO case_revisions VALUES (?,?,1,NULL,?,?,?,?)",
                (revision_id, case_id, digest, expected_hash, _document(payload), created),
            )
            db.execute("UPDATE cases SET head_revision_id=? WHERE id=?", (revision_id, case_id))
            db.execute(
                "INSERT INTO quick_promotions VALUES (?,?,?,?)",
                (operation_id, trial_id, revision_id, created),
            )
            row = db.execute(
                "SELECT * FROM quick_promotions WHERE operation_id=?", (operation_id,)
            ).fetchone()
            reused = False
        else:
            reused = True
    return _result(service, row, reused=reused)


def get_promotion(root, operation_id: str) -> dict:
    service = DatasetService(root)
    with service.trials.connect() as db:
        row = db.execute(
            "SELECT * FROM quick_promotions WHERE operation_id=?", (operation_id,)
        ).fetchone()
    if row is None:
        raise KeyError(operation_id)
    return _result(service, row, reused=True)


def list_promotions(
    root, *, trial_id: str | None = None, limit: int = 50, offset: int = 0
) -> list[dict]:
    service = DatasetService(root)
    service.trials._page(limit, offset)
    with service.trials.connect() as db:
        rows = db.execute(
            """SELECT * FROM quick_promotions WHERE (? IS NULL OR trial_id=?)
                ORDER BY created_at DESC,operation_id DESC LIMIT ? OFFSET ?""",
            (trial_id, trial_id, limit, offset),
        ).fetchall()
    return [_result(service, row, reused=True) for row in rows]
