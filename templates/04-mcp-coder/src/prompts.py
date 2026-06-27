_RULES = (
    "Output ONLY raw code. No markdown fences, no prose, no explanation.\n"
    "Use type hints. Keep it minimal and correct."
)

# ── Few-shot examples per task type ───────────────────────────────────────────
# Local-only tokens (zero cloud cost), so a concise example is worth the accuracy.
# Each shows the expected style: type hints, edge-case raising, no prose.
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
}
# "connected functions" and "project"/"complex" reuse the module example
# (several cooperating definitions in one file).
_FEW_SHOT["connected functions"] = _FEW_SHOT["module"]
_FEW_SHOT["project"] = _FEW_SHOT["module"]
_FEW_SHOT["complex"] = _FEW_SHOT["module"]


def few_shot_for(task_type: str) -> str:
    """Return a concise in-context example for the given task type ('' if unknown)."""
    ex = _FEW_SHOT.get((task_type or "").strip().lower(), "")
    return f"\n{ex}\n" if ex else ""


def file_prompt(
    description: str, filename: str, language: str, context: str, task_type: str = ""
) -> str:
    ctx = f"\n\nContext:\n{context}" if context else ""
    example = few_shot_for(task_type) if language == "python" else ""
    return (
        f"Write a complete, working {language} file.\n"
        f"Filename: {filename}\n"
        f"Requirements: {description}"
        f"{ctx}\n"
        f"{example}\n"
        f"Include all necessary imports.\n{_RULES}"
    )


def function_prompt(signature: str, description: str, language: str, context: str) -> str:
    ctx = f"\n\nContext:\n{context}" if context else ""
    example = few_shot_for("function") if language == "python" else ""
    return (
        f"Implement the following {language} function.\n"
        f"Signature: {signature}\n"
        f"Description: {description}"
        f"{ctx}\n"
        f"{example}\n"
        f"Handle edge cases.\n{_RULES}"
    )


def class_prompt(name: str, description: str, methods: str, language: str, context: str) -> str:
    method_spec = f"\nMethods to implement:\n{methods}" if methods else ""
    ctx = f"\n\nContext:\n{context}" if context else ""
    example = few_shot_for("class") if language == "python" else ""
    return (
        f"Implement the following {language} class.\n"
        f"Class name: {name}\n"
        f"Description: {description}"
        f"{method_spec}"
        f"{ctx}\n"
        f"{example}\n"
        f"{_RULES}"
    )


def conditions_to_tests_prompt(conditions: str, description: str, filename: str) -> str:
    """Convert natural-language conditions to pytest test functions (called BEFORE code generation)."""
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
        f"The following {language} code has an error. Fix it.\n\n"
        f"Code:\n{code}\n\n"
        f"Error:\n{error}\n\n"
        f"{_RULES}"
    )
