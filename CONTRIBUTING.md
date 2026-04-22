# Contributing to DockeDuck 🦆

First off, thank you for considering contributing to DockeDuck! 

## How to add a new Template

We welcome new templates for different frameworks (e.g., Django, 
Next.js, Data Engineering stacks). To add a template:

1. **Self-Contained:** Your template must be completely self-contained in its own folder under `templates/`.
2. **Non-Root User:** You MUST use the standard non-root `appuser` architecture (see existing templates).
3. **Ergonomics:** Provide a `Makefile` with at least a `build` and a `run/start` command.
4. **Testing:** Ensure your template builds successfully without errors.

## Running Tests Locally

Before submitting a Pull Request, please run the test suite to ensure all templates build correctly:

```bash
pip install pytest
make test
```

## Pull Request Process

1. Fork the repo and create your branch from main.
2. Ensure the GitHub Actions CI passes.
3. Update the README.md if you added a new template.
4. Update the Root `Makefile`

