"""Fixed prompts. Both backends receive the same text. Each stays under 600 characters."""

from __future__ import annotations

NORMALIZE_HELPER = '''def normalize_email(email: str) -> str:
    """Lowercase and strip an email address."""
    return email.strip().lower()


'''

TASKS = {
    "A": (
        "Add input validation to create_user in src/users.py. If the name is empty or the email "
        "is empty, raise ValueError. Keep the existing password parameter. Add "
        "tests/test_users_validation.py covering both rejections and one valid user. "
        "Do not edit unrelated files."
    ),
    "B": (
        "create_user in src/users.py stores the email unchanged. normalize_email(email) on this "
        "branch lowercases and strips the address, and nothing calls it yet. Call normalize_email "
        "inside create_user so the stored email is normalized. Add a test where a mixed-case "
        "address with surrounding spaces is stored trimmed and lowercase. Do not edit unrelated files."
    ),
}
