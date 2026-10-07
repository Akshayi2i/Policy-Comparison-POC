import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# tests run offline unless a test opts into the mock server
os.environ.pop("LLM_BASE_URL", None)

POLICY_1 = ROOT / "canonical_json" / "compare_policy_1.canonical.json"   # 2026-27 (renewal)
POLICY_2 = ROOT / "canonical_json" / "compare_policy_2.canonical.json"   # 2025-26 (expiring)
