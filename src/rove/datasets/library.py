"""Import bundled sample inputs as immutable cases without inventing evaluations."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from rove.datasets.service import MAX_IMAGE_BYTES, DatasetService, _document
from rove.trials.snapshots import content_hash
from rove.trials.store import now

_PUBLIC = frozenset(
    {
        "proprioception",
        "robot",
        "action_dim",
        "state_dim",
        "control_space",
        "action_space_desc",
        "initial_joint_positions",
        "eval_category",
        "constraints",
        "correction",
        "turns",
    }
)
_REFERENCE = frozenset(
    {
        "eval_qa",
        "expected_subtasks",
        "acceptable_interpretations",
        "ground_truth_action",
    }
)


def _revision_id(case_id: str, digest: str) -> str:
    return uuid.uuid5(uuid.UUID(hex=case_id), digest).hex


def import_sample(
    service: DatasetService, data_dir: Path, entry: dict, *, namespace="bundled-gallery"
) -> dict:
    """Stable input identity; repeat imports neither reset edits nor create reviews/trials."""
    if "eval_category" in entry and (
        not isinstance(entry["eval_category"], str) or not entry["eval_category"].strip()
    ):
        raise ValueError("Sample evaluation category must be a non-empty string")
    filename = entry.get("filename")
    if not isinstance(filename, str) or not filename or Path(filename).is_absolute():
        raise ValueError("A relative sample image filename is required")
    base = data_dir.resolve()
    path = (base / filename).resolve()
    if not path.is_relative_to(base) or ".." in Path(filename).parts:
        raise ValueError("Sample image must stay inside the data directory")
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Sample image exceeds 16 MiB")
    media = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
    asset = service.trials.save_asset(data, media)
    service._image(asset["sha256"])
    public = {k: v for k, v in entry.items() if k in _PUBLIC}
    references = {k: v for k, v in entry.items() if k in _REFERENCE}
    known = _PUBLIC | _REFERENCE | {"filename", "task", "name", "source", "episode"}
    conditions = {k: v for k, v in entry.items() if k not in known}
    conditions.update(library=namespace, original_filename=filename, source=entry.get("source", {}))
    if "eval_category" in entry:
        conditions["eval_category"] = entry["eval_category"]
    payload = service._case_payload(
        {
            "name": entry.get("name") or str(entry.get("task", ""))[:160],
            "task": entry.get("task"),
            "candidate_context": public,
            "conditions": conditions,
            "reference_data": references,
            "recorded_evidence": {"episode": entry["episode"]} if entry.get("episode") else {},
        }
    )
    case_id = uuid.uuid5(uuid.NAMESPACE_URL, f"rove:{namespace}:{filename}").hex
    digest = content_hash({**payload, "image_sha256": asset["sha256"]})
    revision_id = _revision_id(case_id, digest)
    created = now()
    with service.trials.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id FROM case_revisions WHERE id=?", (revision_id,)).fetchone()
        if existing:
            head = db.execute(
                "SELECT r.id,r.sha256 FROM cases c JOIN case_revisions r ON r.id=c.head_revision_id WHERE c.id=?",
                (case_id,),
            ).fetchone()
            # A source rollback can select an already archived revision. Follow it
            # only when the selected head was imported, never a user-authored edit.
            if head and head["id"] == _revision_id(case_id, head["sha256"]):
                db.execute("UPDATE cases SET head_revision_id=? WHERE id=?", (revision_id, case_id))
        else:
            head = db.execute(
                "SELECT head_revision_id FROM cases WHERE id=?", (case_id,)
            ).fetchone()
            parent_id = head["head_revision_id"] if head else None
            revision = db.execute(
                "SELECT coalesce(max(revision),0)+1 FROM case_revisions WHERE case_id=?", (case_id,)
            ).fetchone()[0]
            advance_head = True
            if not head:
                db.execute("INSERT INTO cases VALUES (?,NULL,?)", (case_id, created))
            elif parent_id:
                previous = db.execute(
                    "SELECT sha256 FROM case_revisions WHERE id=?", (parent_id,)
                ).fetchone()
                # A user-authored revision has its own random identity. Leave it selected.
                advance_head = parent_id == _revision_id(case_id, previous["sha256"])
            if not advance_head:
                imported = db.execute(
                    "SELECT id,sha256 FROM case_revisions WHERE case_id=? ORDER BY revision DESC",
                    (case_id,),
                ).fetchall()
                parent_id = next(
                    (r["id"] for r in imported if r["id"] == _revision_id(case_id, r["sha256"])),
                    None,
                )
            db.execute(
                "INSERT INTO case_revisions VALUES (?,?,?,?,?,?,?,?)",
                (
                    revision_id,
                    case_id,
                    revision,
                    parent_id,
                    asset["sha256"],
                    digest,
                    _document(payload),
                    created,
                ),
            )
            if advance_head:
                db.execute("UPDATE cases SET head_revision_id=? WHERE id=?", (revision_id, case_id))
    return service.get_case_revision(revision_id)


def sync_library(root: Path, data_dir: Path) -> dict:
    manifest = data_dir / "manifest.json"
    if not manifest.exists():
        return {"examples": [], "summary": {"total": 0, "imported": 0, "unavailable": 0}}
    rows = json.loads(manifest.read_text())
    if not isinstance(rows, list) or len(rows) > 1000 or any(not isinstance(r, dict) for r in rows):
        raise ValueError("Sample manifest must contain at most 1000 case records")
    filenames = [r.get("filename") for r in rows]
    if any(not isinstance(f, str) for f in filenames) or len(set(filenames)) != len(rows):
        raise ValueError("Sample filenames must be unique strings")
    service = DatasetService(root)
    results = []
    for entry in rows:
        try:
            case = import_sample(service, data_dir, entry)
            results.append(
                {
                    **entry,
                    "case_id": case["case_id"],
                    "case_revision_id": case["id"],
                    "import_status": "imported",
                    "suggested_success": case["suggested_success"],
                }
            )
        except (ValueError, OSError) as error:
            message = "Sample image is unavailable" if isinstance(error, OSError) else str(error)
            results.append({**entry, "import_status": "unavailable", "import_error": message})
    imported = sum(r["import_status"] == "imported" for r in results)
    return {
        "examples": results,
        "summary": {"total": len(rows), "imported": imported, "unavailable": len(rows) - imported},
    }
