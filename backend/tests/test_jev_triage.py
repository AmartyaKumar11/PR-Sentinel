"""Self-check for Jev triage assembly (no live API)."""

from types import SimpleNamespace

from app.services.jev_triage import assemble_triage, assemble_verification


def _noul(v: float):
    return SimpleNamespace(noul=v)


def _choice(label: str, conf: float = 0.9):
    return SimpleNamespace(choice=label, confidence=conf)


def _score(v: float):
    return SimpleNamespace(score=v)


def test_assemble_scenario1_critical():
    diagnosis = {
        "changed_files": ["src/auth.py"],
        "linked_issue": {
            "requirements": [
                "Validate email format",
                "Send reset email",
                "Expire token after 1 hour",
            ]
        },
        "blast_radius": {"depth_1_impacted": ["a"], "depth_2_impacted": ["b"], "untested_impacted": []},
    }
    req_q = {f"req_{i}_addressed": None for i in range(3)}
    scope_q = {"scope_0_in_issue": None}
    answers = {
        "severity": _choice("HIGH", 0.89),
        "action": _choice("dispatch_urgent", 0.91),
        "is_trivial": _noul(0.02),
        "is_phantom_pr": _noul(0.05),
        "is_underspecified_issue": _noul(0.1),
        "touches_auth_security": _noul(0.95),
        "risk_level": _score(2.5),
        "req_0_addressed": _noul(0.1),
        "req_1_addressed": _noul(0.9),
        "req_2_addressed": _noul(0.08),
        "scope_0_in_issue": _noul(0.8),
    }
    triage = assemble_triage(diagnosis, answers, req_q, scope_q)
    assert triage["severity"] == "CRITICAL"  # auth + missing overrides
    assert triage["action"] == "dispatch_urgent"
    assert "Validate email format" in triage["intent_alignment"]["missing"]
    assert "Send reset email" in triage["intent_alignment"]["addressed"]
    assert triage["confidence_scores"]["touches_auth"] == 0.95


def test_assemble_verification_partial():
    answers = {
        "fixed_0": _noul(0.9),
        "fixed_1": _noul(0.2),
        "scope_resolved_0": _noul(0.8),
        "new_issues": _noul(0.1),
    }
    v = assemble_verification(
        ["Validate email", "Expire token"],
        ["notifications.py"],
        answers,
    )
    assert v["all_resolved"] is False
    assert v["resolved_items"] == ["Validate email"]
    assert v["remaining_items"] == ["Expire token"]


if __name__ == "__main__":
    test_assemble_scenario1_critical()
    test_assemble_verification_partial()
    print("jev_ok")
