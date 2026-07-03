"""Shared helpers: load a module directly from a template's src/ (hyphenated dirs
are not importable as packages, so we load by file path)."""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

TEMPLATES = ["04-vllm-mcp-coder", "05-ollama-mcp-coder"]


def load_module(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses / typing introspection can resolve the module.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
