# MCP Coder — User Prompts

Templates for the two main usage flows.

---

## Flow A — Full delegation (local model does everything, Claude rescues if needed)

### System prompt (set once in Claude settings or `CLAUDE.md`)

```
You are a code generation orchestrator backed by a local model via MCP.

Your job:
1. When given a task YAML, call `write_and_fix` with the YAML content immediately.
   Do NOT write any code yourself on the first attempt.
2. Wait for the local model to finish.
3. If the result is DONE: present the finished file to the user. Stop.
4. If the result is FAILED (local model exhausted retries): read the YAML spec,
   the last attempted code, and the test failures from the tool response,
   then fix the implementation yourself and return the corrected code.

Never skip step 1. Never write code before calling `write_and_fix`.
```

### User message template

```
Implement the task in this YAML spec using the local model.
Call write_and_fix with the full YAML content below.
Only fix it yourself if the local model fails.

---
{paste your YAML spec here}
```

**Example:**

```
Implement the task in this YAML spec using the local model.
Call write_and_fix with the full YAML content below.
Only fix it yourself if the local model fails.

---
name: binary_search
type: function
filename: binary_search.py
language: python
description: |
  Linear search on a list of any types.
  Input: list[any], target element
  Return: [index, target_value]

conditions: |
  - If empty list -> raise ValueError
  - If single element found -> return [0, element]
  - If single element not found -> raise ValueError
  - If function(['a', 10, -5, 'c'], 'a') -> return [0, 'a']
  - If target absent in multi-element list -> raise ValueError
```

---

## Flow B — Sequence of tasks (batch)

### User message template

```
Implement each task in the following YAML specs, one at a time.
For each task: call write_and_fix with the YAML content, wait for the result,
then proceed to the next. Only fix a task yourself if the local model fails.

---
TASK 1:
{yaml spec 1}

---
TASK 2:
{yaml spec 2}

---
TASK 3:
{yaml spec 3}
```

---

## YAML spec format

```yaml
name: short_identifier          # e.g. lru_cache
type: function|class|module|connected functions|project
filename: output_file.py        # exact filename to generate
language: python
description: |
  Plain-English description of the module/class/function.
  Include: input types, return types, key classes/methods and their signatures.

conditions: |
  - If <input scenario> -> <expected output or exception>
  - If <input scenario> -> <expected output or exception>
  ...
```

**Conditions format rules:**
- One condition per line, starting with `- If`
- Use `-> raise ExceptionType` for error cases
- Use `-> return value` or `-> output value` for success cases
- Include concrete examples with actual values where helpful
- Each condition becomes one pytest test function

---

## When Claude rescue is triggered

When the local model fails, Claude receives:

```
A local model failed to implement the task below after exhausting all retries.

Task spec (YAML):
  <the YAML content>

Last attempted code:
  <the last code the local model generated>

Test failures:
  <pytest output showing what failed>

Write the complete, corrected Python implementation that passes all tests.
```

Claude then writes one fixed version. This rescue call costs approximately
$0.01–$0.03 depending on code size. On tasks the local model handles well,
cost is $0.
