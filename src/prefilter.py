"""Cheap, reliable first cut: structured criteria -> parameterised SQL -> candidate list.

Code only, no LLM. Takes a Study and returns the patients who pass every
structured criterion, with a Citation for each criterion they met, so the
validation layer can check structured evidence the same way as notes evidence.
Notes criteria are ignored here; the LLM stages handle them.

What is screened
    Admission episodes up to config.REFERENCE_NOW. An episode is one row of
    Inpatients plus the ED attendance that led to it (same patient, ED
    disposition "Admitted", checked in up to 24 hours before admission), because
    the admission obs and bloods are recorded in ED. A patient passes if any of
    their episodes passes; the most recent passing episode is the qualifying
    encounter.

How criteria combine
    Structured criteria are applied in study order. An inclusion keeps only the
    episodes that meet it; an exclusion that is met removes the episode. A patient
    is counted against the criterion that removed their last remaining episode,
    which gives the front-end funnel. A criterion that cannot be evaluated (for
    example no urea for CURB-65) counts as not met.

Supported fields (anything else raises PrefilterError)
    age                     years at REFERENCE_NOW, from Patients.DateOfBirth     numeric
    primary_diagnosis       ICD-10 prefix of the episode's primary diagnosis       == / !=
    admission_method        "emergency" or "elective"                              == / !=
    admitted_within_hours   hours from admission to REFERENCE_NOW                  numeric
    currently_admitted      yes / no (no discharge time yet)                       == / !=
    curb65, news2           calculated by src/scores.py from the episode's first
                            obs set (CURB-65 also uses the first urea and age)     numeric
    egfr, ntprobnp, urea, crp, creatinine, sodium, potassium, haemoglobin, wcc,
    lactate, troponin, glucose
                            lab value; with time_window_days: any result in the
                            window, otherwise the episode's first (admission)
                            result                                                  numeric
    systolic_bp             as for labs, from Observations                         numeric
    diagnosis_code          any diagnosis on record with this ICD-10 prefix        == / !=
    procedure_code          any procedure with this OPCS-4 prefix, optionally in
                            the time window                                         == / !=

Safety
    Criterion values only ever reach SQL as bound parameters. Field names and
    operators are looked up in fixed tables; nothing from a criterion is pasted
    into SQL text. Code prefixes are matched with substr(), so "%" and "_" in a
    value are ordinary characters, not wildcards.

Citations
    Each citation names a warehouse row and the value found there. For curb65
    and news2 the field is "CURB65" or "NEWS2" on the Observations row the score
    was calculated from (CURB-65 also cites the urea row and DateOfBirth), so the
    validation layer recomputes the score with src/scores.py instead of reading a
    column. Criteria met by absence (operator "!=" on a code) cite nothing.
"""

from __future__ import annotations

import operator as py_operator
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

import config
from src.models import Citation, Criterion, CriterionKind, CriterionType, Operator, Study
from src.scores import NEW_CONFUSION, compute_curb65, compute_news2


class PrefilterError(ValueError):
    """A structured criterion the prefilter cannot turn into a query."""


class Candidate(BaseModel):
    patient_id: str  # LocalPatientIdentifier
    encounter_id: str  # qualifying Inpatients.EncounterID
    citations: dict[str, list[Citation]] = Field(default_factory=dict)  # criterion id -> evidence


class PrefilterResult(BaseModel):
    study_id: str | None
    reference_time: datetime
    patients_considered: int  # patients with at least one admission up to reference_time
    candidates: list[Candidate] = Field(default_factory=list)
    removed_by_criterion: dict[str, int] = Field(default_factory=dict)  # in criterion order


# ---------------------------------------------------------------------------
# Whitelists
# ---------------------------------------------------------------------------

COMPARE: dict[Operator, Callable[[float, float], bool]] = {
    Operator.lt: py_operator.lt,
    Operator.le: py_operator.le,
    Operator.eq: py_operator.eq,
    Operator.ne: py_operator.ne,
    Operator.ge: py_operator.ge,
    Operator.gt: py_operator.gt,
}
NUMERIC_OPERATORS = set(Operator)
EQUALITY_OPERATORS = {Operator.eq, Operator.ne}

