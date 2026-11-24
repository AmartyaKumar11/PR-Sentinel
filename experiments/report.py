"""Summaries and figures from runs.jsonl. No network."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from experiments.core import CONDITIONS, load_rows, mean, sample_sd

ROOT = Path(__file__).resolve().parents[1]


def _nums(rows: list[dict], key: str) -> list[float]:
    values = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def _rate(rows: list[dict], pred) -> tuple[float | None, float | None, str]:
    flags = [1.0 if pred(row) else 0.0 for row in rows]
    if not rows:
        return None, None, "0/0"
    hits = int(sum(flags))
    return mean(flags), sample_sd(flags), f"{hits}/{len(rows)}"


def summarize(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        key = f"{row.get('task')}/{row.get('backend')}"
        grouped.setdefault(key, []).append(row)
    order = [f"{task}/{backend}" for task, backend in CONDITIONS]
    extra = [key for key in grouped if key not in order]
    table = []
    for key in order + extra:
        group = grouped.get(key) or []
        if not group:
            continue
        times = _nums(group, "time_to_result_s")
        files = _nums(group, "files_changed")
        added = _nums(group, "lines_added")
        removed = _nums(group, "lines_removed")
        success_mean, success_sd, success = _rate(group, lambda row: row.get("outcome") == "success")
        accept_flags = [row for row in group if row.get("acceptance_pass") in (True, False)]
        accept_mean, accept_sd, acceptance = _rate(
            accept_flags, lambda row: row.get("acceptance_pass") is True
        )
        merge_flags = [row for row in group if row.get("mergeable_into_pr_head") in (True, False)]
        merge_mean, merge_sd, mergeable = _rate(
            merge_flags, lambda row: row.get("mergeable_into_pr_head") is True
        )
        table.append(
            {
                "condition": key,
                "n": len(group),
                "success": success,
                "success_mean": success_mean,
                "success_sd": success_sd,
                "acceptance": acceptance,
                "acceptance_mean": accept_mean,
                "acceptance_sd": accept_sd,
                "time_mean": mean(times),
                "time_sd": sample_sd(times),
                "files_mean": mean(files),
                "files_sd": sample_sd(files),
                "added_mean": mean(added),
                "added_sd": sample_sd(added),
                "removed_mean": mean(removed),
                "removed_sd": sample_sd(removed),
                "mergeable": mergeable,
                "mergeable_mean": merge_mean,
                "mergeable_sd": merge_sd,
            }
        )
    return table


def _fmt(center: float | None, spread: float | None) -> str:
    if center is None:
        return "n/a"
    if spread is None:
        return f"{center:.2f} ± n/a"
    return f"{center:.2f} ± {spread:.2f}"


def write_summary(table: list[dict], dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    fields = [
        "condition",
        "n",
        "success",
        "acceptance",
        "time_mean_sd",
        "files_mean_sd",
        "added_mean_sd",
        "removed_mean_sd",
        "mergeable",
    ]
    with (dest / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in table:
            writer.writerow(
                {
                    "condition": row["condition"],
                    "n": row["n"],
                    "success": row["success"],
                    "acceptance": row["acceptance"],
                    "time_mean_sd": _fmt(row["time_mean"], row["time_sd"]),
                    "files_mean_sd": _fmt(row["files_mean"], row["files_sd"]),
                    "added_mean_sd": _fmt(row["added_mean"], row["added_sd"]),
                    "removed_mean_sd": _fmt(row["removed_mean"], row["removed_sd"]),
                    "mergeable": row["mergeable"],
                }
            )
    lines = [
        "# Execution backend experiment",
        "",
        "Success counts runs whose outcome is success. Acceptance is the harness test, not the quality gate.",
        "Spread is the sample standard deviation (n-1). Time is seconds. Lines are git numstat against the seeded head.",
        "",
        "| condition | n | success | acceptance | time (s) | files | lines added | lines removed | mergeable |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in table:
        lines.append(
            "| {condition} | {n} | {success} | {acceptance} | {time} | {files} | {added} | {removed} | {mergeable} |".format(
                condition=row["condition"],
                n=row["n"],
                success=row["success"],
                acceptance=row["acceptance"],
                time=_fmt(row["time_mean"], row["time_sd"]),
                files=_fmt(row["files_mean"], row["files_sd"]),
                added=_fmt(row["added_mean"], row["added_sd"]),
                removed=_fmt(row["removed_mean"], row["removed_sd"]),
                mergeable=row["mergeable"],
            )
        )
    lines.append("")
    (dest / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def write_figures(table: list[dict], dest: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not table:
        return
    labels = [row["condition"] for row in table]
    x = list(range(len(labels)))
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    ax.bar(
        x,
        [row["time_mean"] or 0 for row in table],
        yerr=[row["time_sd"] or 0 for row in table],
        capsize=4,
        color="#3c6e71",
        label="Mean time to result",
    )
    ax.set_xticks(x, labels, rotation=20, ha="right")
    ax.set_xlabel("Condition")
    ax.set_ylabel("Time to result (s)")
    ax.set_title("Time to result by condition")
    ax.legend()
    fig.tight_layout()
    fig.savefig(dest / "fig_time.pdf")
    plt.close(fig)

    width = 0.25
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    series = (
        ("Success", "success_mean", "success_sd", -width),
        ("Acceptance", "acceptance_mean", "acceptance_sd", 0),
        ("Mergeable", "mergeable_mean", "mergeable_sd", width),
    )
    for label, mean_key, sd_key, shift in series:
        ax.bar(
            [item + shift for item in x],
            [row[mean_key] or 0 for row in table],
            width,
            yerr=[row[sd_key] or 0 for row in table],
            capsize=3,
            label=label,
        )
    ax.set_xticks(x, labels, rotation=20, ha="right")
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("Condition")
    ax.set_ylabel("Rate")
    ax.set_title("Success, acceptance, and mergeable rate by condition")
    ax.legend()
    fig.tight_layout()
    fig.savefig(dest / "fig_success.pdf")
    plt.close(fig)


def write_report(expid: str, root: Path | None = None) -> Path:
    base = root or ROOT
    dest = base / "experiments" / "results" / expid
    table = summarize(load_rows(dest / "runs.jsonl"))
    write_summary(table, dest)
    write_figures(table, dest)
    return dest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expid", required=True)
    args = parser.parse_args(argv)
    dest = write_report(args.expid)
    print(f"wrote {dest / 'summary.md'}")


if __name__ == "__main__":
    main()
