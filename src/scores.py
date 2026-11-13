"""Clinical scores, calculated exactly by code (AGENTS.md: scores must be exact, not guessed).

Owns: NEWS2 and CURB-65. Pure functions of plain values: no database, no model.

    compute_news2(resp_rate=26, spo2=91, on_oxygen=True, temperature=38.4,
                  systolic_bp=95, heart_rate=115, consciousness="C")   # -> 16
    compute_curb65(confusion=True, urea=8.1, resp_rate=32,
                   systolic_bp=85, diastolic_bp=50, age=70)            # -> 5
"""

from __future__ import annotations

NEW_CONFUSION = {"C", "V", "P", "U"}  # ACVPU: anything other than Alert


def compute_news2(
    resp_rate: int,
    spo2: int,
    on_oxygen: bool,
    temperature: float,
    systolic_bp: int,
    heart_rate: int,
    consciousness: str,
    spo2_scale: int = 1,
) -> int:
    """National Early Warning Score 2 (Royal College of Physicians, 2017).

    consciousness is ACVPU: "A" scores 0; "C", "V", "P" or "U" scores 3.
    spo2_scale 2 is only for patients with a prescribed 88-92% target
    (hypercapnic respiratory failure); use 1 otherwise.
    """
    if spo2_scale not in (1, 2):
        raise ValueError("spo2_scale must be 1 or 2")
    if consciousness not in {"A"} | NEW_CONFUSION:
        raise ValueError(f"consciousness must be one of A, C, V, P, U (got {consciousness!r})")
    return (
        _resp_rate_points(resp_rate)
        + (_spo2_scale1_points(spo2) if spo2_scale == 1 else _spo2_scale2_points(spo2, on_oxygen))
        + (2 if on_oxygen else 0)
        + _systolic_points(systolic_bp)
        + _pulse_points(heart_rate)
        + (0 if consciousness == "A" else 3)
        + _temperature_points(temperature)
    )


def _resp_rate_points(rr: int) -> int:
    if rr <= 8:
        return 3
    if rr <= 11:
        return 1
    if rr <= 20:
        return 0
    if rr <= 24:
        return 2
    return 3


def _spo2_scale1_points(spo2: int) -> int:
    if spo2 <= 91:
        return 3
    if spo2 <= 93:
        return 2
    if spo2 <= 95:
        return 1
    return 0


def _spo2_scale2_points(spo2: int, on_oxygen: bool) -> int:
    if spo2 <= 83:
        return 3
    if spo2 <= 85:
        return 2
    if spo2 <= 87:
        return 1
    if spo2 <= 92 or not on_oxygen:  # 88-92%, or 93%+ on air
        return 0
    if spo2 <= 94:
        return 1
    if spo2 <= 96:
        return 2
    return 3


def _systolic_points(sbp: int) -> int:
    if sbp <= 90:
        return 3
    if sbp <= 100:
        return 2
    if sbp <= 110:
        return 1
    if sbp <= 219:
        return 0
    return 3


def _pulse_points(hr: int) -> int:
    if hr <= 40:
        return 3
    if hr <= 50:
        return 1
    if hr <= 90:
        return 0
    if hr <= 110:
        return 1
    if hr <= 130:
        return 2
    return 3


def _temperature_points(temp: float) -> int:
    if temp <= 35.0:
        return 3
    if temp <= 36.0:
        return 1
    if temp <= 38.0:
        return 0
    if temp <= 39.0:
        return 1
    return 2


def compute_curb65(
    confusion: bool,
    urea: float | None,
    resp_rate: int,
    systolic_bp: int,
    diastolic_bp: int,
    age: int,
) -> int:
    """CURB-65 (British Thoracic Society): one point each for

    C  new confusion
    U  urea > 7 mmol/L
    R  respiratory rate >= 30/min
    B  systolic BP < 90 mmHg or diastolic BP <= 60 mmHg
    65 age >= 65

    urea=None scores 0 for U (that is CRB-65). Callers that need the full
    score must check for a missing urea themselves.
    """
    return (
        int(confusion)
        + int(urea is not None and urea > 7)
        + int(resp_rate >= 30)
        + int(systolic_bp < 90 or diastolic_bp <= 60)
        + int(age >= 65)
    )
