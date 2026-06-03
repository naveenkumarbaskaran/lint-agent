# lint-agent-ai

AI-powered linting that **learns your codebase style** and flags new code that
deviates from naming conventions, import patterns, function length norms, and
layer conventions.

Powered by [Anthropic Claude](https://www.anthropic.com/) (`claude-sonnet-4-6`).

---

## Features

- Learns naming conventions (snake\_case, PascalCase, UPPER\_SNAKE, camelCase)
  for functions, classes, variables, and constants
- Captures import style (direct `import X` vs `from X import Y`)
- Measures function-length norms (mean, median, 90th / 95th percentile)
- Detects docstring-coverage and type-annotation rates
- Identifies indent style and quote style
- Uses an agentic loop with real file-reading tools so Claude inspects actual
  source code rather than working from a summary alone
- Three output formats: rich terminal table, JSON, and GitHub Actions annotations

---

## Installation

```bash
pip install lint-agent-ai
```

Or install from source:

```bash
git clone https://github.com/example/lint-agent-ai
cd lint-agent-ai
pip install -e .
```

### Requirements

- Python 3.10+
- An Anthropic API key (set `ANTHROPIC_API_KEY` in your environment)

---

## Quick Start

### 1. Train — learn your codebase style

```bash
lint-agent train ./src --save .lint-agent.json
```

This scans up to 200 Python files under `./src`, extracts style patterns, and
saves them to `.lint-agent.json` (you can commit this file alongside your code).

Sample output:

```
Scanning src for style patterns ...

  Profile saved -> .lint-agent.json

  Property                Value
  Files analysed          47
  Function naming         snake_case
  Class naming            PascalCase
  Median function length  18 lines
  95th-pct func length    72 lines
  Docstring coverage      63.2%
  Type annotation rate    81.4%
  Indent style            4 spaces
  Quote style             double
```

### 2. Check — flag style violations

Check a single file:

```bash
lint-agent check src/new_feature.py
```

Check an entire directory:

```bash
lint-agent check src/
```

Output as JSON (useful for tooling integrations):

```bash
lint-agent check src/ --output json
```

Output as GitHub Actions annotations:

```bash
lint-agent check src/ --output github
```

---

## CLI Reference

### `lint-agent train`

```
Usage: lint-agent train [OPTIONS] SRC_DIR

  Learn style conventions from SRC_DIR and save a profile.

Arguments:
  SRC_DIR  Directory to scan.  [required]

Options:
  --save TEXT          Where to save the profile JSON.  [default: .lint-agent.json]
  --max-files INTEGER  Maximum files to sample.  [default: 200]
  -q, --quiet          Suppress progress output.
  --help               Show this message and exit.
```

### `lint-agent check`

```
Usage: lint-agent check [OPTIONS] TARGET

  Check a file or directory against the learned style profile.

Arguments:
  TARGET  File or directory to check.  [required]

Options:
  -p, --profile TEXT               Path to style profile JSON.  [default: .lint-agent.json]
  --model TEXT                     Claude model to use.  [default: claude-sonnet-4-6]
  -o, --output [rich|json|github]  Output format.  [default: rich]
  --fail-on [error|warning|info|never]
                                   Exit non-zero when violations at or above
                                   this severity are found.  [default: error]
  --help                           Show this message and exit.
```

---

## Programmatic Usage

```python
from lint_agent import LintAgent, StyleLearner

# 1. Learn
learner = StyleLearner(max_files=100)
profile = learner.learn("./src")
learner.save(profile, ".lint-agent.json")

# 2. Check
agent = LintAgent(style_profile=profile)
violations = agent.check("./src/new_module.py")

for v in violations:
    print(f"{v['severity'].upper()} [{v['rule']}] {v['file']}:{v.get('line','')}")
    print(f"  {v['message']}")

# 3. Load a saved profile later
loaded_profile = StyleLearner.load(".lint-agent.json")
agent2 = LintAgent(style_profile=loaded_profile)
```

---

## How It Works

```
 lint-agent train src/
        |
        v
  StyleLearner                       .lint-agent.json
  - walks Python files with ast       (committed to repo)
  - counts naming patterns
  - measures function lengths
  - detects import conventions

 lint-agent check new_code.py
        |
        v
  LintAgent
  - loads style profile
  - builds Claude system prompt describing conventions
  - starts agentic loop:
      user turn -> Claude decides which tools to call
      tool: read_file(path)       <- reads actual source
      tool: list_files(dir)       <- discovers files
      tool: get_git_diff()        <- diffs vs HEAD
  - Claude compares code to profile
  - returns structured JSON violations
```

### Violation Object Schema

```json
{
  "file": "src/utils.py",
  "line": 42,
  "rule": "naming/function",
  "message": "Function 'processData' uses camelCase; expected snake_case",
  "severity": "warning"
}
```

Severity levels: `error`, `warning`, `info`.

---

## CI / CD Integration

### GitHub Actions

```yaml
# .github/workflows/lint.yml
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install lint-agent-ai
      - name: AI Lint Check
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          lint-agent check src/ --output github --fail-on warning
```

### Pre-commit Hook

```yaml
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: lint-agent
      name: AI lint check
      entry: lint-agent check
      language: system
      types: [python]
      args: ["--fail-on", "error"]
```

---

## Configuration

The style profile (`.lint-agent.json`) is a plain JSON file you can inspect and
edit manually. Fields:

| Field | Description |
|---|---|
| `files_analysed` | Number of files scanned during training |
| `naming_conventions` | Dominant convention per name category |
| `import_conventions` | Dominant import style and percentages |
| `function_length` | mean, p50, p90, max\_common (lines) |
| `file_length` | Same statistics at file level |
| `docstring_coverage_pct` | % of functions with docstrings |
| `type_annotation_rate_pct` | % of functions with type annotations |
| `indent_style` | `"4 spaces"` / `"2 spaces"` / `"8 spaces"` |
| `quote_style` | `"double"` or `"single"` |
| `common_modules` | Top-20 imported module names |

---

## License

MIT
