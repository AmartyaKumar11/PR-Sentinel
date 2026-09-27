"""Repo context: cold read, reinforcement, injection. Blast radius stays put."""

import json
from pathlib import Path

import pytest

from app.database import get_db
from app.services.blast_radius import trace
from app.services.context_sampler import sample_files
from app.services.graph_builder import build_graph
from app.services.prompt_crafter import craft_prompt
from app.services.repo_context import (
    _blank,
    _refresh_tasks,
    apply_review,
    bootstrap_context,
    collapse_fact,
    config_hashes,
    context_for_diagnosis,
    contradicted_facts,
    correct_failed_context,
    extract_hard_facts,
    load_context,
    merge_hard_facts,
    merge_instruction_facts,
    parse_coderabbit,
    pin_conventions,
    project_row,
    read_instruction_files,
    reinforce,
    render_context,
    seed_conventions,
    select_source_files,
    stamp_instruction_hashes,
)
from app.services.task_manager import create_task


def _now_row(repo, files, paths, sources):
    return project_row(_blank(repo, "2026-09-27T00:00:00+00:00"), files, paths, sources)


def test_injection_phrasing_scales_with_confidence():
    text = render_context(
        {
            "hard_facts": {
                "test_framework": {
                    "value": "vitest",
                    "confidence": 1.0,
                    "source": "config",
                    "overridden_by_user": False,
                }
            },
            "conventions": {
                "test_style": {"dominant": "colocated", "confidence": 0.6},
                "error_handling": {"dominant": "throw", "confidence": 0.4},
            },
        }
    )
    assert "This repo uses vitest." in text
    assert "leans toward" in text
    assert "throwing" not in text


def test_decay_flips_an_old_dominant_convention():
    conv = {
        "import_style": {
            "counts": {"absolute": 80},
            "evidence_count": 80,
            "dominant": "absolute",
            "confidence": 1,
            "overridden_by_user": False,
        }
    }
    for _ in range(8):
        conv = reinforce(conv, {"import_style": {"relative": 3}}, "t")
    assert conv["import_style"]["dominant"] == "absolute"
    for _ in range(12):
        conv = reinforce(conv, {"import_style": {"relative": 3}}, "t")
    assert conv["import_style"]["dominant"] == "relative"


def test_user_override_keeps_dominant_when_samples_contradict():
    conv = reinforce(
        {
            "import_style": {
                "counts": {"absolute": 10},
                "evidence_count": 10,
                "dominant": "absolute",
                "confidence": 1,
                "overridden_by_user": True,
            }
        },
        {"import_style": {"relative": 40}},
        "t",
    )
    item = conv["import_style"]
    assert item["dominant"] == "absolute"
    assert item["confidence"] == 1
    assert item["counts"]["relative"] > item["counts"]["absolute"]


def test_config_contradiction_clears_a_user_override():
    old_files = {"package.json": '{"devDependencies":{"jest":"1"}}'}
    hashes = config_hashes(old_files, [])
    facts, _hashes = merge_hard_facts(
        {
            "test_framework": {
                "value": "jest",
                "confidence": 1,
                "source": "user_override",
                "overridden_by_user": True,
            }
        },
        hashes,
        {"package.json": '{"devDependencies":{"vitest":"2"}}'},
        [],
        "t",
    )
    assert facts["test_framework"]["value"] == "vitest"
    assert facts["test_framework"]["overridden_by_user"] is False


def test_self_correction_collapses_a_context_caused_failure():
    facts = {
        "test_framework": {
            "value": "jest",
            "confidence": 1.0,
            "source": "config",
            "overridden_by_user": False,
        }
    }
    assert contradicted_facts(facts, {"summary": "vitest failed"}, "import jest from 'jest'") == [
        "test_framework"
    ]
    collapse_fact(facts, "test_framework", "t")
    assert facts["test_framework"]["confidence"] == 0
    assert "jest" not in render_context({"hard_facts": facts, "conventions": {}})


