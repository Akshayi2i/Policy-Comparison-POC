"""Reports are saved in the output folder (CLI default and API)."""
import os
from pathlib import Path

import pytest

from tests.conftest import POLICY_1, POLICY_2


@pytest.fixture()
def tmp_output(tmp_path):
    from policy_compare.settings import settings
    os.environ["OUTPUT_DIR"] = str(tmp_path / "output")
    settings.cache_clear()
    yield tmp_path / "output"
    os.environ.pop("OUTPUT_DIR", None)
    settings.cache_clear()


def test_resolve_rules(tmp_output):
    from policy_compare.output import report_name, resolve
    name = report_name("Derek P Halstead Construction Co Inc", "09/24/2026 – 09/24/2027")
    assert name == "Derek_P_Halstead_Construction_Co_Inc_renewal_comparison_2026-09-24.pdf"
    assert report_name("X", "09/24/2026 – 09/24/2027", "grayscale").endswith("_2026-09-24_grayscale.pdf")
    assert resolve(None, name) == tmp_output / name
    assert resolve("my_report", name) == tmp_output / "my_report.pdf"
    assert resolve("D:/elsewhere/r.pdf", name) == Path("D:/elsewhere/r.pdf")


def test_cli_and_api_save_into_output(tmp_output):
    from fastapi.testclient import TestClient

    from policy_compare.__main__ import main
    from policy_compare.api import app

    assert main(["--a", str(POLICY_1), "--b", str(POLICY_2), "--no-llm"]) == 0
    stem = "Derek_P_Halstead_Construction_Co_Inc_renewal_comparison_2026-09-24"
    assert (tmp_output / f"{stem}.pdf").exists() and (tmp_output / f"{stem}.report.json").exists()

    (tmp_output / f"{stem}.pdf").unlink()
    with open(POLICY_1, "rb") as a, open(POLICY_2, "rb") as b:
        r = TestClient(app).post("/compare", files={"policy_a": a, "policy_b": b}, data={"llm": "false"})
    assert r.status_code == 200 and r.headers["x-saved-to"].endswith(f"{stem}.pdf")
    assert (tmp_output / f"{stem}.pdf").exists()
    assert (tmp_output / f"{stem}.carrier_email.txt").exists() and (tmp_output / f"{stem}.client_letter.txt").exists()


def test_ask_cli_and_api(capsys):
    from fastapi.testclient import TestClient

    from policy_compare.__main__ import main
    from policy_compare.api import app

    assert main(["--a", str(POLICY_1), "--b", str(POLICY_2), "--no-llm", "--ask", "snow removal operations"]) == 0
    out = capsys.readouterr().out
    assert "retrieval only" in out and "passage  : [" in out

    with open(POLICY_1, "rb") as a, open(POLICY_2, "rb") as b:
        r = TestClient(app).post("/ask", files={"policy_a": a, "policy_b": b},
                                 data={"question": "Is snow removal excluded?", "llm": "false"})
    assert r.status_code == 200
    body = r.json()
    assert body["verified"] is False and any("snow" in p["text"].lower() for p in body["passages"])
