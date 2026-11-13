-- Advolare mock data warehouse (SQLite).
-- All data is synthetic. Do not change this file without explicit approval (see AGENTS.md).
--
-- Join keys:
--   LocalPatientIdentifier (MRN) - one per distinct patient; joins patient-level tables.
--   EncounterID                  - one per inpatient episode or ED attendance; joins event-level tables.
--                                  An encounter lives in either Inpatients or ECDS, so EncounterID
--                                  has no foreign key constraint.
--
-- Every row in every table has a unique, citable ID (the PRIMARY KEY).
-- Date/times are ISO 8601 text: 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS Patients (
    LocalPatientIdentifier  TEXT PRIMARY KEY,
    DateOfBirth             TEXT NOT NULL,
    Sex                     TEXT NOT NULL,
    EthnicCategory          TEXT,
    PostcodeDistrict        TEXT
);

-- Never passed to any LLM and never exposed by any agent tool.
-- Joined only after clinician approval.
CREATE TABLE IF NOT EXISTS PatientContacts (
    LocalPatientIdentifier  TEXT PRIMARY KEY REFERENCES Patients(LocalPatientIdentifier),
    MobileNumber            TEXT,   -- Ofcom drama range 07700 900000-900999 only
    Landline                TEXT,
    Email                   TEXT,   -- @example.com only
    GuardianName            TEXT,   -- populated for under-16s
    GuardianPhone           TEXT
);

CREATE TABLE IF NOT EXISTS Inpatients (
    EncounterID             TEXT PRIMARY KEY,
    LocalPatientIdentifier  TEXT NOT NULL REFERENCES Patients(LocalPatientIdentifier),
    AdmissionDateTime       TEXT NOT NULL,
    DischargeDateTime       TEXT,
    AdmissionMethod         TEXT,
    Specialty               TEXT,
    Ward                    TEXT
);

CREATE TABLE IF NOT EXISTS ECDS (
    EncounterID             TEXT PRIMARY KEY,
    LocalPatientIdentifier  TEXT NOT NULL REFERENCES Patients(LocalPatientIdentifier),
    CheckInDateTime         TEXT NOT NULL,
    Acuity                  INTEGER CHECK (Acuity BETWEEN 1 AND 5),
    ChiefComplaint          TEXT,
    DischargeDestination    TEXT
);

CREATE TABLE IF NOT EXISTS Observations (
    ObservationID           TEXT PRIMARY KEY,
    EncounterID             TEXT NOT NULL,
    LocalPatientIdentifier  TEXT NOT NULL REFERENCES Patients(LocalPatientIdentifier),
    RecordedDateTime        TEXT NOT NULL,
    RespRate                INTEGER,
    SpO2                    INTEGER,
    OnOxygen                INTEGER CHECK (OnOxygen IN (0, 1)),
    Temperature             REAL,
    SystolicBP              INTEGER,
    DiastolicBP             INTEGER,
    HeartRate               INTEGER,
    Consciousness           TEXT CHECK (Consciousness IN ('A', 'C', 'V', 'P', 'U'))  -- ACVPU
);

CREATE TABLE IF NOT EXISTS Pathology (
    PathologyID             TEXT PRIMARY KEY,
    EncounterID             TEXT NOT NULL,
    LocalPatientIdentifier  TEXT NOT NULL REFERENCES Patients(LocalPatientIdentifier),
    TestCode                TEXT NOT NULL,
    TestName                TEXT NOT NULL,
    Value                   REAL,
    Units                   TEXT,
    RefLow                  REAL,
    RefHigh                 REAL,
    CollectedDateTime       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS Diagnoses (
    DiagnosisID             TEXT PRIMARY KEY,
    EncounterID             TEXT NOT NULL,
    LocalPatientIdentifier  TEXT NOT NULL REFERENCES Patients(LocalPatientIdentifier),
    ICD10Code               TEXT NOT NULL,
    Description             TEXT,
    Position                TEXT NOT NULL CHECK (Position IN ('primary', 'secondary'))
);

CREATE TABLE IF NOT EXISTS Procedures (
    ProcedureID             TEXT PRIMARY KEY,
    EncounterID             TEXT NOT NULL,
    LocalPatientIdentifier  TEXT NOT NULL REFERENCES Patients(LocalPatientIdentifier),
    OPCSCode                TEXT NOT NULL,
    Description             TEXT,
    ProcedureDateTime       TEXT
);

CREATE TABLE IF NOT EXISTS ClinicalNotes (
    NoteID                  TEXT PRIMARY KEY,
    EncounterID             TEXT NOT NULL,
    LocalPatientIdentifier  TEXT NOT NULL REFERENCES Patients(LocalPatientIdentifier),
    NoteType                TEXT NOT NULL
                            CHECK (NoteType IN ('triage', 'clerking', 'ward round', 'nursing', 'discharge')),
    AuthorRole              TEXT,
    CreatedDateTime         TEXT NOT NULL,
    NoteText                TEXT NOT NULL
);

-- Indexes for the common lookups (by patient, by encounter).
CREATE INDEX IF NOT EXISTS idx_inpatients_patient    ON Inpatients(LocalPatientIdentifier);
CREATE INDEX IF NOT EXISTS idx_ecds_patient          ON ECDS(LocalPatientIdentifier);
CREATE INDEX IF NOT EXISTS idx_observations_patient  ON Observations(LocalPatientIdentifier);
CREATE INDEX IF NOT EXISTS idx_observations_encounter ON Observations(EncounterID);
CREATE INDEX IF NOT EXISTS idx_pathology_patient     ON Pathology(LocalPatientIdentifier, TestCode);
CREATE INDEX IF NOT EXISTS idx_pathology_encounter   ON Pathology(EncounterID);
CREATE INDEX IF NOT EXISTS idx_diagnoses_patient     ON Diagnoses(LocalPatientIdentifier, ICD10Code);
CREATE INDEX IF NOT EXISTS idx_diagnoses_encounter   ON Diagnoses(EncounterID);
CREATE INDEX IF NOT EXISTS idx_procedures_patient    ON Procedures(LocalPatientIdentifier);
CREATE INDEX IF NOT EXISTS idx_notes_patient         ON ClinicalNotes(LocalPatientIdentifier);
CREATE INDEX IF NOT EXISTS idx_notes_encounter       ON ClinicalNotes(EncounterID);
