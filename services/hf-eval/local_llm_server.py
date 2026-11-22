#!/usr/bin/env python3
"""Loopback-only, single-request JSON generation server for a local GGUF model (llama-cpp-python).

Exposes a minimal subset of the OpenAI chat-completions shape so a harness can switch to llama-server or vLLM later:
POST /v1/chat/completions {messages, response_format: {type: "json_schema", json_schema: {schema}}, max_tokens, temperature}
GET /health

* Binds 127.0.0.1 only; there is no authentication, so never expose the port.
* Qwen3.5 thinking is disabled by an empty <think></think> block in a ChatML prompt (the model card's non-thinking mode).
* Output is constrained by a GBNF grammar compiled from the request's JSON schema.
* Requests are processed one at a time; the model is CPU-bound and concurrency would only add queueing.
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llama_cpp import Llama, LlamaGrammar

MAX_BODY = 1_000_000


def chatml(messages):
    parts = []
    for message in messages:
        role = message.get("role")
        if role not in ("system", "user", "assistant") or not isinstance(message.get("content"), str):
            raise ValueError("messages need system/user/assistant roles with string content")
        parts.append(f"<|im_start|>{role}\n{message['content']}<|im_end|>\n")
    return "".join(parts) + "<|im_start|>assistant\n<think>\n\n</think>\n\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-id", required=True, help="label returned in responses")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--ctx", type=int, default=8192)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()

    llm = Llama(model_path=args.model, n_ctx=args.ctx, n_threads=args.threads, verbose=False)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"status": "ok", "model": args.model_id})
            else:
                self.reply(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                return self.reply(404, {"error": "not_found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self.reply(413, {"error": "body_size"})
            try:
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                prompt = chatml(request["messages"])
                schema = request.get("response_format", {}).get("json_schema", {}).get("schema")
                grammar = LlamaGrammar.from_json_schema(json.dumps(schema), verbose=False) if schema else None
                max_tokens = min(int(request.get("max_tokens", 512)), 2048)
                temperature = float(request.get("temperature", 0))
            except Exception:  # noqa: BLE001 - malformed client input; no detail echoed
                return self.reply(400, {"error": "invalid_request"})
            with lock:
                prompt_tokens = len(llm.tokenize(prompt.encode("utf-8"), special=True))
                if prompt_tokens + max_tokens > args.ctx:
                    return self.reply(400, {"error": "context_length", "promptTokens": prompt_tokens})
                started = time.perf_counter()
                out = llm.create_completion(prompt, max_tokens=max_tokens, temperature=temperature, grammar=grammar, seed=0)
                elapsed = time.perf_counter() - started
                llm.reset()
            choice = out["choices"][0]
            self.reply(200, {
                "object": "chat.completion", "model": args.model_id,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": choice["text"]}, "finish_reason": choice.get("finish_reason")}],
                "usage": out.get("usage"), "timings": {"totalMs": round(elapsed * 1000)},
            })

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(json.dumps({"status": "listening", "host": "127.0.0.1", "port": args.port, "model": args.model_id}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
