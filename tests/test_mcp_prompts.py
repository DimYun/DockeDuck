"""Unit tests for the local-worker prompt builders (no GPU / no network)."""
import pytest

from conftest import TEMPLATES, load_module


@pytest.fixture(params=TEMPLATES)
def prompts(request):
    tpl = request.param
    return load_module(f"templates/{tpl}/src/prompts.py", f"prompts_{tpl.replace('-', '_')}")


def test_few_shot_matches_task_type(prompts):
    assert "clamp" in prompts.few_shot_for("function")
    assert "Counter" in prompts.few_shot_for("class")
    assert "slugify" in prompts.few_shot_for("connected functions")
    assert "dataclass" in prompts.few_shot_for("project")
    assert prompts.few_shot_for("nonsense") == ""


def test_rules_are_language_aware(prompts):
    assert "type hints" in prompts._rules_for("python").lower()
    assert "dockerfile" in prompts._rules_for("dockerfile").lower()
    assert "json" in prompts._rules_for("json").lower()
    assert "yaml" in prompts._rules_for("yaml").lower()


def test_file_prompt_python_includes_fewshot_and_imports(prompts):
    out = prompts.file_prompt("do X", "solution.py", "python", "", "function")
    assert "clamp" in out
    assert "Include all necessary imports" in out


def test_file_prompt_nonpython_skips_python_only_bits(prompts):
    out = prompts.file_prompt("a web server image", "Dockerfile", "dockerfile", "", "project")
    assert "Include all necessary imports" not in out
    assert "dataclass" not in out  # no Python few-shot for a Dockerfile
    assert "Dockerfile" in out


def test_conditions_to_tests_prompt_has_rules(prompts):
    out = prompts.conditions_to_tests_prompt("- empty -> ValueError", "desc", "x.py")
    assert "pytest.raises" in out
    assert "empty -> ValueError" in out


def test_fix_prompt_carries_error_and_language(prompts):
    out = prompts.fix_prompt("code", "SyntaxError: bad", "python")
    assert "SyntaxError: bad" in out
    assert "python" in out
