"""Command-line interface for lint-agent."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from .agent import LintAgent
from .learner import StyleLearner

console = Console()
err_console = Console(stderr=True)

DEFAULT_PROFILE = ".lint-agent.json"


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option()
def cli() -> None:
    """lint-agent — AI-powered linting that learns your codebase style."""


# ---------------------------------------------------------------------------
# train command
# ---------------------------------------------------------------------------

@cli.command("train")
@click.argument("src_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--save",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Where to save the learned style profile (JSON).",
)
@click.option(
    "--max-files",
    default=200,
    show_default=True,
    help="Maximum number of source files to sample.",
)
@click.option(
    "--quiet", "-q",
    is_flag=True,
    help="Suppress progress output.",
)
def train(
    src_dir: Path,
    save: str,
    max_files: int,
    quiet: bool,
) -> None:
    """Learn style conventions from SRC_DIR and save a profile.

    Example:

        lint-agent train ./src --save .lint-agent.json
    """
    if not quiet:
        console.print(f"[bold cyan]Scanning[/bold cyan] {src_dir} for style patterns …")

    learner = StyleLearner(max_files=max_files)
    profile = learner.learn(str(src_dir))

    learner.save(profile, save)

    if not quiet:
        _print_profile_summary(profile, save)

    sys.exit(0)


def _print_profile_summary(profile: dict[str, Any], saved_to: str) -> None:
    """Pretty-print a summary of the learned profile."""
    console.print()
    console.print(
        Panel(
            f"[bold green]Profile saved[/bold green] → [yellow]{saved_to}[/yellow]",
            title="lint-agent train",
            box=box.ROUNDED,
        )
    )

    table = Table(box=box.SIMPLE_HEAD, show_header=True)
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Files analysed", str(profile.get("files_analysed", "?")))
    naming = profile.get("naming_conventions", {})
    table.add_row("Function naming", naming.get("functions", "?"))
    table.add_row("Class naming", naming.get("classes", "?"))
    fl = profile.get("function_length", {})
    if fl.get("p50") is not None:
        table.add_row("Median function length", f"{fl['p50']} lines")
    if fl.get("max_common") is not None:
        table.add_row("95th-pct function length", f"{fl['max_common']} lines")
    doc_pct = profile.get("docstring_coverage_pct")
    if doc_pct is not None:
        table.add_row("Docstring coverage", f"{doc_pct}%")
    ann_pct = profile.get("type_annotation_rate_pct")
    if ann_pct is not None:
        table.add_row("Type annotation rate", f"{ann_pct}%")
    table.add_row("Indent style", profile.get("indent_style", "?"))
    table.add_row("Quote style", profile.get("quote_style", "?"))

    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# check command
# ---------------------------------------------------------------------------

@cli.command("check")
@click.argument(
    "target",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--profile",
    "-p",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Path to the style profile JSON produced by `train`.",
)
@click.option(
    "--model",
    default="claude-sonnet-4-6",
    show_default=True,
    help="Claude model to use for analysis.",
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["rich", "json", "github"]),
    default="rich",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--fail-on",
    type=click.Choice(["error", "warning", "info", "never"]),
    default="error",
    show_default=True,
    help="Exit with non-zero status when violations at or above this severity are found.",
)
def check(
    target: Path,
    profile: str,
    model: str,
    output: str,
    fail_on: str,
) -> None:
    """Check a file or directory against the learned style profile.

    Example:

        lint-agent check ./src/mymodule.py
        lint-agent check ./src --output json
    """
    # Load profile
    profile_path = Path(profile)
    if not profile_path.exists():
        err_console.print(
            f"[bold red]Error:[/bold red] profile not found: {profile}\n"
            "Run `lint-agent train <src_dir>` first."
        )
        sys.exit(2)

    try:
        style_profile = StyleLearner.load(str(profile_path))
    except (json.JSONDecodeError, OSError) as exc:
        err_console.print(f"[bold red]Error loading profile:[/bold red] {exc}")
        sys.exit(2)

    # Run the agent
    if output == "rich":
        console.print(f"[bold cyan]Checking[/bold cyan] {target} …")

    agent = LintAgent(style_profile=style_profile, model=model)

    try:
        violations = agent.check(str(target))
    except anthropic_import_error() as exc:  # type: ignore[misc]
        err_console.print(f"[bold red]Anthropic API error:[/bold red] {exc}")
        sys.exit(2)

    # Render output
    if output == "json":
        click.echo(json.dumps(violations, indent=2))
    elif output == "github":
        _render_github_annotations(violations)
    else:
        _render_rich_violations(violations, str(target))

    # Exit code
    if fail_on == "never":
        sys.exit(0)

    severity_rank = {"info": 0, "warning": 1, "error": 2}
    threshold = severity_rank.get(fail_on, 2)
    worst = max(
        (severity_rank.get(v.get("severity", "info"), 0) for v in violations),
        default=-1,
    )
    sys.exit(1 if worst >= threshold else 0)


def anthropic_import_error():
    """Return the Anthropic API error base class (lazy import to keep startup fast)."""
    try:
        import anthropic  # noqa: PLC0415
        return anthropic.APIError
    except ImportError:
        return Exception


def _render_rich_violations(
    violations: list[dict[str, Any]], target: str
) -> None:
    if not violations:
        console.print("[bold green]No violations found.[/bold green] ✨")
        return

    severity_colour = {"error": "red", "warning": "yellow", "info": "blue"}

    table = Table(
        title=f"Violations in {target}",
        box=box.ROUNDED,
        show_header=True,
        expand=True,
    )
    table.add_column("Severity", width=10)
    table.add_column("File", no_wrap=False, ratio=3)
    table.add_column("Line", width=6)
    table.add_column("Rule", ratio=2)
    table.add_column("Message", ratio=5)

    for v in violations:
        sev = v.get("severity", "info")
        colour = severity_colour.get(sev, "white")
        table.add_row(
            f"[{colour}]{sev.upper()}[/{colour}]",
            v.get("file", ""),
            str(v.get("line") or ""),
            v.get("rule", ""),
            v.get("message", ""),
        )

    console.print()
    console.print(table)
    console.print(
        f"\n[bold]Total:[/bold] {len(violations)} violation(s)"
    )


def _render_github_annotations(violations: list[dict[str, Any]]) -> None:
    """Emit GitHub Actions annotation commands."""
    level_map = {"error": "error", "warning": "warning", "info": "notice"}
    for v in violations:
        level = level_map.get(v.get("severity", "info"), "notice")
        file_ = v.get("file", "")
        line = v.get("line", "")
        rule = v.get("rule", "lint-agent")
        message = v.get("message", "style violation")
        location = f"file={file_}"
        if line:
            location += f",line={line}"
        click.echo(f"::{level} {location},title={rule}::{message}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cli()


if __name__ == "__main__":
    main()
