"""Transactional local case curation; human judgments never silently become labels."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import uuid

from rove.datasets.models import CaseInput, FreezeInput, ReviewInput, SuccessContract
from rove.datasets.suggestions import suggest_success
from rove.trials.snapshots import canonical_json, content_hash, sanitize
from rove.trials.store import TrialStore, now

MAX_DOCUMENT_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024


class ConflictError(ValueError):
    """A caller's revision no longer matches the editable record."""


def _document(value: dict) -> str:
    try:
        value = canonical_json(sanitize(value))
    except (ValueError, TypeError) as error:
        raise ValueError("Documents require finite JSON values") from error
    if len(value.encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError("Document exceeds 256 KiB; large evidence must use managed assets")
    return value


def _required(db, table: str, identity: str):
    # Explicit statements keep this internal helper closed to SQL identifiers.
    queries = {
        "cases": "SELECT * FROM cases WHERE id=?",
        "case_revisions": "SELECT * FROM case_revisions WHERE id=?",
        "success_contracts": "SELECT * FROM success_contracts WHERE id=?",
        "reviews": "SELECT * FROM reviews WHERE id=?",
        "trials": "SELECT * FROM trials WHERE id=?",
        "dataset_revisions": "SELECT * FROM dataset_revisions WHERE id=?",
    }
    row = db.execute(queries[table], (identity,)).fetchone()
    if row is None:
        raise KeyError(identity)
    return row


class DatasetService:
    def __init__(self, root):
        self.trials = TrialStore(root)

    def _image(self, digest: str) -> dict:
        metadata = self.trials.asset_metadata(digest)
        if metadata["size_bytes"] > MAX_IMAGE_BYTES:
            raise ValueError("Case image exceeds 16 MiB")
        with self.trials.asset_path(digest).open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        if len(data) != metadata["size_bytes"] or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Managed image integrity check failed")
        if not data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
            raise ValueError("Case images must be PNG or JPEG")
        try:
            from PIL import Image
        except ImportError as error:
            raise ValueError(
                "Image validation requires the rove[data] optional dependency"
            ) from error
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG"} or image.width * image.height > 16_000_000:
                    raise ValueError("Case image dimensions or format are unsupported")
                actual_type = "image/png" if image.format == "PNG" else "image/jpeg"
                image.verify()
        except Exception as error:
            raise ValueError("Case image could not be decoded safely") from error
        return {**metadata, "media_type": actual_type}

    @staticmethod
    def _case_payload(payload: dict) -> dict:
        _document(payload)
        validated = CaseInput.model_validate(payload).model_dump(mode="json")
        private = {
            "ground_truth",
            "annotations",
            "reference_output",
            "reference_data",
            "ground_truth_action",
            "recorded_evidence",
            "episode",
            "expected_subtasks",
            "acceptable_interpretations",
            "eval_qa",
            "reviews",
            "baseline_output",
        }

        def contains_labels(value):
            if isinstance(value, dict):
                return bool(private & value.keys()) or any(
                    contains_labels(v) for v in value.values()
                )
            if isinstance(value, list):
                return any(contains_labels(v) for v in value)
            return False

        if contains_labels(validated["candidate_context"]):
            raise ValueError("Private labels and recorded outcomes cannot be candidate context")
        from rove.datasets.robotics import EpisodeRecording, SyntheticEnvironment

        recorded = validated["recorded_evidence"]
        if recorded.get("schema_version") is not None:
            validated["recorded_evidence"] = EpisodeRecording.model_validate(recorded).model_dump(
                mode="json"
            )
        environment = validated["conditions"].get("synthetic_environment")
        if environment is not None:
            validated["conditions"]["synthetic_environment"] = SyntheticEnvironment.model_validate(
                environment
            ).model_dump(mode="json")
        return json.loads(_document(validated))

    def validate_case_payload(self, payload: dict) -> dict:
        """Validate drafts without creating a case or interpreting old outcomes."""
        validated = self._case_payload(payload)
        recorded = validated.get("recorded_evidence") or {}
        for reference in recorded.get("evidence_refs", []):
            from rove.trials.evidence import validate_reference

            verified = validate_reference(self.trials, reference)
            if verified["kind"] in {"trajectory", "state"}:
                if (
                    verified.get("clock_id") != recorded.get("source_clock_id")
                    or verified.get("frame") != recorded.get("frame")
                    or verified.get("units") != recorded.get("units")
                ):
                    raise ValueError(
                        "Episode evidence reference clock/frame/units must match the recording"
                    )
                metadata = self.trials.asset_metadata(verified["asset_sha256"])
                if metadata["media_type"] != "application/json":
                    raise ValueError(
                        "Episode state/trajectory evidence requires a structured JSON recording"
                    )
                content = json.loads(self.trials.asset_path(verified["asset_sha256"]).read_bytes())
                if (
                    not isinstance(content, dict)
                    or content.get("source_clock_id") != recorded["source_clock_id"]
                    or content.get("frame") != recorded["frame"]
                    or content.get("units") != recorded["units"]
                    or not isinstance(content.get("records"), list)
                ):
                    raise ValueError(
                        "Managed recording schema/clock/frame/units do not match the episode"
                    )
        return validated

    def resolve_annotations(
        self, dataset: dict, case_revision_id: str, bindings: list[dict]
    ) -> dict:
        """Resolve exact frozen accepted review identities into endpoint-scoped inputs."""
        member = next(
            (
                m
                for m in dataset["members"]
                if m["case_revision_id"] == case_revision_id and m["disposition"] == "included"
            ),
            None,
        )
        if member is None:
            raise ValueError("Annotation binding requires included frozen case membership")
        if any(
            issue.get("case_revision_id") == case_revision_id
            and issue.get("code") == "case_annotation_disagreement"
            for issue in dataset.get("issues", [])
        ):
            raise ValueError("Frozen dataset contains unresolved annotation disagreement")
        reviews = [self.get_review(identity) for identity in member["review_ids"]]
        reviews = [
            r
            for r in reviews
            if r["target_type"] == "case_annotation"
            and r["status"] == "final"
            and r["decision"] == "accepted"
            and r["contract_id"] == dataset["contract_id"]
            and r["case_revision_id"] == case_revision_id
        ]
        result = {}
        for binding in bindings:
            values = [
                (r["annotations"][binding["annotation_key"]], r["id"])
                for r in reviews
                if binding["annotation_key"] in r["annotations"]
            ]
            if not values or len({canonical_json(v) for v, _ in values}) != 1:
                raise ValueError(
                    f"Missing or conflicting frozen annotation: {binding['annotation_key']}"
                )
            target = result.setdefault(binding["endpoint"], {"annotations": {}, "review_ids": []})
            target["annotations"][binding["target_key"]] = values[0][0]
            target["review_ids"] = sorted(
                set(target["review_ids"] + [identity for _, identity in values])
            )
        return result

    def import_case(
        self, payload: dict, image_sha256: str, *, operation_id: str | None = None
    ) -> dict:
        from rove.datasets.mutations import lookup, record

        request = {"payload": payload, "image_sha256": image_sha256}
        payload = self.validate_case_payload(payload)
        self._image(image_sha256)
        case_id, revision_id = uuid.uuid4().hex, uuid.uuid4().hex
        created = now()
        identity = content_hash({**payload, "image_sha256": image_sha256})
        with self.trials.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            reused = lookup(db, operation_id, "import_case", request)
            if reused:
                return self.get_case_revision(reused)
            db.execute("INSERT INTO cases VALUES (?,NULL,?)", (case_id, created))
            db.execute(
                "INSERT INTO case_revisions VALUES (?,?,1,NULL,?,?,?,?)",
                (revision_id, case_id, image_sha256, identity, _document(payload), created),
            )
            db.execute("UPDATE cases SET head_revision_id=? WHERE id=?", (revision_id, case_id))
            record(db, operation_id, "import_case", request, revision_id)
        return self.get_case_revision(revision_id)

    def revise_case(
        self,
        case_id: str,
        payload: dict,
        *,
        expected_head_revision_id: str,
        image_sha256: str | None = None,
        operation_id: str | None = None,
    ) -> dict:
        from rove.datasets.mutations import lookup, record

        request = {
            "case_id": case_id,
            "payload": payload,
            "expected_head_revision_id": expected_head_revision_id,
            "image_sha256": image_sha256,
        }
        with self.trials.connect() as db:
            reused = lookup(db, operation_id, "revise_case", request)
        if reused:
            return self.get_case_revision(reused)
        previous = self.get_case(case_id)
        payload = self.validate_case_payload(
            {"reference_data": previous.get("reference_data", {}), **payload}
        )
        digest = image_sha256 or previous["image_asset"]["sha256"]
        self._image(digest)
        revision_id = uuid.uuid4().hex
        with self.trials.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            reused = lookup(db, operation_id, "revise_case", request)
            if reused:
                return self.get_case_revision(reused)
            case = _required(db, "cases", case_id)
            if case["head_revision_id"] != expected_head_revision_id:
                raise ConflictError("Case changed; refresh before saving a revision")
            previous = _required(db, "case_revisions", case["head_revision_id"])
            db.execute(
                "INSERT INTO case_revisions VALUES (?,?,?,?,?,?,?,?)",
                (
                    revision_id,
                    case_id,
                    db.execute(
                        "SELECT coalesce(max(revision),0)+1 FROM case_revisions WHERE case_id=?",
                        (case_id,),
                    ).fetchone()[0],
                    previous["id"],
                    digest,
                    content_hash({**payload, "image_sha256": digest}),
                    _document(payload),
                    now(),
                ),
            )
            db.execute("UPDATE cases SET head_revision_id=? WHERE id=?", (revision_id, case_id))
            record(db, operation_id, "revise_case", request, revision_id)
        return self.get_case_revision(revision_id)

    def get_case(self, case_id: str) -> dict:
        with self.trials.connect() as db:
            case = _required(db, "cases", case_id)
        return self.get_case_revision(case["head_revision_id"])

    def validate_case_image(self, case_revision_id: str) -> dict:
        """Verify complete asset bytes immediately before launch or dispatch."""
        case = self.get_case_revision(case_revision_id)
        return self._image(case["image_asset"]["sha256"])

    def get_case_revision(self, identity: str) -> dict:
        with self.trials.connect() as db:
            row = _required(db, "case_revisions", identity)
            case = _required(db, "cases", row["case_id"])
            asset = db.execute(
                "SELECT * FROM assets WHERE sha256=?", (row["image_sha256"],)
            ).fetchone()
            final_reviews = db.execute(
                "SELECT count(*) FROM reviews WHERE case_revision_id=? AND status='final'",
                (identity,),
            ).fetchone()[0]
        try:
            asset_path = self.trials.asset_path(row["image_sha256"])
            available = asset_path.stat().st_size == asset["size_bytes"]
        except (KeyError, FileNotFoundError, ValueError):
            available = False
        payload = json.loads(row["payload"])
        return {
            **payload,
            "suggested_success": suggest_success(payload),
            "id": row["id"],
            "case_id": row["case_id"],
            "case_revision_id": row["id"],
            "revision": row["revision"],
            "parent_id": row["parent_id"],
            "head_revision_id": case["head_revision_id"],
            "image_asset": dict(asset),
            "sha256": row["sha256"],
            "created_at": row["created_at"],
            "readiness": {
                "can_run": available,
                "assessment": "reviewed" if final_reviews else "unreviewed",
                "final_review_count": final_reviews,
                "issues": [] if available else ["Image asset is unavailable or has changed"],
            },
        }

    def list_cases(self, limit: int = 50, offset: int = 0) -> list[dict]:
        self.trials._page(limit, offset)
        with self.trials.connect() as db:
            rows = db.execute(
                "SELECT head_revision_id FROM cases ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self.get_case_revision(row["head_revision_id"]) for row in rows]

    def search_cases(
        self, limit: int = 50, offset: int = 0, *, q: str = "", category: str = ""
    ) -> dict:
        """Search all current case revisions; facet counts precede the category filter."""
        self.trials._page(limit, offset)
        q, category = q.strip(), category.strip()
        if len(q) > 200 or len(category) > 160:
            raise ValueError("Case search or category is too long")
        with self.trials.connect() as db:
            categories = db.execute(
                """WITH heads AS (
                    SELECT CASE
                        WHEN json_type(r.payload,'$.candidate_context.eval_category')='text'
                            AND trim(json_extract(r.payload,'$.candidate_context.eval_category'))!=''
                        THEN trim(json_extract(r.payload,'$.candidate_context.eval_category'))
                        WHEN json_type(r.payload,'$.conditions.eval_category')='text'
                            AND trim(json_extract(r.payload,'$.conditions.eval_category'))!=''
                        THEN trim(json_extract(r.payload,'$.conditions.eval_category'))
                        ELSE 'uncategorized' END AS category
                    FROM cases c JOIN case_revisions r ON r.id=c.head_revision_id
                    WHERE instr(lower(json_extract(r.payload,'$.name') || ' ' ||
                                      json_extract(r.payload,'$.task')), lower(?)) > 0
                )
                SELECT category AS name,count(*) AS count FROM heads
                GROUP BY category ORDER BY category""",
                (q,),
            ).fetchall()
            total = sum(
                row["count"] for row in categories if not category or row["name"] == category
            )
            rows = db.execute(
                """WITH heads AS (
                    SELECT r.id,r.created_at,
                        CASE WHEN json_type(r.payload,'$.candidate_context.eval_category')='text'
                            AND trim(json_extract(r.payload,'$.candidate_context.eval_category'))!=''
                        THEN trim(json_extract(r.payload,'$.candidate_context.eval_category'))
                        WHEN json_type(r.payload,'$.conditions.eval_category')='text'
                            AND trim(json_extract(r.payload,'$.conditions.eval_category'))!=''
                        THEN trim(json_extract(r.payload,'$.conditions.eval_category'))
                        ELSE 'uncategorized' END AS category
                    FROM cases c JOIN case_revisions r ON r.id=c.head_revision_id
                    WHERE instr(lower(json_extract(r.payload,'$.name') || ' ' ||
                                      json_extract(r.payload,'$.task')), lower(?)) > 0
                )
                SELECT id FROM heads WHERE (?='' OR category=?)
                ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
                (q, category, category, limit, offset),
            ).fetchall()
        return {
            "cases": [self.get_case_revision(row["id"]) for row in rows],
            "total": total,
            "categories": [dict(row) for row in categories],
            "limit": limit,
            "offset": offset,
        }

    def create_contract(self, payload: dict) -> dict:
        payload = SuccessContract.model_validate(payload).model_dump(mode="json")
        payload = json.loads(_document(payload))
        identity = content_hash(payload)
        with self.trials.connect() as db:
            for revision_id in payload["case_expectations"]:
                _required(db, "case_revisions", revision_id)
            db.execute(
                "INSERT OR IGNORE INTO success_contracts VALUES (?,?,?,?)",
                (identity, identity, _document(payload), now()),
            )
        return self.get_contract(identity)

    def get_contract(self, identity: str) -> dict:
        with self.trials.connect() as db:
            row = _required(db, "success_contracts", identity)
        return {
            **json.loads(row["payload"]),
            "id": row["id"],
            "sha256": row["sha256"],
            "created_at": row["created_at"],
        }

    def list_contracts(self, limit: int = 50, offset: int = 0) -> list[dict]:
        self.trials._page(limit, offset)
        with self.trials.connect() as db:
            rows = db.execute(
                "SELECT id FROM success_contracts ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self.get_contract(row["id"]) for row in rows]

    @staticmethod
    def _review(row: sqlite3.Row) -> dict:
        return {
            **json.loads(row["payload"]),
            "id": row["id"],
            "revision": row["revision"],
            "output_sha256": row["output_sha256"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _validate_review(self, db, payload: dict) -> str | None:
        _required(db, "case_revisions", payload["case_revision_id"])
        contract = json.loads(_required(db, "success_contracts", payload["contract_id"])["payload"])
        criterion_ids = {criterion["id"] for criterion in contract["criteria"]}
        if payload["criteria"].keys() - criterion_ids:
            raise ValueError("Review contains criteria outside the frozen contract")
        if (
            payload["target_type"] == "trial_output"
            and payload["status"] == "final"
            and payload["decision"] == "accepted"
        ):
            required = {
                criterion["id"]
                for criterion in contract["criteria"]
                if criterion["required"] and criterion["assessment"] == "human_review"
            }
            if any(payload["criteria"].get(key) != "accepted" for key in required):
                raise ValueError(
                    "Accepted output reviews require all required human criteria accepted"
                )
        output_hash = None
        if payload["trial_id"]:
            trial = _required(db, "trials", payload["trial_id"])
            task = json.loads(trial["task"])
            if task.get("case_revision_id", task.get("id")) != payload["case_revision_id"]:
                raise ValueError("Trial does not belong to the selected case revision")
            if trial["status"] == "running" or trial["result"] is None:
                raise ValueError("Output review requires a finished trial with retained output")
            output_hash = content_hash(json.loads(trial["result"]))
        for reference in payload["evidence_refs"]:
            kind, separator, identity = reference.partition(":")
            valid = False
            if separator and kind == "asset":
                valid = (
                    db.execute("SELECT 1 FROM assets WHERE sha256=?", (identity,)).fetchone()
                    is not None
                )
            elif separator and kind == "case":
                valid = identity == payload["case_revision_id"]
            elif separator and kind == "trial":
                valid = identity == payload["trial_id"]
            elif separator and kind == "event" and payload["trial_id"]:
                valid = (
                    db.execute(
                        "SELECT 1 FROM events WHERE event_id=? AND trial_id=?",
                        (identity, payload["trial_id"]),
                    ).fetchone()
                    is not None
                )
            if not valid:
                raise ValueError(
                    "Evidence references must identify managed assets or the reviewed case/trial/events"
                )
        if payload["supersedes_id"]:
            previous = self._review(_required(db, "reviews", payload["supersedes_id"]))
            for field in ("case_revision_id", "trial_id", "target_type", "reviewer", "contract_id"):
                if previous[field] != payload[field]:
                    raise ValueError("A superseding review must retain its target and reviewer")
            if previous["status"] != "final":
                raise ValueError("Only a final review can be superseded")
            if (
                payload["status"] == "final"
                and db.execute(
                    "SELECT 1 FROM reviews WHERE supersedes_id=? AND status='final'",
                    (payload["supersedes_id"],),
                ).fetchone()
            ):
                raise ConflictError(
                    "This review already has a final successor; refresh its history"
                )
        return output_hash

    def create_review(self, payload: dict) -> dict:
        _document(payload)
        payload = ReviewInput.model_validate(payload).model_dump(mode="json")
        payload = json.loads(_document(payload))
        identity, created = uuid.uuid4().hex, now()
        with self.trials.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            output_hash = self._validate_review(db, payload)
            db.execute(
                "INSERT INTO reviews VALUES (?,?,?,?,?,?,1,?,?,?,?,?)",
                (
                    identity,
                    payload["case_revision_id"],
                    payload["trial_id"],
                    payload["contract_id"],
                    payload["target_type"],
                    payload["status"],
                    output_hash,
                    payload["supersedes_id"],
                    _document(payload),
                    created,
                    created,
                ),
            )
        return self.get_review(identity)

    def update_review(self, identity: str, payload: dict, *, expected_revision: int) -> dict:
        _document(payload)
        with self.trials.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = _required(db, "reviews", identity)
            original = json.loads(row["payload"])
            if row["revision"] != expected_revision or row["status"] == "final":
                raise ConflictError("Review changed or is final; create a revision instead")
            updated = ReviewInput.model_validate({**original, **payload}).model_dump(mode="json")
            for field in (
                "target_type",
                "case_revision_id",
                "trial_id",
                "contract_id",
                "reviewer",
                "supersedes_id",
            ):
                if updated[field] != original[field]:
                    raise ValueError("A review draft's target, rubric and attribution are fixed")
            updated = json.loads(_document(updated))
            output_hash = self._validate_review(db, updated)
            db.execute(
                """UPDATE reviews SET status=?,revision=revision+1,payload=?,updated_at=?,output_sha256=?
                    WHERE id=?""",
                (updated["status"], _document(updated), now(), output_hash, identity),
            )
        return self.get_review(identity)

    def get_review(self, identity: str) -> dict:
        with self.trials.connect() as db:
            return self._review(_required(db, "reviews", identity))

    def list_reviews(
        self,
        case_revision_id: str | None = None,
        trial_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        self.trials._page(limit, offset)
        with self.trials.connect() as db:
            rows = db.execute(
                """SELECT * FROM reviews WHERE (? IS NULL OR case_revision_id=?)
                    AND (? IS NULL OR trial_id=?) ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
                (case_revision_id, case_revision_id, trial_id, trial_id, limit, offset),
            ).fetchall()
        return [self._review(row) for row in rows]

    def _preview(self, db, payload: dict) -> dict:
        _required(db, "success_contracts", payload["contract_id"])
        if payload["parent_id"]:
            _required(db, "dataset_revisions", payload["parent_id"])
        issues, members = [], []
        included = excluded = fully_reviewed = 0
        seen_cases = set()
        for member in sorted(payload["members"], key=lambda item: item["case_revision_id"]):
            case = _required(db, "case_revisions", member["case_revision_id"])
            if case["case_id"] in seen_cases:
                raise ValueError("A dataset cannot include multiple revisions of one case")
            seen_cases.add(case["case_id"])
            reviews = []
            for identity in sorted(member["review_ids"]):
                review = self._review(_required(db, "reviews", identity))
                if (
                    review["case_revision_id"] != case["id"]
                    or review["contract_id"] != payload["contract_id"]
                ):
                    raise ValueError(
                        "Selected review does not match the case revision and contract"
                    )
                if review["status"] != "final":
                    raise ValueError("Only finalized reviews can be frozen")
                reviews.append(review)
            # A newer review cannot mutate an older frozen dataset. In this preview,
            # retain all selected viewpoints and expose any unresolved disagreement.
            final = [
                self._review(row)
                for row in db.execute(
                    "SELECT * FROM reviews WHERE case_revision_id=? AND contract_id=? AND status='final'",
                    (case["id"], payload["contract_id"]),
                ).fetchall()
            ]
            superseded = {review["supersedes_id"] for review in final if review["supersedes_id"]}
            active = [review for review in final if review["id"] not in superseded]
            valid = True
            for category in ("case_validity", "case_annotation"):
                chosen = [review for review in reviews if review["target_type"] == category]
                active_category = [review for review in active if review["target_type"] == category]
                decisions = {review["decision"] for review in active_category}
                if not chosen or any(review["decision"] != "accepted" for review in chosen):
                    valid = False
                    issues.append(
                        {"case_revision_id": case["id"], "code": f"{category}_incomplete"}
                    )
                annotation_variants = {
                    content_hash(review["annotations"])
                    for review in active_category
                    if category == "case_annotation" and review["decision"] == "accepted"
                }
                if len(decisions) > 1 or "unknown" in decisions or len(annotation_variants) > 1:
                    valid = False
                    issues.append(
                        {"case_revision_id": case["id"], "code": f"{category}_disagreement"}
                    )
                if any(review["id"] in superseded for review in chosen):
                    valid = False
                    issues.append(
                        {"case_revision_id": case["id"], "code": "superseded_review_selected"}
                    )
            if member["disposition"] == "included":
                included += 1
                fully_reviewed += int(valid)
            else:
                excluded += 1
            members.append(
                {
                    **member,
                    "review_ids": sorted(member["review_ids"]),
                    "case_sha256": case["sha256"],
                }
            )
        coverage = {
            "included": included,
            "excluded": excluded,
            "reviewed": fully_reviewed,
            "fraction": fully_reviewed / included,
        }
        identity = content_hash(
            {**payload, "members": members, "coverage": coverage, "issues": issues}
        )
        return {
            **payload,
            "members": members,
            "preview_hash": identity,
            "sha256": identity,
            "readiness": "reviewed" if fully_reviewed == included else "incomplete",
            "coverage": coverage,
            "issues": issues,
        }

    def preview_freeze(self, payload: dict) -> dict:
        payload = FreezeInput.model_validate(payload).model_dump(mode="json")
        _document(payload)
        with self.trials.connect() as db:
            db.execute("BEGIN")
            return self._preview(db, payload)

    def freeze(
        self,
        payload: dict,
        *,
        expected_preview_hash: str | None = None,
        operation_id: str | None = None,
    ) -> dict:
        from rove.datasets.mutations import lookup, record

        request = {"payload": payload, "expected_preview_hash": expected_preview_hash}
        payload = FreezeInput.model_validate(payload).model_dump(mode="json")
        _document(payload)
        identity = uuid.uuid4().hex
        with self.trials.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            reused = lookup(db, operation_id, "freeze_dataset", request)
            if reused:
                return self.get_dataset(reused)
            preview = self._preview(db, payload)
            if (
                expected_preview_hash is not None
                and preview["preview_hash"] != expected_preview_hash
            ):
                raise ConflictError("Dataset preview changed; preview again before freezing")
            db.execute(
                "INSERT INTO dataset_revisions VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    payload["parent_id"],
                    payload["contract_id"],
                    payload["name"],
                    preview["sha256"],
                    preview["readiness"],
                    canonical_json(preview["coverage"]),
                    canonical_json(preview["issues"]),
                    now(),
                ),
            )
            for member in preview["members"]:
                db.execute(
                    "INSERT INTO dataset_members VALUES (?,?,?,?)",
                    (identity, member["case_revision_id"], member["disposition"], member["reason"]),
                )
                for review_id in member["review_ids"]:
                    db.execute(
                        "INSERT INTO dataset_member_reviews VALUES (?,?,?)",
                        (identity, member["case_revision_id"], review_id),
                    )
            record(db, operation_id, "freeze_dataset", request, identity)
        return self.get_dataset(identity)

    def get_dataset(self, identity: str) -> dict:
        with self.trials.connect() as db:
            row = _required(db, "dataset_revisions", identity)
            members = []
            for member in db.execute(
                "SELECT * FROM dataset_members WHERE dataset_revision_id=? ORDER BY case_revision_id",
                (identity,),
            ).fetchall():
                reviews = db.execute(
                    """SELECT review_id FROM dataset_member_reviews
                        WHERE dataset_revision_id=? AND case_revision_id=? ORDER BY review_id""",
                    (identity, member["case_revision_id"]),
                ).fetchall()
                members.append(
                    {
                        "case_revision_id": member["case_revision_id"],
                        "disposition": member["disposition"],
                        "reason": member["reason"],
                        "review_ids": [review["review_id"] for review in reviews],
                    }
                )
        result = dict(row)
        result.update(
            coverage=json.loads(row["coverage"]),
            issues=json.loads(row["issues"]),
            members=members,
            preview_hash=row["sha256"],
        )
        return result

    def list_datasets(self, limit: int = 50, offset: int = 0) -> list[dict]:
        self.trials._page(limit, offset)
        with self.trials.connect() as db:
            rows = db.execute(
                "SELECT id FROM dataset_revisions ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self.get_dataset(row["id"]) for row in rows]
