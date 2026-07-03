import ast
import os
import subprocess
import tempfile
from pathlib import Path


class CodeValidator:
    def __init__(self) -> None:
        self.timeout = int(os.getenv("CODE_TIMEOUT", "30"))

    def check_syntax(self, code: str, language: str = "python") -> tuple[bool, str]:
        if language != "python":
            return True, "syntax check not available for this language"
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            return False, str(e)

    def run_code(self, code: str, language: str = "python") -> tuple[bool, str]:
        if language != "python":
            return True, "execution not available for this language"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp = f.name
        try:
            result = subprocess.run(
                ["python3", tmp],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, f"timed out after {self.timeout}s"
        finally:
            Path(tmp).unlink(missing_ok=True)

    def run_tests(self, source_code: str, test_code: str) -> tuple[bool, str]:
        # Prepend pytest so tests can use pytest.raises without explicit import
        combined = f"import pytest\n{source_code}\n\n{test_code}"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(combined)
            tmp = f.name
        try:
            result = subprocess.run(
                ["python3", "-m", "pytest", tmp, "-v", "--tb=short", "--no-header",
                 "-p", "no:cacheprovider"],
                capture_output=True,
                text=True,
                timeout=self.timeout * 2,
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, f"tests timed out after {self.timeout * 2}s"
        except FileNotFoundError:
            # pytest not in PATH — fall back to plain execution
            return self.run_code(combined)
        finally:
            Path(tmp).unlink(missing_ok=True)
