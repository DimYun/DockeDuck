_RULES = (
    "Output ONLY raw code. No markdown fences, no prose, no explanation.\n"
    "Use type hints. Keep it minimal and correct."
)


def file_prompt(description: str, filename: str, language: str, context: str) -> str:
    ctx = f"\n\nContext:\n{context}" if context else ""
    return (
        f"Write a complete, working {language} file.\n"
        f"Filename: {filename}\n"
        f"Requirements: {description}"
        f"{ctx}\n\n"
        f"Include all necessary imports.\n{_RULES}"
    )


def function_prompt(signature: str, description: str, language: str, context: str) -> str:
    ctx = f"\n\nContext:\n{context}" if context else ""
    return (
        f"Implement the following {language} function.\n"
        f"Signature: {signature}\n"
        f"Description: {description}"
        f"{ctx}\n\n"
        f"Handle edge cases.\n{_RULES}"
    )


def class_prompt(name: str, description: str, methods: str, language: str, context: str) -> str:
    method_spec = f"\nMethods to implement:\n{methods}" if methods else ""
    ctx = f"\n\nContext:\n{context}" if context else ""
    return (
        f"Implement the following {language} class.\n"
        f"Class name: {name}\n"
        f"Description: {description}"
        f"{method_spec}"
        f"{ctx}\n\n"
        f"{_RULES}"
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
