"""Where reports are saved: everything goes to the output folder (D:\\POC4\\output by default, OUTPUT_DIR in .env)."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from policy_compare.settings import settings


def output_dir() -> Path:
    d = settings().output_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def report_name(insured: str, renewal_period: str, edition: str = "colour") -> str:
    """'Derek P Halstead Construction Co Inc', '09/24/2026 – 09/24/2027' ->
    'Derek_P_Halstead_Construction_Co_Inc_renewal_comparison_2026-09-24.pdf'."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", insured or "policy").strip("_")[:80] or "policy"
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", renewal_period or "")
    stamp = f"_{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else f"_{datetime.now():%Y-%m-%d}"
    return f"{slug}_renewal_comparison{stamp}{'_grayscale' if edition == 'grayscale' else ''}.pdf"


def resolve(out: Optional[str], default_name: str) -> Path:
    """No --out: output/<default name>. A bare file name: output/<name>. A path with folders: used as given."""
    if not out:
        return output_dir() / default_name
    p = Path(out)
    if p.parent == Path("."):
        return output_dir() / (p.name if p.suffix.lower() == ".pdf" else p.name + ".pdf")
    return p
