"""Acceptance for task A. Run with PYTHONPATH set to the result worktree."""

import pytest

users = pytest.importorskip("src.users")


def test_rejects_empty_name():
    with pytest.raises(ValueError):
        users.create_user("", "ada@example.com", "pw")


def test_rejects_empty_email():
    with pytest.raises(ValueError):
        users.create_user("Ada", "", "pw")


def test_accepts_nonempty():
    user = users.create_user("Ada", "ada@example.com", "pw")
    assert user["name"] == "Ada"
    assert user["email"] == "ada@example.com"
