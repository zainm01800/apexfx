"""
Lightweight local AI proxy server for APEX backtester.
Listens on port 3001, accepts POST /api/ai requests from Python scripts,
and forwards them directly to DeepSeek (or any OpenAI-compatible endpoint).

Usage:
  python proxy_server.py

Reads APEX_LOCAL_LLM_KEY and APEX_TWELVE_DATA_KEY from engine/.env automatically.
"""

import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.request
import urllib.error

# Load env from engine/.env
env_path = Path(__file__).parent / "engine" / ".env"
if not env_path.exists():
    env_path = Path(__file__).parent / ".env"

if env_path.exists():
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

DEEPSEEK_KEY = os.environ.get("APEX_LOCAL_LLM_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

PORT = 3001


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[proxy] {self.address_string()} {format % args}")

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def do_POST(self):
        if self.path != "/api/ai":
            self.send_response(404)
            self._set_cors()
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')
            return

        length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(length)
        try:
            body = json.loads(body_bytes)
        except Exception:
            self.send_response(400)
            self._set_cors()
            self.end_headers()
            self.wfile.write(b'{"error":"invalid json"}')
            return

        prompt = body.get("prompt", "")
        system = body.get("system", "")
        max_tokens = int(body.get("max_tokens", 2000))
        temperature = float(body.get("temperature", 0.35))

        # Use key from request or env
        api_key = body.get("localLlmKey") or DEEPSEEK_KEY
        model = body.get("localLlmModel") or DEEPSEEK_MODEL
        api_url = body.get("localLlmUrl") or DEEPSEEK_URL

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode("utf-8")

        req = urllib.request.Request(
            api_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"]
            out = json.dumps({"text": text, "provider": "deepseek", "model": model}).encode("utf-8")
            self.send_response(200)
            self._set_cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"[proxy] DeepSeek error {e.code}: {err_body}")
            self.send_response(502)
            self._set_cors()
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"DeepSeek {e.code}: {err_body[:200]}"}).encode())
        except Exception as e:
            print(f"[proxy] Error: {e}")
            self.send_response(500)
            self._set_cors()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _set_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


if __name__ == "__main__":
    if not DEEPSEEK_KEY:
        print("[proxy] WARNING: APEX_LOCAL_LLM_KEY not set - requests will fail auth")
    else:
        print(f"[proxy] DeepSeek key loaded: {DEEPSEEK_KEY[:6]}...")
    print(f"[proxy] Starting proxy server on http://localhost:{PORT}")
    print(f"[proxy] Forwarding POST /api/ai → {DEEPSEEK_URL}")
    print(f"[proxy] Model: {DEEPSEEK_MODEL}")
    print("[proxy] Press Ctrl+C to stop\n")
    server = HTTPServer(("localhost", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[proxy] Stopped.")
