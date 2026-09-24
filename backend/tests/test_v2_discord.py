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


def test_parse_pr_url():
    assert parse_pr_url("https://github.com/o/r/pull/9") == ("o", "r", 9)
    assert parse_pr_url("nope") is None
