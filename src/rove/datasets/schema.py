"""Transactional migration of the shared local database to schema version two."""


def migrate_v2(db):
    """Called only inside TrialStore's migration transaction."""
    for sql in (
        "CREATE INDEX trial_case_revision ON trials(json_extract(task, '$.case_revision_id'))",
        """CREATE TABLE success_contracts (
            id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
            created_at TEXT NOT NULL)""",
        """CREATE TABLE cases (
            id TEXT PRIMARY KEY, head_revision_id TEXT REFERENCES case_revisions(id),
            created_at TEXT NOT NULL)""",
        """CREATE TABLE case_revisions (
            id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
            revision INTEGER NOT NULL CHECK(revision > 0), parent_id TEXT REFERENCES case_revisions(id),
            image_sha256 TEXT NOT NULL REFERENCES assets(sha256), sha256 TEXT NOT NULL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(case_id, revision))""",
        "CREATE INDEX case_history ON case_revisions(case_id, revision DESC)",
        """CREATE TABLE reviews (
            id TEXT PRIMARY KEY, case_revision_id TEXT NOT NULL REFERENCES case_revisions(id),
            trial_id TEXT REFERENCES trials(id), contract_id TEXT NOT NULL REFERENCES success_contracts(id),
            target_type TEXT NOT NULL CHECK(target_type IN ('case_validity','case_annotation','trial_output')),
            status TEXT NOT NULL CHECK(status IN ('draft','final')),
            revision INTEGER NOT NULL CHECK(revision > 0), output_sha256 TEXT,
            supersedes_id TEXT REFERENCES reviews(id), payload TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            CHECK((target_type='trial_output' AND trial_id IS NOT NULL AND output_sha256 IS NOT NULL)
                OR (target_type!='trial_output' AND trial_id IS NULL AND output_sha256 IS NULL)))""",
        "CREATE INDEX case_reviews ON reviews(case_revision_id, created_at DESC, id)",
        "CREATE INDEX trial_reviews ON reviews(trial_id, created_at DESC, id)",
        """CREATE TABLE dataset_revisions (
            id TEXT PRIMARY KEY, parent_id TEXT REFERENCES dataset_revisions(id),
            contract_id TEXT NOT NULL REFERENCES success_contracts(id), name TEXT NOT NULL,
            sha256 TEXT NOT NULL, readiness TEXT NOT NULL CHECK(readiness IN ('reviewed','incomplete')),
            coverage TEXT NOT NULL, issues TEXT NOT NULL, created_at TEXT NOT NULL)""",
        """CREATE TABLE dataset_members (
            dataset_revision_id TEXT NOT NULL REFERENCES dataset_revisions(id),
            case_revision_id TEXT NOT NULL REFERENCES case_revisions(id),
            disposition TEXT NOT NULL CHECK(disposition IN ('included','excluded')),
            reason TEXT NOT NULL,
            PRIMARY KEY(dataset_revision_id, case_revision_id))""",
        """CREATE TABLE dataset_member_reviews (
            dataset_revision_id TEXT NOT NULL,
            case_revision_id TEXT NOT NULL,
            review_id TEXT NOT NULL REFERENCES reviews(id),
            PRIMARY KEY(dataset_revision_id, case_revision_id, review_id),
            FOREIGN KEY(dataset_revision_id,case_revision_id)
                REFERENCES dataset_members(dataset_revision_id,case_revision_id))""",
        """CREATE TABLE quick_promotions (
            operation_id TEXT PRIMARY KEY,
            trial_id TEXT NOT NULL REFERENCES trials(id),
            case_revision_id TEXT NOT NULL REFERENCES case_revisions(id),
            created_at TEXT NOT NULL)""",
        "CREATE INDEX promotion_trial ON quick_promotions(trial_id, created_at DESC)",
    ):
        db.execute(sql)
