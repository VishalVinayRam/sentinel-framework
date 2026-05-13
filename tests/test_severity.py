import pytest
from sentinel.core.severity import Severity


def test_severity_values():
    assert Severity.P1.value == "P1"
    assert Severity.P2.value == "P2"
    assert Severity.P3.value == "P3"
    assert Severity.P4.value == "P4"


def test_is_critical():
    assert Severity.P1.is_critical() is True
    assert Severity.P2.is_critical() is False
    assert Severity.P3.is_critical() is False
    assert Severity.P4.is_critical() is False


def test_escalate_goes_up():
    assert Severity.P4.escalate() == Severity.P3
    assert Severity.P3.escalate() == Severity.P2
    assert Severity.P2.escalate() == Severity.P1


def test_escalate_caps_at_p1():
    # escalating P1 should stay P1
    assert Severity.P1.escalate() == Severity.P1


def test_labels():
    assert Severity.P1.label() == "CRITICAL"
    assert Severity.P2.label() == "HIGH"
    assert Severity.P3.label() == "MEDIUM"
    assert Severity.P4.label() == "LOW"


def test_severity_from_string():
    assert Severity("P1") == Severity.P1
    assert Severity("P4") == Severity.P4


def test_severity_invalid():
    with pytest.raises(ValueError):
        Severity("P5")


def test_severity_is_string():
    # Severity extends str so it can be used in f-strings and comparisons
    assert Severity.P1 == "P1"
    assert f"level={Severity.P2}" == "level=P2"
