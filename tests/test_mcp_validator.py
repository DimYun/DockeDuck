"""Unit tests for the three validation gates (syntax / exec / tests)."""
import pytest

from conftest import TEMPLATES, load_module


@pytest.fixture(params=TEMPLATES)
def validator(request):
    tpl = request.param
    mod = load_module(f"templates/{tpl}/src/validator.py", f"validator_{tpl.replace('-', '_')}")
    return mod.CodeValidator()


def test_syntax_gate(validator):
    ok, _ = validator.check_syntax("x = 1\n")
    assert ok
    ok, msg = validator.check_syntax("def broken(:\n    pass\n")
    assert not ok and msg


def test_syntax_gate_non_python_is_skipped(validator):
    ok, msg = validator.check_syntax("FROM python:3.11", language="dockerfile")
    assert ok and "not available" in msg


def test_exec_gate(validator):
    ok, _ = validator.run_code("print('hello')\n")
    assert ok
    ok, out = validator.run_code("import sys; sys.exit(1)\n")
    assert not ok


def test_tests_gate_pass(validator):
    src = "def add(a: int, b: int) -> int:\n    return a + b\n"
    tests = "def test_add():\n    assert add(1, 2) == 3\n"
    ok, out = validator.run_tests(src, tests)
    assert ok, out


def test_tests_gate_fail(validator):
    src = "def add(a: int, b: int) -> int:\n    return a - b\n"  # wrong
    tests = "def test_add():\n    assert add(1, 2) == 3\n"
    ok, _ = validator.run_tests(src, tests)
    assert not ok


def test_tests_gate_supports_pytest_raises(validator):
    src = "def f(x):\n    if x < 0:\n        raise ValueError('neg')\n    return x\n"
    tests = "def test_raises():\n    with pytest.raises(ValueError):\n        f(-1)\n"
    ok, out = validator.run_tests(src, tests)
    assert ok, out
