"""Parse unified diffs (BUILD-SEQUENCE Phase 1.2)."""

from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_DEF_RE = re.compile(r"^\+\s*(?:async\s+)?(?:def|class)\s+(\w+)")


def parse_diff(diff_text: str) -> dict:
    files: list[dict] = []
    current: dict | None = None
    hunk: dict | None = None
    adds = dels = 0

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current:
                files.append(current)
            parts = line.split(" b/", 1)
            path = parts[1] if len(parts) == 2 else line.split()[-1]
            current = {"path": path, "status": "modified", "hunks": []}
            hunk = None
            continue
        if line.startswith("new file"):
            if current:
                current["status"] = "added"
            continue
        if line.startswith("deleted file"):
            if current:
                current["status"] = "deleted"
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if current and path != "/dev/null":
                current["path"] = path
            continue
        m = _HUNK.match(line)
        if m and current is not None:
            start = int(m.group(2))
            hunk = {"start_line": start, "end_line": start, "content": ""}
            current["hunks"].append(hunk)
            continue
        if hunk is not None:
            hunk["content"] += line + "\n"
            if line.startswith("+") and not line.startswith("+++"):
                hunk["end_line"] += 1
                adds += 1
            elif line.startswith("-") and not line.startswith("---"):
                dels += 1
            elif not line.startswith("\\"):
                hunk["end_line"] += 1

    if current:
        files.append(current)

    return {
        "files": files,
        "summary": f"{len(files)} files changed, +{adds} -{dels}",
    }


def extract_changed_identifiers(diff_data, dep_graph: dict | None = None) -> list[str]:
    """Accept parsed dict or raw unified diff string."""
    if isinstance(diff_data, str):
        return _ids_from_raw(diff_data)
    changed: list[str] = []
    seen: set[str] = set()
    for f in diff_data.get("files", []):
        path = f.get("path", "")
        if not path.endswith(".py"):
            continue
        mod = _module_from_path(path)
        for hunk in f.get("hunks", []):
            for line in hunk.get("content", "").splitlines():
                m = _DEF_RE.match(line)
                if not m:
                    continue
                qid = f"{mod}.{m.group(1)}"
                if qid not in seen:
                    seen.add(qid)
                    changed.append(qid)
        if dep_graph:
            # optional: map hunk lines to graph nodes
            for node in dep_graph.get("nodes", []):
                if node.get("file") != path:
                    continue
                line = node.get("line", 0)
                for hunk in f.get("hunks", []):
                    if hunk["start_line"] <= line <= hunk.get("end_line", line):
                        nid = node["id"]
                        if nid not in seen:
                            seen.add(nid)
                            changed.append(nid)
    return changed


def _ids_from_raw(diff: str) -> list[str]:
    return extract_changed_identifiers(parse_diff(diff))


def extract_changed_files(diff: str) -> list[str]:
    return [f["path"] for f in parse_diff(diff)["files"]]


def is_code_file(path: str) -> bool:
    return path.endswith(".py") and "__pycache__" not in path and not path.startswith("test")


def is_test_file(path: str) -> bool:
    name = path.replace("\\", "/").split("/")[-1]
    return path.endswith(".py") and ("test_" in name or name.startswith("test"))


def is_trivial_diff(diff: str) -> bool:
    files = extract_changed_files(diff)
    if not files:
        return True
    return all(not f.endswith(".py") for f in files)


def _module_from_path(path: str) -> str:
    # BUILD-SEQUENCE: src/auth.py → auth ; keep src.auth for our existing graph ids
    p = path.replace("\\", "/").removesuffix(".py")
    return p.replace("/", ".")
