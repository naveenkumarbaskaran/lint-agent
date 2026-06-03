"""LintAgent: uses Claude to flag code that deviates from learned style patterns."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import anthropic

MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Tool implementations (executed locally, called by the model)
# ---------------------------------------------------------------------------

def _read_file(path: str) -> str:
    """Return the text contents of a file, or an error message."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return f"ERROR: file not found: {path}"
    except OSError as exc:
        return f"ERROR: {exc}"


def _list_files(directory: str, pattern: str = "**/*.py") -> list[str]:
    """Return a list of file paths matching *pattern* under *directory*."""
    base = Path(directory)
    if not base.exists():
        return []
    return sorted(str(p) for p in base.glob(pattern) if p.is_file())


def _get_git_diff(base_ref: str = "HEAD") -> str:
    """Return the unified diff of staged + unstaged changes against *base_ref*."""
    try:
        result = subprocess.run(
            ["git", "diff", base_ref],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout or "(no diff output)"
    except FileNotFoundError:
        return "ERROR: git not found in PATH"
    except subprocess.TimeoutExpired:
        return "ERROR: git diff timed out"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


# ---------------------------------------------------------------------------
# Tool schema definitions sent to the model
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": (
            "Read the full text content of a source file. "
            "Use this to examine specific files for style analysis or to check "
            "new code against learned patterns."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to read.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": (
            "List source files under a directory that match a glob pattern. "
            "Defaults to all Python files (``**/*.py``)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dir": {
                    "type": "string",
                    "description": "Directory to search under.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern relative to *dir*. Default: **/*.py",
                },
            },
            "required": ["dir"],
        },
    },
    {
        "name": "get_git_diff",
        "description": (
            "Return the unified diff of current uncommitted changes versus a git "
            "reference (default: HEAD). Useful for checking only the code that "
            "is new or modified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base_ref": {
                    "type": "string",
                    "description": "Git ref to diff against. Default: HEAD",
                }
            },
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def _dispatch_tool(name: str, inputs: dict[str, Any]) -> str:
    if name == "read_file":
        return _read_file(inputs["path"])
    if name == "list_files":
        return json.dumps(
            _list_files(inputs["dir"], inputs.get("pattern", "**/*.py"))
        )
    if name == "get_git_diff":
        return _get_git_diff(inputs.get("base_ref", "HEAD"))
    return f"ERROR: unknown tool '{name}'"


# ---------------------------------------------------------------------------
# LintAgent
# ---------------------------------------------------------------------------

class LintAgent:
    """An AI-powered lint agent that learns codebase style and flags deviations.

    Parameters
    ----------
    style_profile:
        A dict produced by :class:`~lint_agent.learner.StyleLearner` that
        describes the codebase's naming, import, and structural conventions.
    api_key:
        Anthropic API key.  Falls back to the ``ANTHROPIC_API_KEY`` env var.
    model:
        Claude model string to use.  Defaults to ``claude-sonnet-4-6``.
    """

    def __init__(
        self,
        style_profile: dict[str, Any] | None = None,
        api_key: str | None = None,
        model: str = MODEL,
    ) -> None:
        self.style_profile = style_profile or {}
        self.model = model
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, target: str) -> list[dict[str, Any]]:
        """Check *target* (file or directory) against the learned style profile.

        Returns a list of violation dicts, each with keys:
        ``file``, ``line`` (optional), ``rule``, ``message``, ``severity``.
        """
        system_prompt = self._build_system_prompt()
        user_message = self._build_check_request(target)

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_message}
        ]

        # Agentic loop — keep going until the model stops requesting tools
        while True:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
            )

            # Append assistant turn to history
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                break

            # Execute requested tool calls and collect results
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type == "tool_use":
                    result_text = _dispatch_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})

        # Extract the final text reply and parse violations from it
        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text += block.text

        return self._parse_violations(final_text)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        profile_json = json.dumps(self.style_profile, indent=2)
        return f"""You are an expert code reviewer acting as a lint tool.

You have been given a codebase style profile extracted by static analysis.
Your task is to check whether code in the target file or directory follows
the conventions captured in that profile.

STYLE PROFILE
=============
{profile_json}

When checking code you MUST:
1. Use the provided tools to read the actual source files.
2. Compare each file against the style profile conventions.
3. Report ONLY concrete, actionable violations — do not report style elements
   that are consistent with the profile.
4. Return your findings as a JSON array (and nothing else) in this exact format:

```json
[
  {{
    "file": "<relative or absolute path>",
    "line": <integer or null>,
    "rule": "<short rule identifier, e.g. naming/function>",
    "message": "<concise human-readable description>",
    "severity": "<error|warning|info>"
  }}
]
```

If there are no violations, return an empty array: `[]`.
Do NOT include any text outside the JSON code block.
"""

    def _build_check_request(self, target: str) -> str:
        return (
            f"Please check the following target for style violations: `{target}`\n\n"
            "Use the available tools to list and read the relevant source files, "
            "then compare them to the style profile and return your findings."
        )

    @staticmethod
    def _parse_violations(text: str) -> list[dict[str, Any]]:
        """Extract the JSON violations array from the model's response text."""
        # Try to find a ```json ... ``` block first
        json_block = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
        if json_block:
            raw = json_block.group(1)
        else:
            # Fall back to the first [ ... ] span in the text
            bracket_match = re.search(r"(\[.*\])", text, re.DOTALL)
            raw = bracket_match.group(1) if bracket_match else "[]"

        try:
            violations = json.loads(raw)
            if not isinstance(violations, list):
                return []
            return violations
        except json.JSONDecodeError:
            return []