def test_sampler_uses_the_parser_not_a_regex_of_the_diff():
    py = sample_files({"src/auth.py": "from src.users import get_user\nfrom . import local\n"})
    assert py["import_style"] == {"absolute": 1, "relative": 1}
    ts = sample_files(
        {
            "src/Button.ts": 'import { x } from "@/components/x"\nexport function Button() { throw new Error("no") }\n'
        }
    )
    assert ts["import_style"]["alias"] == 1
    assert ts["error_handling"]["throw"] == 1
    assert sample_files({"src/broken.ts": "function { broken ("}) == {}


def test_vitest_repo_prompt_names_framework_and_import_style():
    files = {
        "package.json": json.dumps({"devDependencies": {"vitest": "2.0.0", "typescript": "5.4.0"}}),
        "tsconfig.json": '{"compilerOptions": {"strict": true}}',
        "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
    }
    sources = {"src/Button.ts": 'import { x } from "@/components/x"\n'}
    row = _now_row("acme/web", {**files, **sources}, ["src/Button.ts"], sources)
    text = render_context(row)
    assert "vitest" in text.lower()
    assert "alias" in text.lower()
    assert row["hard_facts"]["package_manager"]["value"] == "pnpm"
    assert row["hard_facts"]["type_checker"]["value"] == "tsc strict"


def test_demo_python_facts_match_and_blast_radius_is_unchanged():
    paths = ["src/auth.py", "src/users.py", "tests/test_auth.py"]
    facts = extract_hard_facts({}, paths)
    assert facts["language"]["value"] == "python"
    assert facts["test_framework"]["value"] == "pytest"
    assert facts["test_framework"]["confidence"] == 1.0
    assert facts["test_location"]["value"] == "tests/"

    root = Path(__file__).resolve().parents[2] / "demo-repo"
    files = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in root.rglob("*.py")}
    graph = build_graph(files)
    seed = next(node["id"] for node in graph["nodes"] if node["id"].endswith("validate_token"))
    before = trace([seed], graph)
    render_context(
        {
            "hard_facts": facts,
            "conventions": {"import_style": {"dominant": "absolute", "confidence": 0.9}},
        }
    )
    after = trace([seed], graph)
    assert before == after
    assert before["risk_score"] > 0
    assert before["highest_risk_path"]


@pytest.mark.asyncio
async def test_first_review_reads_facts_and_leaves_conventions_empty():
    db = await get_db()
    row = await bootstrap_context(db, "o/demo", {}, ["src/auth.py", "tests/test_auth.py"])
    assert row["conventions"] == {}
    assert row["hard_facts"]["language"]["value"] == "python"
    assert row["hard_facts"]["test_framework"]["source"] == "config"


@pytest.mark.asyncio
async def test_cache_hit_is_a_single_query():
    db = await get_db()
    await bootstrap_context(db, "o/demo", {}, ["src/auth.py", "tests/test_auth.py"])

    class Counter:
        def __init__(self):
            self.n = 0

        async def execute(self, *args, **kwargs):
            self.n += 1
            return await db.execute(*args, **kwargs)

    counter = Counter()
    row = await load_context(counter, "o/demo")
    assert counter.n == 1
    assert row["hard_facts"]["test_framework"]["value"] == "pytest"


@pytest.mark.asyncio
async def test_reinforcement_accumulates_across_diffs():
    db = await get_db()
    repo = "o/demo"
    paths = ["src/auth.py"]
    files = {"src/auth.py": "x = 1\n"}
    await bootstrap_context(db, repo, files, paths)
    first_src = {"src/auth.py": "from src.users import get_user\n"}
    first = await apply_review(db, repo, files, paths, first_src)
    second_src = {"src/auth.py": "from src.users import get_user\nfrom src.orders import create_order\n"}
    second = await apply_review(db, repo, files, paths, second_src)
    assert second["conventions"]["import_style"]["dominant"] == "absolute"
    assert second["conventions"]["import_style"]["evidence_count"] > first["conventions"]["import_style"]["evidence_count"]