# Criterion field -> Pathology.TestCode
LAB_TESTS = {
    "egfr": "EGFR",
    "ntprobnp": "NTBNP",
    "urea": "UREA",
    "crp": "CRP",
    "creatinine": "CREA",
    "sodium": "NA",
    "potassium": "K",
    "haemoglobin": "HB",
    "wcc": "WBC",
    "lactate": "LAC",
    "troponin": "TNT",
    "glucose": "GLU",
}
ADMISSION_METHODS = {"emergency", "elective"}
TRUE_WORDS, FALSE_WORDS = {"yes", "true", "1", "1.0"}, {"no", "false", "0", "0.0"}


# ---------------------------------------------------------------------------
# Warehouse access
# ---------------------------------------------------------------------------


@dataclass
class Episode:
    encounter_id: str  # Inpatients
    patient_id: str
    admitted: str  # ISO date/time
    discharged: str | None
    admission_method: str
    ed_id: str | None  # the ED attendance that led to this admission, if any

    @property
    def encounters(self) -> tuple[str, ...]:
        return (self.ed_id, self.encounter_id) if self.ed_id else (self.encounter_id,)


EPISODES_SQL = """
SELECT i.EncounterID, i.LocalPatientIdentifier, i.AdmissionDateTime, i.DischargeDateTime, i.AdmissionMethod,
       (SELECT e.EncounterID FROM ECDS e
         WHERE e.LocalPatientIdentifier = i.LocalPatientIdentifier
           AND e.DischargeDestination = 'Admitted'
           AND e.CheckInDateTime <= i.AdmissionDateTime
           AND e.CheckInDateTime >= strftime('%Y-%m-%dT%H:%M:%S', i.AdmissionDateTime, '-24 hours')
         ORDER BY e.CheckInDateTime DESC LIMIT 1) AS EDEncounterID
FROM Inpatients i
WHERE i.AdmissionDateTime <= ?
ORDER BY i.LocalPatientIdentifier, i.AdmissionDateTime
"""


