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
        'On it.\n```action\n{"action": "resume_agent", "params": {"message": "add tests"}}\n```'
    )
    assert "On it." in prose
    assert "```" not in prose
    assert action["action"] == "resume_agent"
    assert action["params"]["message"] == "add tests"
    assert parse_action("What was the blast radius?")[1] is None
    broken, dropped = parse_action("Here.\n```action\n{nope}\n```")
    assert dropped is None
    assert "Here." in broken
    assert should_fast_path(0.9, "ship it")
    assert not should_fast_path(0.9, " ".join(["word"] * 15))
    assert not should_fast_path(0.5, "status")
    assert not allow_action(0.2)
    assert allow_action(0.3)


def test_context_drops_history_then_details():
    from app.discord.conversation import fit_context

    active = [
        {
            "pr_number": 7,
            "repo": "o/r",
            "severity": "CRITICAL",
            "status": "dispatched",
            "missing": "x" * 3000,
            "agent": "none",
        }
    ]
    recent = ["PR #1 — LOW — resolved yesterday"]
    short = fit_context(active, "No active agent:", recent, ["user: hi"], limit=8000)
    assert "Recent history:" in short
    assert "Missing:" in short
    tight = fit_context(active, "No active agent:", recent, ["user: hi"], limit=500)
    assert "Recent history:" not in tight
    assert "Missing:" not in tight
    assert "PR #7 — CRITICAL — dispatched" in tight


async def test_owner_only_rejects_others(monkeypatch):
    from app.discord import views

    monkeypatch.setattr(views.settings, "DISCORD_OWNER_ID", "1")
    sent = {}

    class _Response:
        async def send_message(self, content, ephemeral=False):
            sent["content"] = content
            sent["ephemeral"] = ephemeral

    class _Interaction:
        def __init__(self, user_id):
            self.user = type("U", (), {"id": user_id})()
            self.response = _Response()

    assert await views.owner_only(_Interaction(2)) is False
    assert sent["ephemeral"] is True
    assert sent["content"] == "Only the repo owner can do this."
    assert await views.owner_only(_Interaction(1)) is True


def test_parse_pr_url():
    assert parse_pr_url("https://github.com/o/r/pull/9") == ("o", "r", 9)
    assert parse_pr_url("nope") is None


def test_component_parts():
    from app.discord.views import component_parts

    assert component_parts("prs:approve:2e2000d6-7afc-473e-a79d-420f81f6cd97") == (
        "approve",
        "2e2000d6-7afc-473e-a79d-420f81f6cd97",
    )
    assert component_parts("nope") == ("", "")
