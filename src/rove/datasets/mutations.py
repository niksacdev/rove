"""Small transaction-local idempotency records for confirmed workflow writes."""

from rove.datasets.service import ConflictError
from rove.trials.snapshots import content_hash
from rove.trials.store import now


def lookup(db, operation_id, kind, request):
    if operation_id is None:
        return None
    if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
        raise ValueError("A bounded operation_id is required")
    row = db.execute(
        "SELECT * FROM workflow_mutations WHERE operation_id=?", (operation_id,)
    ).fetchone()
    if row:
        if row["kind"] != kind or row["request_hash"] != content_hash(request):
            raise ConflictError("Operation identity already belongs to a different mutation")
        return row["result_id"]
    return None


def record(db, operation_id, kind, request, result_id):
    if operation_id is not None:
        db.execute(
            "INSERT INTO workflow_mutations VALUES (?,?,?,?,?)",
            (operation_id, kind, content_hash(request), result_id, now()),
        )
