-- Advolare application database (SQLite): studies, screening runs, decisions, audit log.
-- One table per stored model in src/models.py; columns match the model fields.
-- Read and written only through src/store.py.
--
-- Never store patient details here (age, sex, names, contacts). Patients are
-- referred to only by LocalPatientIdentifier (patient_id); their details stay in
-- the warehouse and are read when a page is displayed.
--
-- Columns that are not model fields exist only to link rows:
--   Criteria.study_id, Criteria.position (criterion order within the study),
--   PatientResults.run_id, and the integer id of Decisions and AuditLog.
-- Nested lists (assessments with verdicts and citations, reasons) are JSON text.
-- Date/times are ISO 8601 text.

PRAGMA foreign_keys = ON;

-- Study (criteria are in Criteria)
CREATE TABLE IF NOT EXISTS Studies (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    short_title         TEXT,
    condition           TEXT NOT NULL,
    study_design        TEXT NOT NULL,
    phase               TEXT,
    intervention        TEXT NOT NULL,
    primary_outcome     TEXT NOT NULL,
    target_sample_size  INTEGER NOT NULL CHECK (target_sample_size > 0),
    recruitment_start   TEXT NOT NULL,
    recruitment_end     TEXT NOT NULL,
    chief_investigator  TEXT NOT NULL,
    sponsor             TEXT NOT NULL,
    ethics_reference    TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('recruiting', 'closed'))
);

-- Criterion
CREATE TABLE IF NOT EXISTS Criteria (
    study_id            TEXT NOT NULL REFERENCES Studies(id),
    id                  TEXT NOT NULL,
    position            INTEGER NOT NULL,
    type                TEXT NOT NULL CHECK (type IN ('inclusion', 'exclusion')),
    description         TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('structured', 'notes')),
    field               TEXT,
    operator            TEXT CHECK (operator IN ('<', '<=', '==', '!=', '>=', '>')),
    value,              -- no declared type, so SQLite keeps numbers as numbers and text as text
    time_window_days    INTEGER CHECK (time_window_days > 0),
    PRIMARY KEY (study_id, id)
);

-- ScreeningRun (its patients are in PatientResults)
CREATE TABLE IF NOT EXISTS ScreeningRuns (
    run_id                TEXT PRIMARY KEY,
    study_id              TEXT NOT NULL REFERENCES Studies(id),
    status                TEXT NOT NULL CHECK (status IN ('running', 'complete', 'failed')),
    started_at            TEXT NOT NULL,
    finished_at           TEXT,
    candidates_after_sql  INTEGER NOT NULL DEFAULT 0,
    shortlisted_stage1    INTEGER NOT NULL DEFAULT 0,
    confirmed_stage2      INTEGER NOT NULL DEFAULT 0,
    likely_eligible       INTEGER NOT NULL DEFAULT 0,
    needs_review          INTEGER NOT NULL DEFAULT 0,
    excluded              INTEGER NOT NULL DEFAULT 0
);

-- PatientResult
CREATE TABLE IF NOT EXISTS PatientResults (
    run_id       TEXT NOT NULL REFERENCES ScreeningRuns(run_id),
    study_id     TEXT NOT NULL REFERENCES Studies(id),
    patient_id   TEXT NOT NULL,                -- LocalPatientIdentifier only
    shortlisted  INTEGER NOT NULL CHECK (shortlisted IN (0, 1)),
    assessments  TEXT NOT NULL DEFAULT '[]',   -- JSON list of CriterionAssessment (verdicts, citations, flags)
    status       TEXT NOT NULL CHECK (status IN ('likely_eligible', 'needs_review', 'excluded')),
    reasons      TEXT NOT NULL DEFAULT '[]',   -- JSON list of strings
    approved     INTEGER NOT NULL CHECK (approved IN (0, 1)),
    PRIMARY KEY (run_id, patient_id)
);

-- Decision. History is kept: the latest row per (run_id, patient_id) is the current decision.
CREATE TABLE IF NOT EXISTS Decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  TEXT NOT NULL,
    run_id      TEXT NOT NULL REFERENCES ScreeningRuns(run_id),
    decision    TEXT NOT NULL CHECK (decision IN ('pending', 'approved', 'rejected')),
    note        TEXT NOT NULL DEFAULT '',
    who         TEXT NOT NULL,
    "when"      TEXT NOT NULL,                  -- quoted: WHEN is an SQL keyword
    FOREIGN KEY (run_id, patient_id) REFERENCES PatientResults(run_id, patient_id)
);

-- AuditEntry. Append-only: the triggers below refuse updates and deletes.
CREATE TABLE IF NOT EXISTS AuditLog (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    "when"      TEXT NOT NULL,
    study_id    TEXT NOT NULL,
    who         TEXT NOT NULL,
    action      TEXT NOT NULL,
    patient_id  TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON AuditLog
BEGIN
    SELECT RAISE(ABORT, 'AuditLog is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON AuditLog
BEGIN
    SELECT RAISE(ABORT, 'AuditLog is append-only');
END;

CREATE INDEX IF NOT EXISTS idx_criteria_study       ON Criteria(study_id, position);
CREATE INDEX IF NOT EXISTS idx_runs_study           ON ScreeningRuns(study_id, started_at);
CREATE INDEX IF NOT EXISTS idx_decisions_run        ON Decisions(run_id, patient_id);
CREATE INDEX IF NOT EXISTS idx_audit_study          ON AuditLog(study_id);
