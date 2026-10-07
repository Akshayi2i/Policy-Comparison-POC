"""The model layer against a local OpenAI-compatible mock (streaming, json_schema, fallback, guards)."""
import os
import shutil
import socket

import pytest

from tests.conftest import POLICY_1, POLICY_2, ROOT


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def mock_llm(request, tmp_path):
    from mock_llm_server import serve
    from policy_compare.settings import settings

    port = _free_port()
    srv = serve(port, reject_json_schema=getattr(request, "param", False))
    os.environ.update({"LLM_BASE_URL": f"http://127.0.0.1:{port}/v1", "LLM_MODEL": "mock-qwen3-vl",
                       "LLM_CACHE_DIR": str(tmp_path / "cache")})
    settings.cache_clear()
    yield
    srv.shutdown()
    for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_CACHE_DIR"):
        os.environ.pop(k, None)
    settings.cache_clear()


@pytest.mark.parametrize("mock_llm", [False, True], indirect=True, ids=["json_schema", "fallback_guided_json"])
def test_pipeline_with_model(mock_llm):
    from policy_compare.engine import analyse
    from policy_compare.report.build import build_report

    an = analyse(POLICY_1, POLICY_2, focus=["communicable disease"], use_llm=True)
    calls = an.audit["llm_calls"]
    assert calls and all(c["ok"] for c in calls)
    assert {"section_policy", "section_forms", "synthesis"} <= {c["task"] for c in calls}
    rep = build_report(an)
    assert rep.executive.verdict.startswith("**Mock verdict")
    # model text never changes facts: values and counts still come from the engine
    assert rep.executive.kpis[2].value == "+0 / −2"
    # guards: the policy section only has administrative changes, so the model's "high" is capped
    assert rep.sections[0].risk.level == "low"


def test_client_ping(mock_llm):
    from policy_compare.llm.client import LLMClient
    assert LLMClient().ping() == ["mock-qwen3-vl"]
