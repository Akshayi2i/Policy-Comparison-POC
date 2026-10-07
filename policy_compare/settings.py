"""Runtime settings (.env) and domain configuration (config/*.yaml)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # OpenAI-compatible endpoint (RunPod vLLM proxy), e.g. https://<pod>-8000.proxy.runpod.net/v1
    llm_base_url: Optional[str] = None
    llm_api_key: str = "EMPTY"
    llm_model: str = "Qwen/Qwen3-VL-8B-Instruct"
    llm_max_context: int = 32768
    llm_max_output_tokens: int = 4096
    llm_temperature: float = 0.1
    llm_top_p: float = 0.9
    llm_timeout_s: float = 180.0
    llm_retries: int = 3
    llm_stream: bool = True
    llm_structured: str = "json_schema"  # json_schema | guided_json | none
    llm_cache_dir: Path = ROOT / "output" / ".llm_cache"
    # G: second pass over the model's own statements (only ever reverts to rule-based text)
    llm_judge: bool = True
    # F: read scanned pages with a vision model. auto = on when the model name contains "VL"
    llm_vision: str = "auto"
    ocr_max_pages: int = 15
    source_pdf_dir: Optional[Path] = None      # folder with the original policy PDFs (matched by file name / sha256)

    config_dir: Path = ROOT / "config"
    output_dir: Path = ROOT / "output"     # every report (PDF + report JSON) is saved here

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url)

    @property
    def vision_enabled(self) -> bool:
        v = (self.llm_vision or "auto").lower()
        return self.llm_enabled and (v == "true" or (v == "auto" and "vl" in self.llm_model.lower()))


@lru_cache(maxsize=None)
def settings() -> Settings:
    return Settings()


@lru_cache(maxsize=None)
def config(name: str) -> dict[str, Any]:
    """Load config/<name>.yaml (cached)."""
    path = settings().config_dir / f"{name}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
