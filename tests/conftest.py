import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def isolate_ollama_call_log(tmp_path, monkeypatch):
    """Every test that exercises OllamaAdapter._post_chat (directly or via
    run_tool_loop/generate_structured) would otherwise write real log lines
    into this repo's actual logs/ollama_calls.jsonl — same class of test-
    pollution bug already found and fixed once for agent_api_keys.json.
    Autouse + global so no individual test file has to remember this.
    """
    from agent_platform.llm import ollama_adapter
    monkeypatch.setattr(ollama_adapter, "_CALLS_LOG_PATH", tmp_path / "ollama_calls.jsonl")
