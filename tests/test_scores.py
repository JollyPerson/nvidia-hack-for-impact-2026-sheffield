"""NEWS2 and CURB-65 against hand-worked examples and every band boundary."""

import pytest

from src.scores import compute_curb65, compute_news2

NORMAL = dict(resp_rate=16, spo2=97, on_oxygen=False, temperature=37.0,
              systolic_bp=120, heart_rate=75, consciousness="A")


def news2(**changes) -> int:
    return compute_news2(**{**NORMAL, **changes})


# -- NEWS2: whole examples -------------------------------------------------------


def test_news2_all_normal_is_zero():
    assert news2() == 0


def test_news2_unwell_pneumonia_example():
    # RR 26 (3) + SpO2 91 (3) + on O2 (2) + T 38.4 (1) + SBP 95 (2) + HR 115 (2) + new confusion (3) = 16
    assert news2(resp_rate=26, spo2=91, on_oxygen=True, temperature=38.4,
                 systolic_bp=95, heart_rate=115, consciousness="C") == 16


def test_news2_moderate_example():
    # RR 22 (2) + SpO2 94 (1) + air (0) + T 38.2 (1) + SBP 108 (1) + HR 96 (1) + alert (0) = 6
    assert news2(resp_rate=22, spo2=94, temperature=38.2, systolic_bp=108, heart_rate=96) == 6


# -- NEWS2: each parameter at its band edges (others normal) ----------------------


@pytest.mark.parametrize("rr, points", [(8, 3), (9, 1), (11, 1), (12, 0), (20, 0), (21, 2), (24, 2), (25, 3)])
def test_news2_resp_rate_bands(rr, points):
    assert news2(resp_rate=rr) == points


@pytest.mark.parametrize("spo2, points", [(91, 3), (92, 2), (93, 2), (94, 1), (95, 1), (96, 0), (100, 0)])
def test_news2_spo2_scale1_bands(spo2, points):
    assert news2(spo2=spo2) == points


@pytest.mark.parametrize("spo2, on_oxygen, points", [
    (83, False, 3), (84, False, 2), (85, False, 2), (86, False, 1), (87, False, 1),
    (88, False, 0), (92, False, 0), (93, False, 0), (98, False, 0),   # 93%+ on air scores 0
    (90, True, 0), (93, True, 1), (94, True, 1), (95, True, 2), (96, True, 2), (97, True, 3),
])
def test_news2_spo2_scale2_bands(spo2, on_oxygen, points):
    oxygen_points = 2 if on_oxygen else 0
    assert news2(spo2=spo2, on_oxygen=on_oxygen, spo2_scale=2) == points + oxygen_points


def test_news2_oxygen_adds_two():
    assert news2(on_oxygen=True) == 2


@pytest.mark.parametrize("sbp, points", [(90, 3), (91, 2), (100, 2), (101, 1), (110, 1), (111, 0), (219, 0), (220, 3)])
def test_news2_systolic_bands(sbp, points):
    assert news2(systolic_bp=sbp) == points


@pytest.mark.parametrize("hr, points", [(40, 3), (41, 1), (50, 1), (51, 0), (90, 0), (91, 1), (110, 1),
                                        (111, 2), (130, 2), (131, 3)])
def test_news2_pulse_bands(hr, points):
    assert news2(heart_rate=hr) == points


@pytest.mark.parametrize("temp, points", [(35.0, 3), (35.1, 1), (36.0, 1), (36.1, 0), (38.0, 0),
                                          (38.1, 1), (39.0, 1), (39.1, 2)])
def test_news2_temperature_bands(temp, points):
    assert news2(temperature=temp) == points


@pytest.mark.parametrize("acvpu, points", [("A", 0), ("C", 3), ("V", 3), ("P", 3), ("U", 3)])
def test_news2_consciousness(acvpu, points):
    assert news2(consciousness=acvpu) == points


def test_news2_rejects_bad_input():
    with pytest.raises(ValueError):
        news2(consciousness="X")
    with pytest.raises(ValueError):
        news2(spo2_scale=3)


# -- CURB-65 --------------------------------------------------------------------------

WELL = dict(confusion=False, urea=5.0, resp_rate=18, systolic_bp=130, diastolic_bp=80, age=50)


def curb65(**changes) -> int:
    return compute_curb65(**{**WELL, **changes})


def test_curb65_well_adult_is_zero():
    assert curb65() == 0


def test_curb65_maximum_example():
    # Confused, urea 8.1, RR 32, BP 85/50, aged 70: one point each = 5
    assert compute_curb65(confusion=True, urea=8.1, resp_rate=32, systolic_bp=85, diastolic_bp=50, age=70) == 5


def test_curb65_typical_moderate_example():
    # 78-year-old, urea 9.7, RR 24, BP 132/79, alert: urea + age = 2
    assert compute_curb65(confusion=False, urea=9.7, resp_rate=24, systolic_bp=132, diastolic_bp=79, age=78) == 2


@pytest.mark.parametrize("changes, points", [
    ({"confusion": True}, 1),
    ({"urea": 7.0}, 0), ({"urea": 7.1}, 1),              # strictly above 7
    ({"resp_rate": 29}, 0), ({"resp_rate": 30}, 1),
    ({"systolic_bp": 90}, 0), ({"systolic_bp": 89}, 1),  # systolic below 90
    ({"diastolic_bp": 61}, 0), ({"diastolic_bp": 60}, 1),  # diastolic 60 or below
    ({"systolic_bp": 85, "diastolic_bp": 55}, 1),        # B counts once
    ({"age": 64}, 0), ({"age": 65}, 1),
])
def test_curb65_component_thresholds(changes, points):
    assert curb65(**changes) == points


def test_curb65_missing_urea_scores_no_urea_point():
    assert curb65(urea=None, age=70, confusion=True) == 2
