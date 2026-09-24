from app.discord.views import parse_pr_url
from app.services.cursor_client import public_status
from app.services.prompt_crafter import _simple_template


def test_public_status_maps_sdk_words():
    assert public_status("finished") == "completed"
    assert public_status("error") == "failed"
    assert public_status("running") == "running"


def test_simple_prompt_names_the_file():
    text = _simple_template(
        {},
        {
            "suggested_fix_approach": "Add email validation",
            "affected_files_priority": [{"path": "src/auth.py"}],
        },
    )
    assert "src/auth.py" in text
    assert "Add email validation" in text


def test_parse_action_splits_prose_and_json():
    from app.discord.conversation import allow_action, parse_action, should_fast_path

    prose, action = parse_action(
        'On it.\n{"action": "resume", "params": {"message": "add tests"}}'
    )
    assert "On it." in prose
    assert action["action"] == "resume"
    assert action["params"]["message"] == "add tests"
    assert parse_action("What was the blast radius?")[1] is None
    assert should_fast_path(0.9, "ship it")
    assert not should_fast_path(0.9, " ".join(["word"] * 15))
    assert not should_fast_path(0.5, "status")
    assert not allow_action(0.2)
    assert allow_action(0.3)


def test_context_cap():
    from app.discord.conversation import cap_context

    assert len(cap_context("x" * 9000)) == 8000


def test_parse_pr_url():
    assert parse_pr_url("https://github.com/o/r/pull/9") == ("o", "r", 9)
    assert parse_pr_url("nope") is None
