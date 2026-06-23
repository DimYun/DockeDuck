import os
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

from .prompts import file_prompt, fix_prompt, test_prompt
from .validator import CodeValidator
from .vlm import CoderClient

_port      = int(os.getenv("MCP_PORT", "8000"))
_transport = os.getenv("MCP_TRANSPORT", "sse")
_workspace = Path(os.getenv("MCP_WORKSPACE", "/tmp/mcp_workspace"))

mcp       = FastMCP(
    "dockduck-qwen-coder",
    host="0.0.0.0" if _transport == "sse" else "127.0.0.1",
    port=_port,
)
coder     = CoderClient()
validator = CodeValidator()

_SYSTEM = "You are an expert programmer. Output only raw code with no markdown or explanation."


def _parse_spec(spec: str) -> dict:
    """Accept YAML/JSON content string, path to .yaml, or path to .json file."""
    stripped = spec.strip()
    if "\n" in stripped or stripped.startswith("name:") or stripped.startswith("{"):
        return yaml.safe_load(stripped)   # yaml.safe_load also parses JSON
    path = Path(spec)
    return yaml.safe_load(path.read_text())


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1 — write_input_file
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def write_input_file(
    name: str,
    filename: str,
    description: str,
    tests: str,
    language: str = "python",
) -> str:
    """Create a task spec file (YAML) that defines what code to generate and how to test it.

    As the cloud LLM you should:
      1. Understand the user's task description and border cases (in any language).
      2. Write pytest test functions that formally encode those requirements.
      3. Call this tool — it saves the spec and returns the YAML content.
      4. Pass the returned YAML to write_and_fix to generate and validate the code.

    For bare IDE use: write this file manually (see experiments/tasks/lru_cache.yaml
    as a template), then call write_and_fix directly.

    name        — short identifier used as the filename stem, e.g. "lru_cache"
    filename    — the Python file to generate, e.g. "lru_cache.py"
    description — plain-English requirements (used as the generation prompt)
    tests       — pytest test functions (no imports needed; code is in scope)
    language    — default "python"

    Returns the saved YAML content (pass it directly to write_and_fix).
    """
    _workspace.mkdir(parents=True, exist_ok=True)
    spec = {
        "name": name,
        "filename": filename,
        "language": language,
        "description": description.strip(),
        "tests": tests.strip(),
    }
    content = yaml.dump(spec, allow_unicode=True, default_flow_style=False, sort_keys=False)
    spec_path = _workspace / f"{name}.yaml"
    spec_path.write_text(content)
    return f"# spec saved → {spec_path}\n\n{content}"


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2 — write_and_fix
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def write_and_fix(
    spec: str,
    max_retries: int = 5,
) -> str:
    """Generate code from a task spec and fix it until all acceptance tests pass.

    spec        — YAML content returned by write_input_file, or a path to a .yaml file.
                  Must contain: name, filename, description, tests.

    The ENTIRE generation and fix loop runs offline on the local model — zero
    additional cloud tokens after this call is dispatched.

    Fix loop (repeated up to max_retries):
      1. Generate (or fix) code
      2. Check syntax  →  fix on failure
      3. Run code      →  fix on failure
      4. Run tests     →  fix on failure  (uses spec tests; auto-generates if empty)

    Returns:
      # DONE after N attempt(s)   — all gates passed
      # file: <filename>
      <code>

      # MAX_RETRIES_REACHED (N)   — gave up; best-effort code follows
      <code>
    """
    data       = _parse_spec(spec)
    description = data["description"]
    filename    = data["filename"]
    language    = data.get("language", "python")
    user_tests  = data.get("tests", "").strip()
    _max        = int(os.getenv("MAX_RETRIES", str(max_retries)))

    code = await coder.generate(file_prompt(description, filename, language, ""), system=_SYSTEM)

    for attempt in range(1, _max + 1):
        # Gate 1 — syntax
        ok, msg = validator.check_syntax(code, language)
        if not ok:
            code = await coder.generate(fix_prompt(code, f"Syntax error: {msg}", language), system=_SYSTEM)
            continue

        # Gate 2 — execution
        ok, out = validator.run_code(code, language)
        if not ok:
            code = await coder.generate(fix_prompt(code, f"Runtime error:\n{out}", language), system=_SYSTEM)
            continue

        # Gate 3 — tests (Python only)
        if language == "python":
            test_code = user_tests or await coder.generate(test_prompt(code), system=_SYSTEM)
            ok, out = validator.run_tests(code, test_code)
            if not ok:
                code = await coder.generate(
                    fix_prompt(code, f"Tests failed:\n{out}", language), system=_SYSTEM
                )
                continue

        return f"# DONE after {attempt} attempt(s)\n# file: {filename}\n\n{code}"

    ok, msg = validator.check_syntax(code, language)
    status = "SYNTAX_OK" if ok else f"SYNTAX_ERROR: {msg}"
    return f"# MAX_RETRIES_REACHED ({_max})\n# {status}\n# file: {filename}\n\n{code}"


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3 — validate_output_file
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def validate_output_file(
    spec: str,
    code: str,
) -> str:
    """Run the acceptance tests from a spec against existing code.

    Use this to verify code that was already generated (e.g. code you wrote
    manually, or a file from a previous write_and_fix run).

    spec  — YAML content from write_input_file, or path to a .yaml file
    code  — the Python source code to validate

    Returns a SYNTAX / EXECUTION / TESTS report.
    """
    data       = _parse_spec(spec)
    language   = data.get("language", "python")
    user_tests = data.get("tests", "").strip()
    filename   = data.get("filename", "code.py")

    report: list[str] = [f"# Validation: {filename}"]

    ok, msg = validator.check_syntax(code, language)
    report.append(f"SYNTAX     : {'OK' if ok else 'FAIL — ' + msg}")
    if not ok:
        return "\n".join(report)

    ok, out = validator.run_code(code, language)
    report.append(f"EXECUTION  : {'OK' if ok else 'FAIL'}")
    if out.strip():
        report.append(f"  {out.strip()[:300]}")
    if not ok:
        return "\n".join(report)

    if language == "python" and user_tests:
        ok, out = validator.run_tests(code, user_tests)
        report.append(f"TESTS      : {'PASS ✓' if ok else 'FAIL ✗'}")
        if out.strip():
            report.append(out.strip()[:1500])
    else:
        report.append("TESTS      : (no tests in spec)")

    return "\n".join(report)


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if _transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport="sse")
