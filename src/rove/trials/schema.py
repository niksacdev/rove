"""Transactional additions for managed evidence and workflow lineage."""


def migrate_v3(db):
    db.execute("""CREATE TABLE evidence_refs (
        trial_id TEXT NOT NULL REFERENCES trials(id),
        id TEXT NOT NULL, asset_sha256 TEXT NOT NULL REFERENCES assets(sha256),
        payload TEXT NOT NULL, PRIMARY KEY(trial_id,id))""")
    db.execute("CREATE INDEX evidence_asset ON evidence_refs(asset_sha256)")
    from rove.benchmarks.baselines import migrate_local_workflow

    migrate_local_workflow(db)
