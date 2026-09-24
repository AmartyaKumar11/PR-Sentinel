"""Rich embed builders for Discord notifications."""

import json

import discord

SEVERITY_COLORS = {
    "TRIVIAL": 0x9CA3AF,
    "LOW": 0x22C55E,
    "MEDIUM": 0xEAB308,
    "HIGH": 0xF97316,
    "CRITICAL": 0xEF4444,
}


def _load(value, fallback):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value if value is not None else fallback


def build_task_embed(task: dict) -> discord.Embed:
    severity = task.get("severity", "MEDIUM")
    diag = _load(task.get("diagnosis_json"), {})
    intent = diag.get("intent_alignment") or {}
    blast = diag.get("blast_radius") or {}
    jev = _load(task.get("jev_confidences"), {})
    icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡"}.get(severity, "🟢")

    embed = discord.Embed(
        title=f"{icon} PR #{task.get('pr_number')} — {severity}",
        description=task.get("suggested_fix") or "Review needed.",
        color=SEVERITY_COLORS.get(severity, 0x9CA3AF),
    )
    missing = intent.get("missing") or []
    if missing:
        embed.add_field(
            name="⚠️ Missing requirements",
            value="\n".join(f"• {m}" for m in missing[:5]),
            inline=False,
        )
    creep = intent.get("scope_creep") or []
    if creep:
        embed.add_field(
            name="🔀 Scope creep",
            value="\n".join(f"• {c}" for c in creep[:3]),
            inline=False,
        )
    embed.add_field(
        name="💥 Blast radius",
        value=(
            f"Risk: {blast.get('risk_score', 0):.2f} | "
            f"Path: `{blast.get('highest_risk_path') or 'N/A'}`"
        ),
        inline=False,
    )
    if jev:
        embed.add_field(
            name="🤖 Jev confidence",
            value=(
                f"Auth: {jev.get('touches_auth', 0):.0%} | "
                f"Trivial: {jev.get('is_trivial', 0):.0%} | "
                f"Risk: {jev.get('risk_level', 0):.1f}/3"
            ),
            inline=False,
        )
    embed.set_footer(text=f"Repo: {task.get('repo')} | Task: {str(task.get('id', ''))[:8]}")
    return embed


def build_pr_notice_embed(meta: dict) -> discord.Embed:
    title = meta.get("title") or "Pull request"
    body = (meta.get("body") or "").strip()
    description = title if not body else f"{title}\n\n{body[:300]}"
    embed = discord.Embed(
        title=f"New PR #{meta.get('pr_number')} — {meta.get('repo')}",
        description=description[:4000],
        color=0x6366F1,
    )
    files = meta.get("files") or []
    embed.add_field(
        name="Files changed",
        value="\n".join(f"• {name}" for name in files[:15]) or "none listed",
        inline=False,
    )
    embed.add_field(name="Author", value=meta.get("author") or "unknown", inline=True)
    embed.add_field(
        name="Branch",
        value=f"`{meta.get('head_ref') or '?'}` → `{meta.get('base_ref') or '?'}`",
        inline=True,
    )
    return embed


def build_agent_result_embed(result: dict) -> discord.Embed:
    status = result.get("status", "unknown")
    color = 0x22C55E if status == "completed" else 0xEF4444
    embed = discord.Embed(
        title=f"{'✅' if status == 'completed' else '❌'} Cursor Agent {status.title()}",
        color=color,
    )
    if result.get("branch"):
        embed.add_field(name="Branch", value=f"`{result['branch']}`", inline=True)
    if result.get("pr_url"):
        embed.add_field(name="Pull Request", value=result["pr_url"], inline=True)
    if result.get("token_usage"):
        embed.add_field(name="Tokens used", value=str(result["token_usage"]), inline=True)
    if result.get("result_text"):
        embed.add_field(name="Summary", value=result["result_text"][:500], inline=False)
    if result.get("quality_warning"):
        embed.add_field(name="Jev warning", value=result["quality_warning"], inline=False)
    return embed


def build_verification_embed(verification: dict) -> discord.Embed:
    all_resolved = verification.get("all_resolved", False)
    embed = discord.Embed(
        title=f"{'✅' if all_resolved else '⚠️'} Verification {'Passed' if all_resolved else 'Partial'}",
        color=0x22C55E if all_resolved else 0xEAB308,
    )
    if verification.get("resolved_items"):
        embed.add_field(
            name="Resolved",
            value="\n".join(f"✅ {r}" for r in verification["resolved_items"]),
            inline=False,
        )
    if verification.get("remaining_items"):
        embed.add_field(
            name="Still missing",
            value="\n".join(f"⚠️ {r}" for r in verification["remaining_items"]),
            inline=False,
        )
    return embed


def build_validation_failed_embed(gate: dict, result: dict) -> discord.Embed:
    embed = discord.Embed(
        title="Validation Failed",
        description=(gate.get("summary") or "The fix did not pass the quality gate.")[:500],
        color=0xEF4444,
    )
    embed.add_field(name="CI", value=str(gate.get("ci_status") or "n/a"), inline=True)
    scores = gate.get("requirement_alignment") or {}
    if scores:
        embed.add_field(
            name="Requirements",
            value="\n".join(f"• {name}: {score:.2f}" for name, score in list(scores.items())[:6])[:1000],
            inline=False,
        )
    sanity = gate.get("diff_sanity") or {}
    if sanity:
        embed.add_field(
            name="Diff",
            value=(
                f"Lines: {sanity.get('lines_changed', 0)}\n"
                f"Tests deleted: {sanity.get('test_files_deleted')}\n"
                f"CI config touched: {sanity.get('ci_config_modified')}"
            )[:1000],
            inline=False,
        )
    if result.get("pr_url"):
        embed.add_field(name="Pull Request", value=result["pr_url"], inline=False)
    return embed


def build_needs_review_embed(gate: dict, result: dict) -> discord.Embed:
    scores = gate.get("requirement_alignment") or {}
    unsure = [f"• {name}: {score:.2f}" for name, score in scores.items() if 0.4 <= score <= 0.6]
    embed = discord.Embed(
        title="Needs Review",
        description=(gate.get("summary") or "Alignment is uncertain.")[:500],
        color=0xEAB308,
    )
    embed.add_field(
        name="Uncertain requirements",
        value="\n".join(unsure)[:1000] or "none",
        inline=False,
    )
    if result.get("pr_url"):
        embed.add_field(name="Pull Request", value=result["pr_url"], inline=False)
    return embed
