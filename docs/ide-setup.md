# Using DockeDuck from your IDE (PyCharm & VS Code)

The MCP server exposes a single endpoint — **SSE at `http://localhost:8000/sse`**. Everything
below just points an IDE at that URL. There are two ways to drive it:

- **A — with the IDE's AI assistant** (it calls the tools for you: *"use write_and_fix to …"*).
- **B — a pure direct call**, no AI at all: run [`examples/ide/dockeduck_call.py`](../examples/ide/dockeduck_call.py)
  as a Run Configuration / terminal command. Best if you just want the local model to write a
  file from a spec.

The five tools you get: `write_and_fix`, `write_input_file`, `validate_output_file`,
`recommend_model`, `recommend_context_window`.

---

## Step 1 — start the server (once)

Pick your backend and run these from the repo root. **vLLM (template 04, needs a CUDA GPU):**

```bash
cd templates/04-vllm-mcp-coder
cp .env.example .env          # first time only
make build                    # first time only (~15 min)
make start                    # MCP SSE on http://localhost:8000/sse
make logs                     # wait for: "Uvicorn running on http://0.0.0.0:8000"
```

**Ollama (template 05, runs on CPU too):**

```bash
cd templates/05-ollama-mcp-coder
cp .env.example .env          # first time only
make build && make up         # starts Ollama + MCP; pulls the model on first run
make logs                     # wait for: "[entrypoint] Starting MCP server"
```

Not sure which model/settings suit your machine? Ask the running server:

```bash
python examples/ide/dockeduck_call.py --tool recommend_model     # needs: pip install mcp
```

## Step 2 — verify it's up

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/sse   # expect 200
```

---

## PyCharm / IntelliJ

### Option A1 — JetBrains AI Assistant (built-in MCP, 2025.1+)

1. **Settings → Tools → AI Assistant → Model Context Protocol (MCP)** → **Add**.
2. Transport **SSE**, URL `http://localhost:8000/sse`, name `dockeduck-coder` → **OK / Apply**.
3. Open the **AI Assistant** chat, switch to an agent/tools-capable mode, and ask:
   > *Use write_and_fix to create `search.py`: binary search on a sorted list; raise ValueError if the value is not found.*

### Option A2 — Continue plugin (works the same in PyCharm and VS Code)

1. Install **Continue** from the JetBrains Marketplace.
2. Open the Continue panel → gear icon → **Open config** (`~/.continue/config.yaml`), and merge in
   the `mcpServers` block from [`examples/ide/continue-config.yaml`](../examples/ide/continue-config.yaml):
   ```yaml
   mcpServers:
     - name: DockeDuck Coder
       type: sse
       url: http://localhost:8000/sse
   ```
3. Reload Continue. In **Agent** mode the five tools appear; try *"call recommend_model for my GPU."*

### Option B — pure direct call (no AI), as a Run Configuration

1. `pip install mcp` into your project interpreter.
2. **Run → Edit Configurations → + → Python**:
   - **Script:** `examples/ide/dockeduck_call.py`
   - **Parameters:** `experiments/tasks/class-example.yaml`  (or your own spec)
   - **Working directory:** the repo root
3. Hit **Run**. The local model writes + fixes the file and prints the result. Other tools:
   ```bash
   python examples/ide/dockeduck_call.py my_spec.yaml
   python examples/ide/dockeduck_call.py --tool recommend_model --prefer context
   python examples/ide/dockeduck_call.py my_spec.yaml --tool validate_output_file --code out.py
   ```

---

## VS Code

### Option A1 — native MCP (GitHub Copilot, Agent mode, VS Code ≥ 1.99)

1. Copy [`examples/ide/vscode-mcp.json`](../examples/ide/vscode-mcp.json) to **`.vscode/mcp.json`**
   in your project (or add its `servers` block to user settings):
   ```json
   { "servers": { "dockeduck-coder": { "type": "sse", "url": "http://localhost:8000/sse" } } }
   ```
2. Open **Copilot Chat**, switch to **Agent** mode — the DockeDuck tools are now selectable.
3. Ask: *"Use write_and_fix to create lru_cache.py: an LRU cache with get/put, O(1)."*

### Option A2 — Continue / Cline / Roo

Continue: same `~/.continue/config.yaml` as PyCharm (above). Cline/Roo: add an SSE MCP server
with URL `http://localhost:8000/sse` in the extension's MCP settings.

### Option B — pure direct call

Same as PyCharm Option B: `pip install mcp`, then run
`python examples/ide/dockeduck_call.py <spec.yaml>` from the VS Code terminal or a launch config.

---

## Handy make commands

| From `templates/04-vllm-mcp-coder/` (vLLM) | From `templates/05-ollama-mcp-coder/` (Ollama) | Does |
|---|---|---|
| `make start` | `make up` | Start the server (MCP SSE :8000) |
| `make logs` | `make logs` | Follow logs until ready |
| `make ide-config` | `make ide-config` | Print the SSE URL + IDE config snippets |
| `make stop` | `make down` | Stop the server |

> **Note:** the `recommend_model` / `recommend_context_window` tools require an image built from
> the current source. If you built the image earlier, run `make build` again so they go live.

---

## Spec format (for direct calls & write_and_fix)

A spec is YAML with either `conditions:` (natural-language cases — the local model writes the
tests) or `tests:` (ready pytest). Ready-made examples: [`experiments/tasks/`](../experiments/tasks).

```yaml
name: find_item
filename: find_item.py
language: python
description: |
  Linear search on a flat or one-level-nested list. Return [index, value];
  for a match inside a nested list return [[i, j], value]; raise ValueError if absent.
conditions: |
  - If lst is empty -> raise ValueError
  - If find_item(['a', 10], 'a') -> return [0, 'a']
  - If find_item([['a','b'], [1,5]], 5) -> return [[1, 1], 5]
```
