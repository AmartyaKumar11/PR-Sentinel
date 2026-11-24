"""Acceptance for task B. The result must call normalize_email."""

import inspect

import pytest

users = pytest.importorskip("src.users")


def test_stored_email_is_normalized():
    user = users.create_user("Ada", "  Foo@Bar.COM ", "pw")
    assert user["email"] == "foo@bar.com"


def test_create_user_calls_normalize_email():
    source = inspect.getsource(users.create_user)
    assert "normalize_email(" in source
