"""Prompt builders for the local coding worker.

Tasks are not always Python: a spec may ask for a Dockerfile, a YAML/JSON config,
or a small multi-file project. `_rules_for()` supplies per-language output rules and
always asks for clean, human-friendly output. The few-shot examples are Python-only
(zero cloud cost) and cover every task `type:` we support.
"""


def _rules_for(language: str) -> str:
    """Per-language output rules. Always asks for clean, human-friendly output."""
    lang = (language or "python").strip().lower()
    common = (
        "Output ONLY the raw file contents. No markdown fences, no prose.\n"
        "Write clean, human-friendly output: clear names, small units, and a short "
        "docstring or comment only where it genuinely aids understanding."
    )
    if lang == "python":
        return common + "\nUse type hints. Keep it minimal and correct."
    if lang in ("dockerfile", "docker"):
        return common + "\nProduce a valid Dockerfile: pinned base image, minimal layers."
    if lang in ("yaml", "yml"):
        return common + "\nProduce valid YAML with 2-space indentation."
    if lang == "json":
        return common + "\nProduce strictly valid JSON (no comments, no trailing commas)."
    return common


# Back-compat alias (Python default).
_RULES = _rules_for("python")

# ── Few-shot examples per task type ───────────────────────────────────────────
# Local-only tokens (zero cloud cost), so a concise example is worth the accuracy.
# Each shows the expected style: type hints, edge-case raising, human-friendly, no prose.
_FEW_SHOT: dict[str, str] = {
    "function": (
        "Example of the expected style:\n"
        "def clamp(value: float, low: float, high: float) -> float:\n"
        "    if low > high:\n"
        "        raise ValueError('low must be <= high')\n"
        "    return max(low, min(value, high))\n"
    ),
    "class": (
        "Example of the expected style:\n"
        "class Counter:\n"
        "    def __init__(self) -> None:\n"
        "        self._n = 0\n"
        "    def increment(self, by: int = 1) -> None:\n"
        "        self._n += by\n"
        "    @property\n"
        "    def value(self) -> int:\n"
        "        return self._n\n"
    ),
    "module": (
        "Example of the expected style (constants + exception + functions in one file):\n"
        "DEFAULT_RETRIES = 3\n\n"
        "class RetryError(Exception):\n"
        "    pass\n\n"
        "def backoff_delay(attempt: int) -> float:\n"
        "    if attempt < 0:\n"
        "        raise RetryError('attempt must be >= 0')\n"
        "    return 0.5 * (2 ** attempt)\n"
    ),
    "connected functions": (
        "Example of the expected style (several small functions that call each other):\n"
        "def _normalize(name: str) -> str:\n"
        "    if not name:\n"
        "        raise ValueError('name must not be empty')\n"
        "    return name.strip().lower()\n\n"
        "def slugify(name: str) -> str:\n"
        "    return _normalize(name).replace(' ', '-')\n"
    ),
    "project": (
        "Example of the expected style (dataclass + class that writes files):\n"
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n\n"
        "@dataclass\n"
        "class Item:\n"
        "    name: str\n"
        "    count: int\n\n"
        "class Store:\n"
        "    def save(self, item: Item, out_dir: str) -> None:\n"
        "        Path(out_dir).mkdir(parents=True, exist_ok=True)\n"
        "        Path(out_dir, item.name).write_text(str(item.count))\n"
    ),
}
# "complex" is an alias for the project-level example.
_FEW_SHOT["complex"] = _FEW_SHOT["project"]


def few_shot_for(task_type: str) -> str:
    """Return a concise in-context example for the given task type ('' if unknown)."""
    ex = _FEW_SHOT.get((task_type or "").strip().lower(), "")
    return f"\n{ex}\n" if ex else ""


def file_prompt(
    description: str, filename: str, language: str, context: str, task_type: str = ""
) -> str:
    is_python = (language or "python").strip().lower() == "python"
    ctx = f"\n\nContext:\n{context}" if context else ""
    example = few_shot_for(task_type) if is_python else ""
    imports = "Include all necessary imports.\n" if is_python else ""
    return (
        f"Write a complete, working {language} file.\n"
        f"Filename: {filename}\n"
        f"Requirements: {description}"
        f"{ctx}\n"
        f"{example}\n"
        f"{imports}{_rules_for(language)}"
    )


def function_prompt(signature: str, description: str, language: str, context: str) -> str:
    is_python = (language or "python").strip().lower() == "python"
    ctx = f"\n\nContext:\n{context}" if context else ""
    example = few_shot_for("function") if is_python else ""
    return (
        f"Implement the following {language} function.\n"
        f"Signature: {signature}\n"
        f"Description: {description}"
        f"{ctx}\n"
        f"{example}\n"
        f"Handle edge cases.\n{_rules_for(language)}"
    )


def class_prompt(name: str, description: str, methods: str, language: str, context: str) -> str:
    is_python = (language or "python").strip().lower() == "python"
    method_spec = f"\nMethods to implement:\n{methods}" if methods else ""
    ctx = f"\n\nContext:\n{context}" if context else ""
    example = few_shot_for("class") if is_python else ""
    return (
        f"Implement the following {language} class.\n"
        f"Class name: {name}\n"
        f"Description: {description}"
        f"{method_spec}"
        f"{ctx}\n"
        f"{example}\n"
        f"{_rules_for(language)}"
    )


def conditions_to_tests_prompt(conditions: str, description: str, filename: str) -> str:
    """Convert natural-language conditions to pytest test functions (before code generation)."""
    return (
        f"Convert these conditions into pytest test functions for '{filename}'.\n\n"
        f"Description: {description.strip()}\n\n"
        f"Conditions:\n{conditions.strip()}\n\n"
        "Rules:\n"
        "- One pytest test function per condition, named test_<what_it_tests>\n"
        "- Do NOT add any import statements (the implementation will be prepended at runtime)\n"
        "- Use pytest.raises(ExceptionType) for error conditions\n"
        "- Reference classes and functions by the names from the description\n"
        f"Output ONLY the test functions, no other code.\n{_RULES}"
    )


def test_prompt(code: str) -> str:
    return (
        "Write pytest border-case tests for the code below.\n\n"
        f"{code}\n\n"
        "Rules:\n"
        "- Output ONLY pytest test functions named test_*.\n"
        "- Test edge cases: empty inputs, boundary values, None, type errors.\n"
        "- Do NOT import the module under test — its code is already in scope.\n"
        f"{_RULES}"
    )


def fix_prompt(code: str, error: str, language: str) -> str:
    return (
        f"The following {language} file has an error. Fix it.\n\n"
        f"Content:\n{code}\n\n"
        f"Error:\n{error}\n\n"
        f"{_rules_for(language)}"
    )
