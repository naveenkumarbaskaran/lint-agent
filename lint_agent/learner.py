"""StyleLearner: statically extracts coding conventions from a Python codebase."""

from __future__ import annotations

import ast
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


class StyleLearner:
    """Sample source files and extract naming, import, and structural patterns.

    Parameters
    ----------
    max_files:
        Maximum number of files to sample when scanning a directory.
    """

    def __init__(self, max_files: int = 200) -> None:
        self.max_files = max_files
        self._reset()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def learn(self, src_dir: str) -> dict[str, Any]:
        """Scan *src_dir* and return a style profile dict."""
        self._reset()
        files = self._collect_files(src_dir)
        for path in files:
            self._analyse_file(path)
        return self._build_profile()

    def save(self, profile: dict[str, Any], dest: str) -> None:
        """Persist *profile* as JSON to *dest*."""
        Path(dest).write_text(json.dumps(profile, indent=2), encoding="utf-8")

    @staticmethod
    def load(src: str) -> dict[str, Any]:
        """Load a previously saved profile from *src*."""
        return json.loads(Path(src).read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Private collection helpers
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self._function_names: list[str] = []
        self._class_names: list[str] = []
        self._variable_names: list[str] = []
        self._constant_names: list[str] = []
        self._import_styles: Counter[str] = Counter()   # "import X" vs "from X import Y"
        self._import_modules: Counter[str] = Counter()  # top-level module names
        self._function_lengths: list[int] = []          # lines per function
        self._file_lengths: list[int] = []              # lines per file
        self._docstring_coverage: list[bool] = []       # True if function has docstring
        self._type_annotation_rate: list[bool] = []     # True if function has annotations
        self._indent_sizes: Counter[int] = Counter()
        self._quote_styles: Counter[str] = Counter()    # single vs double
        self._files_analysed: int = 0
        self._errors: list[str] = []

    def _collect_files(
        self, src_dir: str, pattern: str = "**/*.py"
    ) -> list[Path]:
        base = Path(src_dir)
        files = sorted(base.glob(pattern))
        # Skip common noise directories
        files = [
            f
            for f in files
            if not any(
                part.startswith(".")
                or part in ("__pycache__", "node_modules", ".venv", "venv", "env", "dist", "build")
                for part in f.parts
            )
            and f.is_file()
        ]
        return files[: self.max_files]

    # ------------------------------------------------------------------
    # Per-file analysis
    # ------------------------------------------------------------------

    def _analyse_file(self, path: Path) -> None:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        lines = source.splitlines()
        self._file_lengths.append(len(lines))
        self._files_analysed += 1

        # Indentation — look at lines that start with whitespace
        for line in lines:
            stripped = line.lstrip()
            if stripped and line != stripped:
                indent = len(line) - len(stripped)
                # Round to nearest power-of-two-ish indent unit
                if indent in (2, 4, 8):
                    self._indent_sizes[indent] += 1

        # Quote style — count string literals
        for match in re.finditer(r'(["\'])', source):
            self._quote_styles[match.group(1)] += 1

        # Parse imports without full AST (more robust to syntax errors in old files)
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import "):
                self._import_styles["direct"] += 1
                module = stripped.split()[1].split(".")[0]
                self._import_modules[module] += 1
            elif stripped.startswith("from ") and " import " in stripped:
                self._import_styles["from"] += 1
                module = stripped.split()[1].split(".")[0]
                self._import_modules[module] += 1

        # AST-based analysis
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            self._errors.append(str(path))
            return

        self._walk_ast(tree)

    def _walk_ast(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._function_names.append(node.name)
                # Function length (end_lineno - lineno)
                if hasattr(node, "end_lineno") and node.end_lineno:
                    self._function_lengths.append(
                        node.end_lineno - node.lineno + 1
                    )
                # Docstring presence
                has_doc = (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                )
                self._docstring_coverage.append(bool(has_doc))
                # Type annotation presence (at least one arg or return)
                has_annotations = bool(
                    node.returns
                    or any(
                        arg.annotation
                        for arg in (
                            node.args.args
                            + node.args.posonlyargs
                            + node.args.kwonlyargs
                        )
                    )
                )
                self._type_annotation_rate.append(has_annotations)

            elif isinstance(node, ast.ClassDef):
                self._class_names.append(node.name)

            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        if name.isupper():
                            self._constant_names.append(name)
                        else:
                            self._variable_names.append(name)

    # ------------------------------------------------------------------
    # Profile construction
    # ------------------------------------------------------------------

    def _build_profile(self) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "files_analysed": self._files_analysed,
            "naming_conventions": self._infer_naming_conventions(),
            "import_conventions": self._infer_import_conventions(),
            "function_length": self._infer_length_norms(self._function_lengths, "function"),
            "file_length": self._infer_length_norms(self._file_lengths, "file"),
            "docstring_coverage_pct": self._pct(self._docstring_coverage),
            "type_annotation_rate_pct": self._pct(self._type_annotation_rate),
            "indent_style": self._infer_indent(),
            "quote_style": self._infer_quote_style(),
            "common_modules": [m for m, _ in self._import_modules.most_common(20)],
        }
        if self._errors:
            profile["parse_errors"] = self._errors[:10]  # cap noise
        return profile

    # --- naming helpers ---

    def _infer_naming_conventions(self) -> dict[str, str]:
        return {
            "functions": self._dominant_convention(self._function_names),
            "classes": self._dominant_convention(self._class_names),
            "variables": self._dominant_convention(self._variable_names),
            "constants": self._dominant_convention(self._constant_names),
        }

    @staticmethod
    def _dominant_convention(names: list[str]) -> str:
        if not names:
            return "unknown"
        counts: Counter[str] = Counter()
        for name in names:
            if re.match(r"^[A-Z][a-zA-Z0-9]*$", name):
                counts["PascalCase"] += 1
            elif re.match(r"^[a-z][a-z0-9_]*$", name):
                counts["snake_case"] += 1
            elif re.match(r"^[a-z][a-zA-Z0-9]*$", name):
                counts["camelCase"] += 1
            elif re.match(r"^[A-Z][A-Z0-9_]*$", name):
                counts["UPPER_SNAKE"] += 1
            else:
                counts["mixed"] += 1
        return counts.most_common(1)[0][0]

    # --- import helpers ---

    def _infer_import_conventions(self) -> dict[str, Any]:
        total = sum(self._import_styles.values())
        if total == 0:
            dominant = "unknown"
        else:
            dominant = self._import_styles.most_common(1)[0][0]
        return {
            "dominant_style": dominant,
            "direct_import_pct": self._safe_pct(
                self._import_styles["direct"], total
            ),
            "from_import_pct": self._safe_pct(
                self._import_styles["from"], total
            ),
        }

    # --- length helpers ---

    @staticmethod
    def _infer_length_norms(
        lengths: list[int], kind: str
    ) -> dict[str, Any]:
        if not lengths:
            return {"kind": kind, "mean": None, "p50": None, "p90": None, "max_common": None}
        sorted_l = sorted(lengths)
        n = len(sorted_l)
        p50 = sorted_l[n // 2]
        p90 = sorted_l[int(n * 0.9)]
        mean = round(statistics.mean(lengths), 1)
        # "max_common" = 95th percentile, a reasonable upper bound
        p95 = sorted_l[int(n * 0.95)]
        return {
            "kind": kind,
            "mean": mean,
            "p50": p50,
            "p90": p90,
            "max_common": p95,
        }

    # --- misc helpers ---

    def _infer_indent(self) -> str:
        if not self._indent_sizes:
            return "4 spaces"  # Python default
        dominant = self._indent_sizes.most_common(1)[0][0]
        return f"{dominant} spaces"

    def _infer_quote_style(self) -> str:
        if not self._quote_styles:
            return "double"
        dominant = self._quote_styles.most_common(1)[0][0]
        return "double" if dominant == '"' else "single"

    @staticmethod
    def _pct(bools: list[bool]) -> float | None:
        if not bools:
            return None
        return round(100.0 * sum(bools) / len(bools), 1)

    @staticmethod
    def _safe_pct(numerator: int, denominator: int) -> float:
        if denominator == 0:
            return 0.0
        return round(100.0 * numerator / denominator, 1)
