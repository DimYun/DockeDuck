"""Unit tests for experiments/bench_real.py pure helpers (no GPU / no API key)."""
import yaml

from conftest import load_module

bench = load_module("experiments/bench_real.py", "bench_real")


def test_rules_are_language_aware():
    assert "type hints" in bench._rules_for("python").lower()
    assert "dockerfile" in bench._rules_for("dockerfile").lower()
    assert "json" in bench._rules_for("json").lower()


def test_few_shot_distinct_per_type():
    assert "clamp" in bench._few_shot_for("function")
    assert "slugify" in bench._few_shot_for("connected functions")
    assert "dataclass" in bench._few_shot_for("project")


def test_gen_prompt_language_awareness():
    py = bench._build_gen_prompt({"filename": "s.py", "language": "python",
                                  "type": "function", "description": "do X"})
    assert "Include all necessary imports" in py and "clamp" in py

    docker = bench._build_gen_prompt({"filename": "Dockerfile", "language": "dockerfile",
                                      "type": "project", "description": "web image"})
    assert "Include all necessary imports" not in docker
    assert "dataclass" not in docker


def test_validation_gates():
    ok, _ = bench.check_syntax("x = 1\n")
    assert ok
    ok, _ = bench.run_code("print('hi')\n")
    assert ok
    ok, _ = bench.run_tests("def add(a, b):\n    return a + b\n",
                            "def test_add():\n    assert add(1, 2) == 3\n")
    assert ok


def test_confidence_ladder():
    r = bench.BenchResult(backend="x", task_level="function")
    assert bench._confidence(r) == 0
    r.syntax_ok = True
    assert bench._confidence(r) == 33
    r.exec_ok = True
    assert bench._confidence(r) == 67
    r.tests_ok = True
    assert bench._confidence(r) == 100


def test_task_registry_matches_specs():
    assert set(bench.TASKS) == {"function", "class", "connected", "module", "project"}
    for key, meta in bench.TASKS.items():
        spec_path = meta["spec_file"]
        assert spec_path.exists(), f"missing spec for {key}: {spec_path}"
        spec = yaml.safe_load(spec_path.read_text())
        assert "conditions" in spec or "tests" in spec, f"{key} has no conditions/tests"
        assert spec.get("filename"), f"{key} spec missing filename"


def test_every_spec_has_valid_canonical_tests():
    """Benchmark fairness depends on every spec shipping canonical pytest that all
    backends are graded against. Guard that they exist and parse as Python."""
    import ast
    for key, meta in bench.TASKS.items():
        spec = yaml.safe_load(meta["spec_file"].read_text())
        tests = (spec.get("tests") or "").strip()
        assert tests, f"{key}: missing canonical tests: field"
        ast.parse(tests)                              # valid Python
        assert "def test_" in tests, f"{key}: no test functions"


def test_strip_fences_and_thinking():
    assert bench._strip_fences("```python\nx=1\n```") == "x=1"
    assert bench._strip_fences("<think>reason</think>\ncode") == "code"


def test_strip_fences_extracts_block_from_chatty_prose():
    # Regression: chatty instruct models wrap code in a fence with prose around it.
    # A boundary-only strip left the prose in place and the code failed to parse.
    chatty = ("Certainly! Below is the function:\n\n"
              "```python\ndef add(a, b):\n    return a + b\n```\n\n"
              "This takes two arguments.")
    out = bench._strip_fences(chatty)
    assert out == "def add(a, b):\n    return a + b"
    bench.check_syntax(out)[0] is True  # parses
    # bare code (no fence) must pass through untouched
    assert bench._strip_fences("def f():\n    return 1") == "def f():\n    return 1"
    # multiple blocks: keep the longest (real file, not a tiny usage snippet)
    two = ("Usage:\n```\n>>> add(1, 2)\n```\n"
           "Code:\n```python\ndef add(a, b):\n    return a + b\n```")
    assert "def add" in bench._strip_fences(two)


def test_grade_and_gate_score():
    assert bench._grade("def f():\n    return 1",
                        "def test_f():\n    assert f() == 1")[:3] == (True, True, True)
    assert bench._grade("def f(:", "")[:3] == (False, False, False)          # syntax
    assert bench._grade("raise RuntimeError()", "")[:3] == (True, False, False)  # exec
    assert bench._gate_score(True, True, True) == 3
    assert bench._gate_score(True, False, False) == 1
    assert bench._gate_score(False, False, False) == 0


def test_trim_history_keeps_alternation_and_task():
    def build(n_pairs):
        m = [{"role": "user", "content": "task"}]
        for k in range(n_pairs):
            m += [{"role": "assistant", "content": f"code{k}"},
                  {"role": "user", "content": f"fix{k}"}]
        return m
    for n in range(0, 9):
        t = bench._trim_history(build(n), keep_pairs=3)
        roles = [x["role"] for x in t]
        assert roles[0] == "user" and t[0]["content"] == "task"   # task spec preserved
        assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1))  # alternation
