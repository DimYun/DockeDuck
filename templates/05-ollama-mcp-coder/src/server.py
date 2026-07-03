import os
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

from .hardware import context_for, detect_hardware, recommend_model as _recommend_model
from .prompts import conditions_to_tests_prompt, file_prompt, fix_prompt, test_prompt
from .validator import CodeValidator
from .vlm import CoderClient

_port      = int(os.getenv("MCP_PORT", "8000"))
_transport = os.getenv("MCP_TRANSPORT", "sse")
_workspace = Path(os.getenv("MCP_WORKSPACE", "/tmp/mcp_workspace"))

mcp       = FastMCP(
    "dockeduck-ollama-coder",
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

    For bare IDE use: write this file manually (see experiments/tasks/class-example.yaml
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

    spec        — YAML content or path to a .yaml file.
                  Supports two formats:
                    conditions: (natural language)  → local model generates tests first, then code
                    tests:      (pytest functions)   → use directly, skip test generation step

    The ENTIRE loop runs on the local model — zero cloud tokens after dispatch.

    Fix loop (repeated up to max_retries):
      1. [Once, before loop] Generate tests from conditions (if conditions: present)
      2. Generate (or fix) code
      3. Check syntax  →  fix on failure
      4. Run code      →  fix on failure
      5. Run tests     →  fix on failure

    Returns on success:
      # DONE after N attempt(s)
      # file: <filename>
      <code>

    Returns on failure (structured for Claude rescue):
      # MAX_RETRIES_REACHED (N)
      # file: <filename>
      LAST_CODE:
      <code>
      GENERATED_TESTS:
      <tests>
      LAST_ERROR:
      <error output>
    """
    data        = _parse_spec(spec)
    description = data["description"]
    filename    = data["filename"]
    language    = data.get("language", "python")
    task_type   = data.get("type", "").strip()
    conditions  = data.get("conditions", "").strip()
    user_tests  = data.get("tests", "").strip()
    _max        = int(os.getenv("MAX_RETRIES", str(max_retries)))

    # Step 1 — generate tests from conditions BEFORE generating code
    # This ensures the fix loop validates against user intent, not against code the model wrote
    if not user_tests and conditions and language == "python":
        user_tests = await coder.generate(
            conditions_to_tests_prompt(conditions, description, filename), system=_SYSTEM
        )

    # Step 2 — generate initial implementation (task-type aware few-shot)
    code = await coder.generate(
        file_prompt(description, filename, language, "", task_type), system=_SYSTEM
    )

    # Step 3 — if still no tests (no conditions, no tests supplied), derive border-case
    # tests ONCE from the initial code and reuse them. Generating them once — instead of
    # regenerating every fix iteration — keeps the acceptance target stable across retries.
    if not user_tests and language == "python":
        user_tests = await coder.generate(test_prompt(code), system=_SYSTEM)

    # Confidence: highest validation gate reached so far.
    # TESTS PASS=100 · EXEC=67 · SYNTAX=33 · FAIL=0
    best = 0
    last_error = ""
    for attempt in range(1, _max + 1):
        # Gate 1 — syntax
        ok, msg = validator.check_syntax(code, language)
        if not ok:
            last_error = f"SyntaxError: {msg}"
            code = await coder.generate(fix_prompt(code, last_error, language), system=_SYSTEM)
            continue
        best = max(best, 33)

        # Gate 2 — execution
        ok, out = validator.run_code(code, language)
        if not ok:
            last_error = f"RuntimeError:\n{out}"
            code = await coder.generate(fix_prompt(code, last_error, language), system=_SYSTEM)
            continue
        best = max(best, 67)

        # Gate 3 — tests (Python only, when tests are available)
        if language == "python" and user_tests:
            ok, out = validator.run_tests(code, user_tests)
            if not ok:
                last_error = f"Tests failed:\n{out}"
                code = await coder.generate(fix_prompt(code, last_error, language), system=_SYSTEM)
                continue

        return (
            f"# DONE after {attempt} attempt(s)\n"
            f"# file: {filename}\n"
            f"# confidence: 100%\n\n{code}"
        )

    # Structured failure — Claude can rescue using LAST_CODE + GENERATED_TESTS + LAST_ERROR
    return (
        f"# MAX_RETRIES_REACHED ({_max})\n"
        f"# file: {filename}\n"
        f"# confidence: {best}%\n\n"
        f"LAST_CODE:\n{code}\n\n"
        f"GENERATED_TESTS:\n{user_tests}\n\n"
        f"LAST_ERROR:\n{last_error}"
    )


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
    # Confidence: TESTS PASS=100 · EXEC=67 · SYNTAX=33 · FAIL=0
    confidence = 0

    ok, msg = validator.check_syntax(code, language)
    report.append(f"SYNTAX     : {'OK' if ok else 'FAIL — ' + msg}")
    if not ok:
        report.append(f"# confidence: {confidence}%")
        return "\n".join(report)
    confidence = 33

    ok, out = validator.run_code(code, language)
    report.append(f"EXECUTION  : {'OK' if ok else 'FAIL'}")
    if out.strip():
        report.append(f"  {out.strip()[:300]}")
    if not ok:
        report.append(f"# confidence: {confidence}%")
        return "\n".join(report)
    confidence = 67

    if language == "python" and user_tests:
        ok, out = validator.run_tests(code, user_tests)
        report.append(f"TESTS      : {'PASS ✓' if ok else 'FAIL ✗'}")
        if ok:
            confidence = 100
        if out.strip():
            report.append(out.strip()[:1500])
    else:
        report.append("TESTS      : (no tests in spec)")

    report.append(f"# confidence: {confidence}%")
    return "\n".join(report)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4 — recommend_model  (Haiku proposes the best model for the user's GPU/CPU)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def recommend_model(prefer: str = "quality", apply: bool = False) -> str:
    """Detect this machine (GPU or CPU/RAM) and recommend the best Ollama model to run here.

    Numbers come from the DockeDuck 6 GB benchmark (experiments/RESULTS.md) plus KV-cache
    math. Ollama runs on CPU too, so a recommendation is always returned.

    prefer — 'quality' (default) · 'context' (largest window) · 'speed' (smallest model).
    apply  — if true, switch the live client to the recommended model + context immediately
             (Ollama loads models on demand and honours per-request num_ctx — no restart).
             Persist it by also setting OLLAMA_MODEL / OLLAMA_NUM_CTX in .env.

    Returns the recommended tag, the context that fits, and how to apply it.
    """
    rec = _recommend_model(prefer=prefer)
    hw = rec["hw"]
    where = f"{hw['name']} ({hw['vram_gb']} GB VRAM)" if hw["gpu"] else f"CPU ({hw['ram_gb']} GB RAM)"
    if not rec.get("tag"):
        return f"# Hardware: {where}\n\n{rec['reason']}"
    applied = ""
    if apply:
        coder.model = rec["tag"]
        coder.num_ctx = rec["recommended_context"]
        applied = (f"\n\n# APPLIED to the running server: model={coder.model}, "
                   f"num_ctx={coder.num_ctx}. Ollama pulls the model on first use.")
    thinking = "  (set ENABLE_THINKING=true for +quality)" if rec["thinking"] else ""
    return (
        f"# Recommended model for {where}\n\n"
        f"OLLAMA_MODEL={rec['tag']}\n"
        f"OLLAMA_NUM_CTX={rec['recommended_context']}\n\n"
        f"Expected quality : {rec['quality']}% local · {rec['rescue']} tasks pass with one rescue{thinking}\n"
        f"Why              : {rec['note']}\n"
        f"How to apply     : set the two lines above in templates/05-ollama-mcp-coder/.env "
        f"(or call again with apply=true to switch the live server now).{applied}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5 — recommend_context_window  (Haiku picks the largest context that fits)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def recommend_context_window(model: str = "", apply: bool = False) -> str:
    """Recommend the largest num_ctx that fits the detected GPU/CPU for a model (defaults to
    the current OLLAMA_MODEL). With apply=true, set it on the live client immediately.
    """
    from .hardware import MODELS
    hw = detect_hardware()
    target = model.strip() or coder.model
    spec = next((m for m in MODELS if m["tag"] == target), None)
    where = f"{hw['name']} ({hw['vram_gb']} GB)" if hw["gpu"] else f"CPU ({hw['ram_gb']} GB RAM)"
    if spec is None:
        return (f"# Hardware: {where}\n\nUnknown model '{target}'. Known: "
                + ", ".join(m['tag'] for m in MODELS)
                + ".\nUse recommend_model to pick one, or pass model=<ollama tag>.")
    mem = hw["vram_gb"] if hw["gpu"] else hw["ram_gb"]
    ctx = context_for(spec, mem)
    if ctx < 2048:
        return (f"# {target} does not fit {where} with a usable context. "
                f"Try recommend_model(prefer='speed').")
    applied = ""
    if apply:
        coder.num_ctx = ctx
        applied = f"\n\n# APPLIED: the running server now uses num_ctx={ctx}."
    return (
        f"# Context window for {target} on {where}\n\n"
        f"OLLAMA_NUM_CTX={ctx}\n\n"
        f"Basis : {spec['kv_heads']} KV heads × {spec['layers']} layers "
        f"(~{2 * spec['layers'] * spec['kv_heads'] * spec['head_dim'] * 2 // 1024} KB/token), "
        f"capped at {spec['rope_max']}.\n"
        f"Apply : set OLLAMA_NUM_CTX in .env, or call with apply=true. Keep MAX_TOKENS below "
        f"num_ctx (raise both for thinking models).{applied}"
    )


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if _transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport="sse")
