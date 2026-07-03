"""Structural checks: every template ships the files needed to build and run."""
import os

import pytest

REQUIRED = {
    "01-base-cuda": ["Dockerfile", "Makefile"],
    "02-pytorch-lightning": ["Dockerfile", "Makefile"],
    "03-fastapi-service": ["Dockerfile", "Makefile"],
    "04-vllm-mcp-coder": [
        "Dockerfile", "Makefile", "entrypoint.sh", "requirements.txt", ".env.example",
        "src/server.py", "src/vlm.py", "src/prompts.py", "src/validator.py", "src/hardware.py",
    ],
    "05-ollama-mcp-coder": [
        "Dockerfile", "Makefile", "docker-compose.yml", "entrypoint.sh",
        "requirements.txt", ".env.example",
        "src/server.py", "src/vlm.py", "src/prompts.py", "src/validator.py", "src/hardware.py",
    ],
}


@pytest.mark.parametrize("template", REQUIRED.keys())
def test_template_directory_exists(template):
    assert os.path.isdir(f"templates/{template}"), f"Template {template} is missing"


@pytest.mark.parametrize(
    "template,filename",
    [(t, f) for t, files in REQUIRED.items() for f in files],
)
def test_required_file_present(template, filename):
    path = f"templates/{template}/{filename}"
    assert os.path.isfile(path), f"Missing {path}"


def test_clearml_config_example_exists():
    path = "templates/02-pytorch-lightning/configs/clearml.conf.example"
    assert os.path.exists(path), f"ClearML config example is missing at {path}"


def test_no_legacy_dockduck_naming():
    """Project brand is DockeDuck — the old 'dockduck' image prefix must be gone."""
    offenders = []
    for root, _dirs, files in os.walk("templates"):
        for name in files:
            if name.endswith((".py", ".yml", ".yaml", "Dockerfile", "Makefile")) or name == "Makefile":
                p = os.path.join(root, name)
                try:
                    text = open(p, encoding="utf-8").read()
                except (UnicodeDecodeError, OSError):
                    continue
                if "dockduck" in text:
                    offenders.append(p)
    assert not offenders, f"Legacy 'dockduck' naming found in: {offenders}"