@pytest.mark.asyncio
async def test_correction_hook_drops_the_fact_from_the_stored_row():
    db = await get_db()
    repo = "o/demo"
    row = _now_row(
        repo,
        {"package.json": '{"devDependencies":{"jest":"29"}}'},
        [],
        None,
    )
    from app.services.repo_context import save_context

    await save_context(db, row)
    await create_task(db, "t1", repo, 1, "abc", "MEDIUM", "dispatch", {}, {}, "", "use jest")
    note = await correct_failed_context(
        "t1",
        {"summary": "vitest exited 1"},
        "the agent wrote a jest test",
    )
    assert "test_framework" in note
    assert "jest" not in note.lower().split("removed", 1)[-1]
    stored = await load_context(db, repo)
    assert stored["hard_facts"]["test_framework"]["confidence"] == 0


@pytest.mark.asyncio
async def test_composer_prompt_includes_the_context_block():
    block = render_context(
        {
            "hard_facts": {
                "test_framework": {
                    "value": "vitest",
                    "confidence": 1,
                    "source": "config",
                    "overridden_by_user": False,
                }
            },
            "conventions": {
                "import_style": {"dominant": "alias", "confidence": 0.9},
            },
        }
    )

    class Fake:
        def __init__(self):
            self.system = ""

        async def chat(self, system, messages, **kwargs):
            self.system = system
            return "Fix the button."

    llm = Fake()
    text = await craft_prompt(
        {"intent_alignment": {"missing": []}, "changed_files": [], "blast_radius": {}},
        {"severity": "HIGH"},
        deepseek=llm,
        context_prompt=block,
    )
    assert "vitest" in text.lower()
    assert "alias" in text.lower()
    assert "vitest" in llm.system.lower()
    assert text.startswith("Fix the button.")


@pytest.mark.asyncio
async def test_context_endpoint_returns_the_row():
    from app.routes.reviews import repo_context

    db = await get_db()
    await bootstrap_context(db, "o/demo", {}, ["src/auth.py", "tests/test_auth.py"])
    payload = await repo_context("o/demo")
    assert payload["hard_facts"]["language"]["value"] == "python"
    assert payload["conventions"] == {}
    empty = await repo_context("missing/repo")
    assert empty["hard_facts"] == {}


def test_over_cap_samples_across_directories_not_one_folder():
    paths = [f"aaa/f{i:04d}.py" for i in range(600)] + ["zzz/keep.py"]
    picked = select_source_files(paths)
    assert len(picked) == 500
    assert "zzz/keep.py" in picked
    assert sum(path.startswith("aaa/") for path in picked) == 499


@pytest.mark.asyncio
async def test_full_index_seeds_conventions_once_and_caps(monkeypatch):
    calls = {"tree": 0}

    class Fake:
        async def list_tree_paths(self, owner, repo, ref):
            calls["tree"] += 1
            return ["src/a.py", "src/b.py", "README.md"]

        async def list_dir(self, *_a, **_k):
            return []

        async def read_text(self, owner, repo, path, ref):
            if path == "src/a.py":
                return "from src.b import b\n"
            if path == "src/b.py":
                return "x = 1\n"
            return None

        async def close(self):
            return None

    monkeypatch.setattr("app.services.github_client.GitHubClient", lambda: Fake())
    from app.services.repo_context import _run_full_index

    await _run_full_index("o", "wide", "sha")
    row = await load_context(await get_db(), "o/wide")
    assert row["full_index_done"] == 1
    assert row["conventions"]["import_style"]["dominant"] == "absolute"
    assert row["conventions"]["import_style"]["evidence_count"] == 2
    assert len(select_source_files([f"pkg/f{i}.py" for i in range(800)])) == 500
    await _run_full_index("o", "wide", "sha")
    assert calls["tree"] == 1


