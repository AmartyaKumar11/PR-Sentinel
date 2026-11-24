"""Live git, GitHub, Cursor, and local TreeFleet. Tokens never go into records or logs."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

from experiments.core import (
    PINNED_MODEL,
    Slot,
    branch_for,
    pin_model,
    scrub,
    valid_sha,
)
from experiments.engine import InfraError, LaunchFailed
from experiments.tasks import NORMALIZE_HELPER, TASKS

ROOT = Path(__file__).resolve().parents[1]
DEMO = Path(r"D:\pr-sentinel-demo")
TARGET = "AmartyaKumar11/pr-sentinel-demo"
OWNER, REPO = TARGET.split("/", 1)
FORBIDDEN_PRS = {77, 80}
_REF = re.compile(r"^[A-Za-z0-9._/-]+$")


def apply_token_file() -> None:
    """Point the harness at the running UI token file. Never print the token."""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return
    path = str(Path(local) / "treefleet" / "ui.token")
    os.environ["TREEFLEET_TOKEN_FILE"] = path
    if "app.config" in sys.modules:
        sys.modules["app.config"].settings.TREEFLEET_TOKEN_FILE = path


def _backend_path() -> None:
    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)


def collect_secrets() -> list[str]:
    apply_token_file()
    _backend_path()
    from app.config import settings
    from app.services.treefleet_client import load_token

    values = [settings.GITHUB_TOKEN, settings.CURSOR_API_KEY, settings.TREEFLEET_UI_TOKEN]
    try:
        values.append(load_token())
    except Exception:
        pass
    return [item for item in values if isinstance(item, str) and len(item) >= 8]


def git(cwd: Path, args: list[str], token: str | None = None, timeout: int = 120) -> str:
    coded = ""
    cmd = ["git", "-c", "credential.helper=", *args]
    if token and args and args[0] in {"push", "fetch", "ls-remote"}:
        # GitHub git remotes accept Basic x-access-token. Bearer is refused for this token.
        coded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd = [
            "git",
            "-c",
            "credential.helper=",
            "-c",
            f"http.extraHeader=Authorization: Basic {coded}",
            *args,
        ]
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        short = " ".join(args[:3])
        raise InfraError(f"git {short} timed out") from None
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    for secret in (token, coded):
        if secret:
            out = out.replace(secret, "[redacted]")
            err = err.replace(secret, "[redacted]")
    if proc.returncode != 0:
        short = " ".join(args[:3])
        raise InfraError(f"git {short} failed: {err.strip()[-300:]}")
    return out.strip()


def _pr_number(url: str) -> int | None:
    tail = (url or "").rstrip("/").split("/")
    if len(tail) < 2 or tail[-2] != "pull":
        return None
    if not tail[-1].isdigit():
        return None
    return int(tail[-1])


class LiveEngine:
    timeout_s = 25 * 60
    poll_s = 15.0

    def __init__(self, expid: str, cursor_model: str, secrets: list[str]) -> None:
        apply_token_file()
        if cursor_model != PINNED_MODEL:
            raise InfraError("refusing a model other than the pinned id")
        _backend_path()
        from app.config import settings
        from app.services.cursor_client import CursorClient
        from app.services.treefleet_client import TreeFleetClient

        self.expid = expid
        self.cursor_model = cursor_model
        self.secrets = secrets
        self.token = settings.GITHUB_TOKEN
        self.cursor = CursorClient()
        self.fleet = TreeFleetClient()
        self.last: dict[str, dict] = {}
        self.git_lock = asyncio.Lock()
        self.tmp = ROOT / "experiments" / "_tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.clone = self.tmp / "demo"
        origin = git(DEMO, ["remote", "get-url", "origin"])
        if not (self.clone / ".git").exists():
            git(self.tmp, ["clone", "--local", str(DEMO), "demo"])
            git(self.clone, ["remote", "set-url", "origin", origin])
        self.seed_sha = git(DEMO, ["rev-parse", "main"])
        if not valid_sha(self.seed_sha):
            raise InfraError("seed sha is not a commit id")

    def note(self, text: str) -> None:
        print(scrub(text, self.secrets), flush=True)

    def model_for(self, slot: Slot) -> str:
        return PINNED_MODEL

    async def seed(self, slot: Slot) -> dict:
        async with self.git_lock:
            return await asyncio.to_thread(self._seed_sync, slot)

    def _remote_tip(self, branch: str) -> str | None:
        spec = f"refs/heads/{branch}:refs/remotes/origin/{branch}"
        try:
            git(self.clone, ["fetch", "origin", spec], token=self.token)
            sha = git(self.clone, ["rev-parse", f"refs/remotes/origin/{branch}"])
        except InfraError:
            return None
        return sha if valid_sha(sha) else None

    def _seed_sync(self, slot: Slot) -> dict:
        branch = branch_for(self.expid, slot)
        wt = self.tmp / "slots" / branch.replace("/", "_")
        remote = self._remote_tip(branch)
        base = remote or self.seed_sha
        if not wt.exists():
            git(self.clone, ["worktree", "add", "--detach", str(wt), base])
        git(wt, ["checkout", "-B", branch, base])
        if slot.task == "B":
            self._write_helper(wt)
            dirty = git(wt, ["status", "--porcelain", "--", "src/users.py"])
            if dirty:
                git(wt, ["add", "--", "src/users.py"])
                self._commit(wt, "seed normalize_email helper")
        elif git(wt, ["rev-parse", "HEAD"]) == self.seed_sha:
            # GitHub refuses a pull request whose head tree matches main.
            self._commit(wt, "exp slot marker", allow_empty=True)
        seed_head = git(wt, ["rev-parse", "HEAD"])
        if seed_head != remote:
            git(wt, ["push", "origin", f"{branch}:{branch}"], token=self.token)
        pr = self._ensure_pr(branch, slot)
        if slot.backend == "treefleet":
            self._sync_demo(branch)
        return {
            "pr_number": pr["number"],
            "pr_url": pr["url"],
            "branch": branch,
            "seed_head": seed_head,
        }

    def _commit(self, wt: Path, message: str, *, allow_empty: bool = False) -> None:
        args = [
            "-c",
            "user.email=exp@localhost",
            "-c",
            "user.name=pr-sentinel-exp",
            "commit",
            "-m",
            message,
        ]
        if allow_empty:
            args.insert(5, "--allow-empty")
        git(wt, args)

    def _write_helper(self, wt: Path) -> None:
        path = wt / "src" / "users.py"
        text = path.read_text(encoding="utf-8")
        if "def normalize_email(" in text:
            return
        needle = "def create_user("
        if needle not in text:
            raise InfraError("create_user is missing from src/users.py")
        path.write_text(text.replace(needle, NORMALIZE_HELPER + needle, 1), encoding="utf-8", newline="\n")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _ensure_pr(self, branch: str, slot: Slot) -> dict:
        found = self._find_pr(branch)
        if found:
            return found
        title = f"exp {self.expid} {slot.task} {slot.backend} {slot.rep}"
        body = f"Harness slot {slot.key}. Seeded for the execution experiment. Do not merge."
        response = httpx.post(
            f"https://api.github.com/repos/{OWNER}/{REPO}/pulls",
            headers=self._headers(),
            json={"title": title, "head": branch, "base": "main", "body": body},
            timeout=30.0,
        )
        if response.status_code == 422:
            found = self._find_pr(branch)
            if found:
                return found
        if response.status_code < 200 or response.status_code >= 300:
            detail = scrub(response.text[:300], self.secrets)
            raise InfraError(f"github {response.status_code}: {detail}")
        return self._pr_payload(response.json())

    def _find_pr(self, branch: str) -> dict | None:
        response = httpx.get(
            f"https://api.github.com/repos/{OWNER}/{REPO}/pulls",
            headers=self._headers(),
            params={"head": f"{OWNER}:{branch}", "state": "open"},
            timeout=30.0,
        )
        if response.status_code >= 400:
            detail = scrub(response.text[:300], self.secrets)
            raise InfraError(f"github {response.status_code}: {detail}")
        rows = response.json()
        if not isinstance(rows, list):
            return None
        for row in rows:
            number = int(row.get("number") or 0)
            if number in FORBIDDEN_PRS:
                raise InfraError("refusing protected pull request")
        if not rows:
            return None
        return self._pr_payload(rows[0])

    def _pr_payload(self, row: dict) -> dict:
        number = int(row.get("number") or 0)
        if number in FORBIDDEN_PRS:
            raise InfraError("refusing protected pull request")
        return {"number": number, "url": row.get("html_url") or ""}

    def _get_pr(self, number: int) -> dict:
        if number in FORBIDDEN_PRS:
            raise InfraError("refusing protected pull request")
        response = httpx.get(
            f"https://api.github.com/repos/{OWNER}/{REPO}/pulls/{number}",
            headers=self._headers(),
            timeout=30.0,
        )
        if response.status_code >= 400:
            raise InfraError(f"github {response.status_code}")
        row = response.json()
        sha = ((row.get("head") or {}).get("sha")) or ""
        ref = ((row.get("head") or {}).get("ref")) or ""
        if not valid_sha(sha):
            raise InfraError("pull request head is not a commit id")
        return {"head_sha": sha, "branch": ref, "url": row.get("html_url") or ""}

    def _sync_demo(self, branch: str) -> None:
        branch_for_check = branch
        if not branch_for_check.startswith("exp/"):
            raise InfraError("unsafe branch")
        head = git(DEMO, ["rev-parse", "HEAD"])
        name = git(DEMO, ["rev-parse", "--abbrev-ref", "HEAD"])
        already = ""
        try:
            already = git(DEMO, ["rev-parse", "--verify", f"refs/heads/{branch}"])
        except InfraError:
            already = ""
        if not valid_sha(already):
            git(
                DEMO,
                ["fetch", "origin", f"refs/heads/{branch}:refs/heads/{branch}"],
                token=self.token,
            )
        if git(DEMO, ["rev-parse", "HEAD"]) != head or git(DEMO, ["rev-parse", "--abbrev-ref", "HEAD"]) != name:
            raise InfraError("demo checkout changed")

    async def launch(self, slot: Slot, seeded: dict) -> dict:
        prompt = TASKS[slot.task]
        model = self.model_for(slot)
        try:
            if slot.backend == "cursor":
                result = await self.cursor.launch_on_pull(
                    TARGET, int(seeded["pr_number"]), prompt, model=model
                )
            else:
                result = await self.fleet.launch_on_pull(
                    TARGET,
                    int(seeded["pr_number"]),
                    prompt,
                    model=model,
                    include_base=slot.backend != "treefleet-noref",
                    include_repo=False,
                )
        except Exception as exc:
            detail = scrub(str(exc), self.secrets).replace("\n", " ")[:200]
            if "authorization" in detail.lower() or "bearer" in detail.lower():
                detail = type(exc).__name__
            else:
                detail = f"{type(exc).__name__}: {detail}"
            raise InfraError(f"launch failed before a run id ({detail})") from None
        if result.get("error") or not result.get("agent_id"):
            raise LaunchFailed(str(result.get("reason") or result.get("error") or "launch refused")[:300])
        return result

    async def poll(self, slot: Slot, agent_id: str) -> dict:
        client = self.cursor if slot.backend == "cursor" else self.fleet
        started = asyncio.get_running_loop().time()
        pr_seen = None
        while True:
            try:
                status = await client.get_run_status(agent_id)
            except Exception as exc:
                text = scrub(str(exc), self.secrets)
                if "not found" in text.lower():
                    raise InfraError("run not found") from None
                raise InfraError("status request failed") from None
            now = asyncio.get_running_loop().time() - started
            status = dict(status)
            if status.get("pr_url") and pr_seen is None:
                pr_seen = now
            status["pr_seen_s"] = pr_seen
            if status.get("status") in {"completed", "failed", "cancelled", "expired"}:
                status["finished_s"] = now
                self.last[agent_id] = status
                return status
            self.last[agent_id] = status
            await asyncio.sleep(self.poll_s)

    async def measure(self, slot: Slot, seeded: dict, status: dict) -> dict:
        async with self.git_lock:
            return await asyncio.to_thread(self._measure_sync, slot, seeded, status)

    def _measure_sync(self, slot: Slot, seeded: dict, status: dict) -> dict:
        pr_url = status.get("pr_url") or seeded.get("pr_url")
        result_sha = ""
        if slot.backend == "cursor":
            branch = seeded["branch"]
            git(
                self.clone,
                ["fetch", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}"],
                token=self.token,
            )
            result_sha = git(self.clone, ["rev-parse", f"origin/{branch}"])
        else:
            number = _pr_number(str(status.get("pr_url") or ""))
            if number is None:
                return self._empty(pr_url)
            info = self._get_pr(number)
            ref = info["branch"]
            safe_ref = bool(_REF.fullmatch(ref or "")) and ".." not in ref.split("/")
            if safe_ref:
                spec = f"refs/heads/{ref}:refs/remotes/origin/{ref}"
                try:
                    git(self.clone, ["fetch", "origin", spec], token=self.token)
                    result_sha = git(self.clone, ["rev-parse", f"refs/remotes/origin/{ref}"])
                except InfraError:
                    result_sha = info["head_sha"]
            else:
                result_sha = info["head_sha"]
            pr_url = info["url"] or pr_url
        if not valid_sha(result_sha):
            return self._empty(pr_url)
        seed_head = seeded["seed_head"]
        numstat = git(self.clone, ["diff", "--numstat", f"{seed_head}..{result_sha}"])
        from experiments.core import parse_numstat

        files, added, removed = parse_numstat(numstat)
        return {
            "result_commit": result_sha,
            "files_changed": files,
            "lines_added": added,
            "lines_removed": removed,
            "mergeable_into_pr_head": self._mergeable(seed_head, result_sha, slot),
            "pr_url": pr_url,
        }

    def _empty(self, pr_url: str | None) -> dict:
        return {
            "result_commit": None,
            "files_changed": None,
            "lines_added": None,
            "lines_removed": None,
            "mergeable_into_pr_head": None,
            "pr_url": pr_url,
        }

    def _mergeable(self, seed_head: str, result_sha: str, slot: Slot) -> bool:
        wt = self.tmp / "merge" / slot.key.replace("/", "_")
        if wt.exists():
            git(self.clone, ["worktree", "remove", "--force", str(wt)])
        git(self.clone, ["worktree", "add", "--detach", str(wt), seed_head])
        proc = subprocess.run(
            ["git", "merge", "--no-commit", "--no-ff", result_sha],
            cwd=str(wt),
            capture_output=True,
            timeout=120,
            check=False,
        )
        ok = proc.returncode == 0
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=str(wt),
            capture_output=True,
            timeout=60,
            check=False,
        )
        git(self.clone, ["worktree", "remove", "--force", str(wt)])
        return ok

    async def accept(self, slot: Slot, metrics: dict) -> bool | None:
        sha = metrics.get("result_commit")
        if not sha:
            return None
        async with self.git_lock:
            return await asyncio.to_thread(self._accept_sync, slot, sha)

    def _accept_sync(self, slot: Slot, sha: str) -> bool:
        wt = self.tmp / "accept" / slot.key.replace("/", "_")
        if wt.exists():
            git(self.clone, ["worktree", "remove", "--force", str(wt)])
        git(self.clone, ["fetch", "origin", sha], token=self.token)
        git(self.clone, ["worktree", "add", "--detach", str(wt), sha])
        test = ROOT / "experiments" / "acceptance" / f"test_task_{slot.task.lower()}.py"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(wt)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                str(test),
                "--noconftest",
                f"--rootdir={wt}",
            ],
            cwd=str(wt),
            env=env,
            capture_output=True,
            timeout=120,
            check=False,
        )
        git(self.clone, ["worktree", "remove", "--force", str(wt)])
        return proc.returncode == 0


async def resolve_models(secrets: list[str]) -> str | None:
    """Confirm the pinned id on both APIs. None means stop before any live run."""
    apply_token_file()
    _backend_path()
    from app.services.cursor_client import CursorClient
    from app.services.treefleet_client import TreeFleetClient

    cursor = CursorClient()
    fleet = TreeFleetClient()
    try:
        try:
            cursor_ids = [str(item) for item in await cursor.list_models()]
        except Exception:
            print("Cursor list_models failed. No runs started.")
            return None
        try:
            fleet_ids = [str(item) for item in await fleet.list_models()]
        except Exception:
            print("TreeFleet GET /api/models failed. No runs started.")
            return None
    finally:
        await cursor._client.aclose()
        await fleet._client.aclose()
    for label, ids in (("Cursor", cursor_ids), ("TreeFleet", fleet_ids)):
        try:
            pin_model(ids)
        except Exception as exc:
            print(scrub(str(exc), secrets))
            print(f"{label} offered: {', '.join(ids) or '(none)'}")
            return None
    print(f"Pinned {PINNED_MODEL} is in Cursor list_models and TreeFleet GET /api/models.")
    return PINNED_MODEL
