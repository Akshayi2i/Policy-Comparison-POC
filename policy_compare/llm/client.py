"""OpenAI-compatible client for Qwen3-VL-Instruct served by vLLM behind a RunPod proxy URL.

- streaming responses (the RunPod HTTP proxy closes requests that stay silent for ~100 s)
- structured output: vLLM response_format=json_schema -> extra_body guided_json -> prompt-only + validate/repair
- disk cache keyed by (model, messages, schema) so re-renders and retries do not re-bill the pod
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from policy_compare.settings import settings

log = logging.getLogger("policy_compare.llm")
T = TypeVar("T", bound=BaseModel)
PROMPTS = Path(__file__).parent / "prompts"


class LLMError(RuntimeError):
    pass


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def inline_refs(schema: dict) -> dict:
    """Inline $defs/$ref so every guided-decoding backend accepts the schema."""
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def walk(node, is_props: bool = False):
        if isinstance(node, dict):
            if "$ref" in node and not is_props:
                ref = node["$ref"].split("/")[-1]
                return walk(copy.deepcopy(defs[ref]))
            if is_props:   # keys here are field names (one may be called "title") — keep them all
                return {k: walk(v) for k, v in node.items()}
            return {k: walk(v, is_props=(k == "properties")) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def _extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
    return text[start:]


class LLMClient:
    def __init__(self):
        from openai import OpenAI

        s = settings()
        if not s.llm_base_url:
            raise LLMError("LLM_BASE_URL is not set")
        self.s = s
        self.client = OpenAI(base_url=s.llm_base_url.rstrip("/"), api_key=s.llm_api_key or "EMPTY", timeout=s.llm_timeout_s, max_retries=0)
        self.mode = s.llm_structured          # may degrade json_schema -> guided_json -> none on 400s
        self.calls: list[dict] = []
        s.llm_cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------- public ----------
    def ping(self) -> list[str]:
        return [m.id for m in self.client.models.list().data]

    def structured(self, task: str, user: str, out: Type[T], system: Optional[str] = None, max_tokens: Optional[int] = None) -> T:
        system = system or prompt("system")
        schema = inline_refs(out.model_json_schema())
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        key = hashlib.sha256(json.dumps([self.s.llm_model, messages, schema, self.s.llm_temperature], sort_keys=True).encode()).hexdigest()[:32]
        cache = self.s.llm_cache_dir / f"{task}-{key}.json"
        if cache.exists():
            try:
                obj = out.model_validate_json(cache.read_text(encoding="utf-8"))
                self.calls.append({"task": task, "cached": True, "ok": True})
                return obj
            except ValidationError:
                cache.unlink(missing_ok=True)

        text, err = "", None
        for attempt in range(self.s.llm_retries):
            t0 = time.time()
            try:
                text = self._complete(messages, schema, out.__name__, max_tokens or self.s.llm_max_output_tokens)
                obj = out.model_validate_json(_extract_json(text))
                cache.write_text(obj.model_dump_json(), encoding="utf-8")
                self.calls.append({"task": task, "ok": True, "mode": self.mode, "seconds": round(time.time() - t0, 1), "attempt": attempt + 1})
                return obj
            except ValidationError as e:
                err = e
                log.warning("%s: output failed validation (attempt %d): %s", task, attempt + 1, str(e)[:300])
                messages = messages[:2] + [
                    {"role": "assistant", "content": text[:6000]},
                    {"role": "user", "content": "That JSON did not match the required schema:\n" + str(e)[:1500] +
                     "\nReturn only the corrected JSON object."}]
            except Exception as e:  # network / server errors: back off and retry
                err = e
                log.warning("%s: request failed (attempt %d): %s", task, attempt + 1, str(e)[:300])
                time.sleep(min(2 ** attempt, 8))
        self.calls.append({"task": task, "ok": False, "error": str(err)[:300]})
        raise LLMError(f"{task}: {err}")

    # ---------- transport ----------
    def _budget(self, messages: list[dict], schema: dict, wanted: int) -> int:
        """Output tokens that still fit the served context (prompt + output <= LLM_MAX_CONTEXT); vLLM rejects
        requests that do not. The prompt is estimated at ~3 characters per token, which errs on the safe side."""
        prompt_chars = sum(len(m["content"]) for m in messages) + len(json.dumps(schema))
        room = self.s.llm_max_context - prompt_chars // 3 - 256
        return max(512, min(wanted, room))

    def _complete(self, messages: list[dict], schema: dict, name: str, max_tokens: int) -> str:
        kwargs = dict(model=self.s.llm_model, messages=messages, temperature=self.s.llm_temperature, top_p=self.s.llm_top_p,
                      max_tokens=self._budget(messages, schema, max_tokens), stream=self.s.llm_stream)
        while True:
            call = dict(kwargs)
            if self.mode == "json_schema":
                call["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}
            elif self.mode == "guided_json":
                call["extra_body"] = {"guided_json": schema}
            else:
                call["messages"] = messages[:-1] + [{"role": messages[-1]["role"], "content": messages[-1]["content"] +
                                                     "\n\nReturn only a JSON object matching this JSON Schema:\n" + json.dumps(schema)}]
            try:
                return self._send(call)
            except Exception as e:
                status = getattr(e, "status_code", None)
                msg = str(e).lower()
                structured_error = any(k in msg for k in ("response_format", "json_schema", "guided", "structured", "grammar", "xgrammar", "outlines"))
                if status in (400, 422) and self.mode != "none" and structured_error:
                    nxt = {"json_schema": "guided_json", "guided_json": "none"}[self.mode]
                    log.warning("structured mode %s rejected (%s); falling back to %s", self.mode, str(e)[:200], nxt)
                    self.mode = nxt
                    continue
                raise

    def _send(self, call: dict) -> str:
        if not call.get("stream"):
            resp = self.client.chat.completions.create(**call)
            return resp.choices[0].message.content or ""
        parts = []
        stream = self.client.chat.completions.create(**call)
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                parts.append(chunk.choices[0].delta.content)
        return "".join(parts)