@pytest.mark.asyncio
async def test_first_review_returns_before_full_index_finishes(monkeypatch):
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()

    async def hang(_owner, _repo, _ref):
        started.set()
        await release.wait()

    def kick(owner, repo, ref):
        task = asyncio.create_task(hang(owner, repo, ref))
        _refresh_tasks.add(task)
        task.add_done_callback(_refresh_tasks.discard)

    async def snap(_owner, _repo, _ref, extra=None):
        return {}, ["src/auth.py", "tests/test_auth.py"]

    monkeypatch.setattr("app.services.repo_context.spawn_full_index", kick)
    monkeypatch.setattr("app.services.repo_context.fetch_snapshot", snap)
    try:
        text = await context_for_diagnosis("o", "fresh", "sha")
        assert started.is_set()
        assert "pytest" in text
        assert "alias" not in text
    finally:
        release.set()
        pending = [task for task in list(_refresh_tasks) if not task.done()]
        if pending:
            await asyncio.gather(*pending)


def test_coderabbit_yaml_becomes_an_instruction_fact():
    text = "reviews:\n  path_instructions:\n    - instructions: Use pytest and absolute imports.\n"
    hard, pins = parse_coderabbit(text)
    assert hard["test_framework"]["value"] == "pytest"
    assert hard["test_framework"]["confidence"] == 0.98
    assert hard["test_framework"]["source"] == "instruction_file"
    assert pins["import_style"] == ("absolute", ".coderabbit.yaml")
    shown = render_context({"hard_facts": hard, "conventions": {}})
    assert "This repo's .coderabbit.yaml requires pytest." in shown


@pytest.mark.asyncio
async def test_agents_md_is_one_deepseek_pass():
    class LLM:
        def __init__(self):
            self.n = 0

        async def chat(self, system, messages, thinking=True):
            self.n += 1
            assert thinking is False
            return (
                '{"test_framework":"pytest","import_style":"relative","file_naming":"",'
                '"error_handling":"","test_style":"","rules":"Keep handlers small."}'
            )

    llm = LLM()
    files = {"AGENTS.md": "Use pytest. Relative imports only. Keep handlers small."}
    first = await read_instruction_files(files, {}, llm)
    assert llm.n == 1
    assert first["hard"]["test_framework"]["confidence"] == 0.98
    assert first["hard"]["test_framework"]["source"] == "instruction_file"
    assert first["pins"]["import_style"][0] == "relative"
    assert "Keep handlers small." in first["text"]
    hashes = config_hashes(files, [])
    stamp_instruction_hashes(hashes, files)
    again = await read_instruction_files(files, hashes, llm)
    assert again is None
    assert llm.n == 1
    shown = render_context(
        {
            "hard_facts": first["hard"],
            "conventions": pin_conventions({}, first["pins"], "t"),
            "instruction_text": first["text"],
        }
    )
    assert "This repo's AGENTS.md requires pytest." in shown
    assert "Repo rules:" in shown


def test_instruction_file_beats_inferred_and_config_beats_instruction():
    seeded = seed_conventions({"import_style": {"absolute": 40}}, 40, "t")
    pinned = pin_conventions(seeded, {"import_style": ("relative", "AGENTS.md")}, "t")
    again = reinforce(pinned, {"import_style": {"absolute": 20}}, "t")
    assert again["import_style"]["dominant"] == "relative"
    assert again["import_style"]["confidence"] == 0.98

    facts = extract_hard_facts({}, ["src/auth.py", "tests/test_auth.py"])
    hard = {
        "test_framework": {
            "value": "unittest",
            "confidence": 0.98,
            "source": "instruction_file",
            "origin": "AGENTS.md",
            "overridden_by_user": False,
        }
    }
    merged = merge_instruction_facts(facts, hard)
    assert merged["test_framework"]["value"] == "pytest"
    assert merged["test_framework"]["source"] == "config"