@dataclass
class Warehouse:
    """Read-only queries against the warehouse at a fixed reference time, with small caches."""

    conn: sqlite3.Connection
    now: datetime
    _dob: dict[str, str] | None = None
    _first_obs: dict[str, sqlite3.Row] | None = None
    _first_lab: dict[str, dict[str, sqlite3.Row]] = field(default_factory=dict)

    @property
    def now_iso(self) -> str:
        return self.now.strftime("%Y-%m-%dT%H:%M:%S")

    def since_iso(self, days: int) -> str:
        return (self.now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def episodes(self) -> list[Episode]:
        return [
            Episode(r["EncounterID"], r["LocalPatientIdentifier"], r["AdmissionDateTime"],
                    r["DischargeDateTime"], r["AdmissionMethod"] or "", r["EDEncounterID"])
            for r in self.query(EPISODES_SQL, (self.now_iso,))
        ]

    def dob(self, patient_id: str) -> str | None:
        if self._dob is None:
            self._dob = {r[0]: r[1] for r in self.query("SELECT LocalPatientIdentifier, DateOfBirth FROM Patients")}
        return self._dob.get(patient_id)

    def first_obs(self, ep: Episode) -> sqlite3.Row | None:
        """The earliest observation set of the episode (ED or ward) up to now."""
        if self._first_obs is None:
            self._first_obs = {}
            rows = self.query(
                "SELECT * FROM Observations WHERE RecordedDateTime <= ? ORDER BY RecordedDateTime, ObservationID",
                (self.now_iso,),
            )
            for r in rows:
                self._first_obs.setdefault(r["EncounterID"], r)
        found = [self._first_obs[e] for e in ep.encounters if e in self._first_obs]
        return min(found, key=lambda r: r["RecordedDateTime"]) if found else None

    def first_lab(self, ep: Episode, test_code: str) -> sqlite3.Row | None:
        """The episode's first result for one test (its admission bloods) up to now."""
        if test_code not in self._first_lab:
            first: dict[str, sqlite3.Row] = {}
            rows = self.query(
                "SELECT PathologyID, EncounterID, Value, CollectedDateTime FROM Pathology "
                "WHERE TestCode = ? AND CollectedDateTime <= ? AND Value IS NOT NULL "
                "ORDER BY CollectedDateTime, PathologyID",
                (test_code, self.now_iso),
            )
            for r in rows:
                first.setdefault(r["EncounterID"], r)
            self._first_lab[test_code] = first
        found = [self._first_lab[test_code][e] for e in ep.encounters if e in self._first_lab[test_code]]
        return min(found, key=lambda r: r["CollectedDateTime"]) if found else None


def age_on(dob: date, day: date) -> int:
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


def fmt(value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


# ---------------------------------------------------------------------------
# Field evaluators
#
# Each takes the warehouse, the criterion, its parsed value and the episodes
# still in play, and returns {episode id: citations} for the episodes where the
# criterion's condition is TRUE.
# ---------------------------------------------------------------------------

Met = dict[str, list[Citation]]


def _age(wh: Warehouse, c: Criterion, value: float, episodes: list[Episode]) -> Met:
    met: Met = {}
    for ep in episodes:
        dob = wh.dob(ep.patient_id)
        if dob and COMPARE[c.operator](age_on(date.fromisoformat(dob), wh.now.date()), value):
            met[ep.encounter_id] = [Citation(table="Patients", record_id=ep.patient_id, field="DateOfBirth", quote=dob)]
    return met


def _code_rows(wh: Warehouse, table: str, id_col: str, code_col: str, key_col: str, prefix: str,
               extra_sql: str = "", extra_params: tuple = ()) -> dict[str, sqlite3.Row]:
    """Rows whose code starts with `prefix`, first per key. Table and column names come from the callers only."""
    rows = wh.query(
        f"SELECT {id_col} AS id, {key_col} AS key, {code_col} AS code FROM {table} "
        f"WHERE substr({code_col}, 1, length(?)) = ? {extra_sql} ORDER BY {id_col}",
        (prefix, prefix) + extra_params,
    )
    found: dict[str, sqlite3.Row] = {}
    for r in rows:
        found.setdefault(r["key"], r)
    return found


def _primary_diagnosis(wh: Warehouse, c: Criterion, prefix: str, episodes: list[Episode]) -> Met:
    matches = _code_rows(wh, "Diagnoses", "DiagnosisID", "ICD10Code", "EncounterID", prefix,
                         "AND Position = 'primary'")
    met: Met = {}
    for ep in episodes:
        row = matches.get(ep.encounter_id)
        if c.operator == Operator.eq and row:
            met[ep.encounter_id] = [Citation(table="Diagnoses", record_id=row["id"], field="ICD10Code", quote=row["code"])]
        elif c.operator == Operator.ne and not row:
            met[ep.encounter_id] = []
    return met


def _patient_code(table: str, id_col: str, code_col: str, time_col: str | None):
    """diagnosis_code / procedure_code: any matching row for the patient."""

    def evaluate(wh: Warehouse, c: Criterion, prefix: str, episodes: list[Episode]) -> Met:
        extra_sql, extra_params = "", ()
        if time_col:
            extra_sql = f"AND {time_col} <= ?"
            extra_params = (wh.now_iso,)
            if c.time_window_days:
                extra_sql += f" AND {time_col} > ?"
                extra_params += (wh.since_iso(c.time_window_days),)
        matches = _code_rows(wh, table, id_col, code_col, "LocalPatientIdentifier", prefix, extra_sql, extra_params)
        met: Met = {}
        for ep in episodes:
            row = matches.get(ep.patient_id)
            if c.operator == Operator.eq and row:
                met[ep.encounter_id] = [Citation(table=table, record_id=row["id"], field=code_col, quote=row["code"])]
            elif c.operator == Operator.ne and not row:
                met[ep.encounter_id] = []
        return met

    return evaluate


def _admission_method(wh: Warehouse, c: Criterion, method: str, episodes: list[Episode]) -> Met:
    rows = wh.query(
        "SELECT EncounterID FROM Inpatients WHERE lower(substr(AdmissionMethod, 1, length(?))) = ?",
        (method, method),
    )
    matching = {r["EncounterID"] for r in rows}
    met: Met = {}
    for ep in episodes:
        if (ep.encounter_id in matching) == (c.operator == Operator.eq):
            met[ep.encounter_id] = [Citation(table="Inpatients", record_id=ep.encounter_id,
                                             field="AdmissionMethod", quote=ep.admission_method)]
    return met


def _admitted_within_hours(wh: Warehouse, c: Criterion, value: float, episodes: list[Episode]) -> Met:
    met: Met = {}
    for ep in episodes:
        hours = (wh.now - datetime.fromisoformat(ep.admitted)).total_seconds() / 3600
        if COMPARE[c.operator](hours, value):
            met[ep.encounter_id] = [Citation(table="Inpatients", record_id=ep.encounter_id,
                                             field="AdmissionDateTime", quote=ep.admitted)]
    return met


def _currently_admitted(wh: Warehouse, c: Criterion, wanted: bool, episodes: list[Episode]) -> Met:
    met: Met = {}
    for ep in episodes:
        current = ep.discharged is None or ep.discharged > wh.now_iso
        if (current == wanted) == (c.operator == Operator.eq):
            # quote "" stands for a NULL DischargeDateTime
            met[ep.encounter_id] = [Citation(table="Inpatients", record_id=ep.encounter_id,
                                             field="DischargeDateTime", quote=ep.discharged or "")]
    return met


def _curb65(wh: Warehouse, c: Criterion, value: float, episodes: list[Episode]) -> Met:
    met: Met = {}
    for ep in episodes:
        obs, urea, dob = wh.first_obs(ep), wh.first_lab(ep, "UREA"), wh.dob(ep.patient_id)
        if obs is None or urea is None or dob is None:
            continue  # cannot be calculated, so not met
        age = age_on(date.fromisoformat(dob), datetime.fromisoformat(obs["RecordedDateTime"]).date())
        score = compute_curb65(
            confusion=obs["Consciousness"] in NEW_CONFUSION, urea=urea["Value"], resp_rate=obs["RespRate"],
            systolic_bp=obs["SystolicBP"], diastolic_bp=obs["DiastolicBP"], age=age,
        )
        if COMPARE[c.operator](score, value):
            met[ep.encounter_id] = [
                Citation(table="Observations", record_id=obs["ObservationID"], field="CURB65", quote=str(score)),
                Citation(table="Pathology", record_id=urea["PathologyID"], field="Value", quote=fmt(urea["Value"])),
                Citation(table="Patients", record_id=ep.patient_id, field="DateOfBirth", quote=dob),
            ]
    return met


def _news2(wh: Warehouse, c: Criterion, value: float, episodes: list[Episode]) -> Met:
    met: Met = {}
    for ep in episodes:
        obs = wh.first_obs(ep)
        if obs is None:
            continue
        score = compute_news2(
            resp_rate=obs["RespRate"], spo2=obs["SpO2"], on_oxygen=bool(obs["OnOxygen"]),
            temperature=obs["Temperature"], systolic_bp=obs["SystolicBP"], heart_rate=obs["HeartRate"],
            consciousness=obs["Consciousness"],
        )  # scale 1: the warehouse does not record a prescribed 88-92% target
        if COMPARE[c.operator](score, value):
            met[ep.encounter_id] = [Citation(table="Observations", record_id=obs["ObservationID"],
                                             field="NEWS2", quote=str(score))]
    return met


def _lab(test_code: str):
    def evaluate(wh: Warehouse, c: Criterion, value: float, episodes: list[Episode]) -> Met:
        met: Met = {}
        compare = COMPARE[c.operator]
        if c.time_window_days:
            rows = wh.query(
                "SELECT PathologyID, LocalPatientIdentifier, Value FROM Pathology "
                "WHERE TestCode = ? AND CollectedDateTime > ? AND CollectedDateTime <= ? AND Value IS NOT NULL "
                "ORDER BY CollectedDateTime, PathologyID",
                (test_code, wh.since_iso(c.time_window_days), wh.now_iso),
            )
            latest: dict[str, sqlite3.Row] = {}
            for r in rows:
                if compare(r["Value"], value):
                    latest[r["LocalPatientIdentifier"]] = r  # most recent matching result
            for ep in episodes:
                if ep.patient_id in latest:
                    r = latest[ep.patient_id]
                    met[ep.encounter_id] = [Citation(table="Pathology", record_id=r["PathologyID"],
                                                     field="Value", quote=fmt(r["Value"]))]
        else:
            for ep in episodes:
                r = wh.first_lab(ep, test_code)
                if r is not None and compare(r["Value"], value):
                    met[ep.encounter_id] = [Citation(table="Pathology", record_id=r["PathologyID"],
                                                     field="Value", quote=fmt(r["Value"]))]
        return met

    return evaluate


def _systolic_bp(wh: Warehouse, c: Criterion, value: float, episodes: list[Episode]) -> Met:
    met: Met = {}
    compare = COMPARE[c.operator]
    if c.time_window_days:
        rows = wh.query(
            "SELECT ObservationID, LocalPatientIdentifier, SystolicBP FROM Observations "
            "WHERE RecordedDateTime > ? AND RecordedDateTime <= ? AND SystolicBP IS NOT NULL "
            "ORDER BY RecordedDateTime, ObservationID",
            (wh.since_iso(c.time_window_days), wh.now_iso),
        )
        latest: dict[str, sqlite3.Row] = {}
        for r in rows:
            if compare(r["SystolicBP"], value):
                latest[r["LocalPatientIdentifier"]] = r
        for ep in episodes:
            if ep.patient_id in latest:
                r = latest[ep.patient_id]
                met[ep.encounter_id] = [Citation(table="Observations", record_id=r["ObservationID"],
                                                 field="SystolicBP", quote=str(r["SystolicBP"]))]
    else:
        for ep in episodes:
            obs = wh.first_obs(ep)
            if obs is not None and compare(obs["SystolicBP"], value):
                met[ep.encounter_id] = [Citation(table="Observations", record_id=obs["ObservationID"],
                                                 field="SystolicBP", quote=str(obs["SystolicBP"]))]
    return met


@dataclass(frozen=True)
class FieldSpec:
    evaluate: Callable[[Warehouse, Criterion, object, list[Episode]], Met]
    value_kind: str  # "number", "code", "method" or "flag"
    operators: set[Operator]
    window: bool  # whether time_window_days is allowed


FIELDS: dict[str, FieldSpec] = {
    "age": FieldSpec(_age, "number", NUMERIC_OPERATORS, window=False),
    "primary_diagnosis": FieldSpec(_primary_diagnosis, "code", EQUALITY_OPERATORS, window=False),
    "admission_method": FieldSpec(_admission_method, "method", EQUALITY_OPERATORS, window=False),
    "admitted_within_hours": FieldSpec(_admitted_within_hours, "number", NUMERIC_OPERATORS, window=False),
    "currently_admitted": FieldSpec(_currently_admitted, "flag", EQUALITY_OPERATORS, window=False),
    "curb65": FieldSpec(_curb65, "number", NUMERIC_OPERATORS, window=False),
    "news2": FieldSpec(_news2, "number", NUMERIC_OPERATORS, window=False),
    "systolic_bp": FieldSpec(_systolic_bp, "number", NUMERIC_OPERATORS, window=True),
    "diagnosis_code": FieldSpec(_patient_code("Diagnoses", "DiagnosisID", "ICD10Code", None),
                                "code", EQUALITY_OPERATORS, window=False),
    "procedure_code": FieldSpec(_patient_code("Procedures", "ProcedureID", "OPCSCode", "ProcedureDateTime"),
                                "code", EQUALITY_OPERATORS, window=True),
    **{name: FieldSpec(_lab(code), "number", NUMERIC_OPERATORS, window=True) for name, code in LAB_TESTS.items()},
}
SUPPORTED_FIELDS = sorted(FIELDS)


# ---------------------------------------------------------------------------
# Checking criteria
# ---------------------------------------------------------------------------


def criterion_key(c: Criterion, position: int) -> str:
    return c.id or f"C{position + 1}"


def parse_value(c: Criterion, key: str):
    """Check a structured criterion against the whitelist and return its parsed value."""
    name = (c.field or "").strip().lower()
    spec = FIELDS.get(name)
    if spec is None:
        raise PrefilterError(
            f"Criterion {key}: unsupported field '{c.field}'. Supported fields: {', '.join(SUPPORTED_FIELDS)}."
        )
    if c.operator not in spec.operators:
        allowed = ", ".join(sorted(o.value for o in spec.operators))
        raise PrefilterError(f"Criterion {key}: operator '{c.operator.value}' is not allowed for {name} (use {allowed}).")
    if c.time_window_days and not spec.window:
        raise PrefilterError(f"Criterion {key}: {name} does not take a time window.")

    raw = c.value
    if spec.value_kind == "number":
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise PrefilterError(f"Criterion {key}: {name} needs a number, got {raw!r}.") from None
    text = str(raw).strip()
    if spec.value_kind == "code":
        if not text:
            raise PrefilterError(f"Criterion {key}: {name} needs a code prefix such as 'J18'.")
        return text.upper()
    if spec.value_kind == "method":
        if text.lower() not in ADMISSION_METHODS:
            raise PrefilterError(f"Criterion {key}: admission_method must be 'emergency' or 'elective', got {raw!r}.")
        return text.lower()
    if text.lower() in TRUE_WORDS:
        return True
    if text.lower() in FALSE_WORDS:
        return False
    raise PrefilterError(f"Criterion {key}: {name} needs yes or no, got {raw!r}.")


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def connect_readonly(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_PATH)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; build it with: python data/generate.py")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def run_prefilter(study: Study, conn: sqlite3.Connection | None = None, now: datetime | None = None) -> PrefilterResult:
    """Apply the study's structured criteria. Raises PrefilterError before any query if one is unsupported."""
    structured = [
        (criterion_key(c, i), c) for i, c in enumerate(study.criteria) if c.kind == CriterionKind.structured
    ]
    parsed = [(key, c, parse_value(c, key)) for key, c in structured]  # all checks first

    own_conn = conn is None
    conn = conn or connect_readonly()
    conn.row_factory = sqlite3.Row
    try:
        wh = Warehouse(conn, now or config.REFERENCE_NOW)
        episodes = wh.episodes()
        alive = {ep.encounter_id: ep for ep in episodes}
        evidence: dict[str, dict[str, list[Citation]]] = defaultdict(dict)
        removed: dict[str, int] = {}

        for key, c, value in parsed:
            before = {ep.patient_id for ep in alive.values()}
            met = FIELDS[c.field.strip().lower()].evaluate(wh, c, value, list(alive.values()))
            if c.type == CriterionType.inclusion:
                alive = {eid: ep for eid, ep in alive.items() if eid in met}
                for eid in alive:
                    evidence[eid][key] = met[eid]
            else:
                alive = {eid: ep for eid, ep in alive.items() if eid not in met}
            removed[key] = len(before - {ep.patient_id for ep in alive.values()})

        latest: dict[str, Episode] = {}
        for ep in alive.values():  # most recent passing episode per patient
            if ep.patient_id not in latest or ep.admitted > latest[ep.patient_id].admitted:
                latest[ep.patient_id] = ep
        candidates = [
            Candidate(patient_id=pid, encounter_id=ep.encounter_id, citations=evidence[ep.encounter_id])
            for pid, ep in sorted(latest.items())
        ]
        return PrefilterResult(
            study_id=study.id,
            reference_time=wh.now,
            patients_considered=len({ep.patient_id for ep in episodes}),
            candidates=candidates,
            removed_by_criterion=removed,
        )
    finally:
        if own_conn:
            conn.close()
