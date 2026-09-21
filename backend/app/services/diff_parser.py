"""Unified diff → changed identifiers (M-05)."""

from __future__ import annotations

import re

_DEF_RE = re.compile(r"^(\+)?\s*(?:async\s+)?(?:def|class)\s+(\w+)")
_FILE_RE = re.compile(r"^(?:---|\+\+\+)\s+(?:a/|b/)?(.+)$")
_HUNK_FILE = re.compile(r"^diff --git a/(.+) b/(.+)$")


def extract_changed_identifiers(diff: str, module_prefix: str | None = None) -> list[str]:
    """
    Extract function/class names that were added or modified in a unified diff.
    Returns qualified ids when possible: src.auth.reset_password
    """
    current_file: str | None = None
    changed: list[str] = []
    seen: set[str] = set()

    for line in diff.splitlines():
        m = _HUNK_FILE.match(line)
        if m:
            current_file = m.group(2)
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path != "/dev/null":
                current_file = path
            continue

        if not line.startswith("+") or line.startswith("+++"):
            continue

        m = _DEF_RE.match(line)
        if not m:
            continue
        name = m.group(2)
        if current_file and current_file.endswith(".py"):
            mod = current_file.replace("\\", "/").removesuffix(".py").replace("/", ".")
            qid = f"{mod}.{name}"
        else:
            qid = name
        if qid not in seen:
            seen.add(qid)
            changed.append(qid)

    return changed


def extract_changed_files(diff: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        m = _HUNK_FILE.match(line)
        if m:
            path = m.group(2)
            if path not in seen:
                seen.add(path)
                files.append(path)
            continue
        if line.startswith("+++ ") and not line.endswith("/dev/null"):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path not in seen and path != "/dev/null":
                seen.add(path)
                files.append(path)
    return files


def is_trivial_diff(diff: str) -> bool:
    """True if only non-code files changed."""
    files = extract_changed_files(diff)
    if not files:
        return True
    code_exts = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java"}
    return all(
        not any(f.endswith(ext) for ext in code_exts) for f in files
    )
