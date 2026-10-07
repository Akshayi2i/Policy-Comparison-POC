"""A tiny OpenAI-compatible server for testing the LLM plumbing without a RunPod pod.

It answers /v1/chat/completions (streaming or not) with a JSON object that satisfies the schema sent in
response_format / guided_json / the prompt, echoing real item ids from the prompt so the engine's guards accept
it. Use --reject-json-schema to force the client's fallback to guided_json.

usage: python tools/mock_llm_server.py --port 8765 [--reject-json-schema]
then:  LLM_BASE_URL=http://127.0.0.1:8765/v1 python -m policy_compare ...
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REJECT_JSON_SCHEMA = False


def fake(schema: dict, prompt: str, path: str = ""):
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return fake([s for s in schema["anyOf"] if s.get("type") != "null"][0], prompt, path)
    if t == "object":
        return {k: fake(v, prompt, f"{path}.{k}") for k, v in schema.get("properties", {}).items()}
    if t == "array":
        item = schema.get("items", {})
        if path.endswith(".items") and "id" in item.get("properties", {}):
            ids = list(dict.fromkeys(re.findall(r'"id": "(f\d{3})"', prompt)))
            out = []
            for i in ids:
                obj = fake(item, prompt, path + "[]")
                obj["id"] = i
                out.append(obj)
            return out
        if path.endswith(".critical"):
            m = re.search(r"Candidate critical changes.*?\n(\[.*?\n\])", prompt, re.S)
            ids = re.findall(r'"id": "(f\d{3})"', m.group(1)) if m else []
            return [{**fake(item, prompt, path + "[]"), "id": i} for i in ids]
        if path.endswith(".bullets"):
            return ["Mock key point one.", "Mock key point two."]
        return []
    if t == "string":
        name = path.rsplit(".", 1)[-1]
        return {"lead": "Mock verdict for the account manager.", "headline": "Mock section headline from the model.",
                "why": "Mock explanation of why this matters to the insured.", "title": "Mock critical title",
                "next_step": "Ask the carrier to confirm.", "confidence_note": "Confidence is High because the mock says so.",
                "body": "The mock model summarises the drivers here."}.get(name, f"Mock {name}")
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return False
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json({"object": "list", "data": [{"id": "mock-qwen3-vl", "object": "model"}]})
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        rf = body.get("response_format") or {}
        if rf.get("type") == "json_schema" and REJECT_JSON_SCHEMA:
            self._json({"error": {"message": "response_format json_schema not supported", "type": "BadRequestError"}}, 400)
            return
        schema = (rf.get("json_schema") or {}).get("schema") or body.get("guided_json")
        prompt = "\n".join(m.get("content", "") for m in body.get("messages", []))
        if schema is None:
            m = re.search(r"JSON Schema:\n(\{.*\})\s*$", prompt, re.S)
            schema = json.loads(m.group(1)) if m else {"type": "object", "properties": {}}
        content = json.dumps(fake(schema, prompt))
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(0, len(content), 40):
                chunk = {"id": "x", "object": "chat.completion.chunk", "created": int(time.time()), "model": body.get("model"),
                         "choices": [{"index": 0, "delta": {"content": content[i:i + 40]}, "finish_reason": None}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self._json({"id": "x", "object": "chat.completion", "created": int(time.time()), "model": body.get("model"),
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": len(content) // 4, "total_tokens": 0}})

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(port: int, reject_json_schema: bool = False) -> ThreadingHTTPServer:
    global REJECT_JSON_SCHEMA
    REJECT_JSON_SCHEMA = reject_json_schema
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--reject-json-schema", action="store_true")
    a = ap.parse_args()
    serve(a.port, a.reject_json_schema)
    print(f"mock OpenAI-compatible server on http://127.0.0.1:{a.port}/v1")
    while True:
        time.sleep(3600)
