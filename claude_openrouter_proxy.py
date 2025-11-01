"""
Claude-to-OpenRouter proxy
Reports: claude-sonnet-4-5-20250929
Calls: https://openrouter.ai/api/v1/chat/completions
Model used on OpenRouter: openai/gpt-oss-120b
"""

import os
import json
import time
from typing import Dict, Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

from flask import Flask, request, jsonify, Response
import requests

app = Flask(__name__)

# OpenRouter settings
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-oss-120b"  # the one from your curl
# get API key from env
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# what we tell the client
ANTHROPIC_MODEL_ID = "claude-sonnet-4-5-20250929"

session = requests.Session()


class ClaudeOpenRouterProxy:
    def __init__(self):
        self.conversation_history = {}

    def claude_to_openrouter_body(self, claude_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert Claude messages to OpenRouter chat/completions format.
        Claude can send content as list of blocks.
        OpenRouter speaks OpenAI style. :contentReference[oaicite:4]{index=4}
        """
        messages = claude_request.get("messages", [])
        or_messages = []

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")

            if isinstance(content, list):
                text_content = ""
                for block in content:
                    if block.get("type") == "text":
                        text_content += block.get("text", "")
                content = text_content

            or_messages.append({"role": role, "content": content})

        body = {
            "model": claude_request.get("model", OPENROUTER_MODEL),
            "messages": or_messages,
        }

        # if Claude client asked for streaming we forward it
        if claude_request.get("stream", False):
            body["stream"] = True

        # pass through max_tokens or temperature if present
        if "max_tokens" in claude_request:
            body["max_tokens"] = claude_request["max_tokens"]
        if "temperature" in claude_request:
            body["temperature"] = claude_request["temperature"]

        return body

    def openrouter_to_claude(self, or_response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Turn OpenRouter non streaming response into Claude style.
        OpenRouter is OpenAI compatible and returns choices[0].message.content. :contentReference[oaicite:5]{index=5}
        """
        choices = or_response.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
        else:
            content = json.dumps(or_response)

        return {
            "id": f"msg_{int(time.time())}",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": content,
                }
            ],
            "model": ANTHROPIC_MODEL_ID,
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": or_response.get("usage", {"input_tokens": 0, "output_tokens": 0}),
        }


def openrouter_headers() -> Dict[str, str]:
    if not OPENROUTER_API_KEY:
        # You can also raise here
        return {"Content-Type": "application/json"}
    # From OpenRouter auth docs. :contentReference[oaicite:6]{index=6}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    }
    # Optional
    referer = os.getenv("OPENROUTER_SITE_URL")
    title = os.getenv("OPENROUTER_SITE_NAME")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    return headers


@app.route("/v1/messages/count_tokens", methods=["POST"])
def count_tokens():
    """
    Dummy count for Claude compatible clients.
    """
    body = request.get_json(silent=True) or {}
    messages = body.get("messages", [])
    total_chars = 0

    for m in messages:
        c = m.get("content", "")
        if isinstance(c, list):
            for b in c:
                if b.get("type") == "text":
                    total_chars += len(b.get("text", ""))
        else:
            total_chars += len(str(c))

    input_tokens = total_chars // 4

    return jsonify(
        {
            "input_tokens": input_tokens,
            "output_tokens": 0,
            "total_tokens": input_tokens,
            "model": ANTHROPIC_MODEL_ID,
        }
    )


@app.route("/v1/messages", methods=["POST"])
def handle_messages():
    """
    Accepts Claude style request, calls OpenRouter, returns Claude style.
    Works with /v1/messages?beta=true.
    """
    try:
        claude_request = request.get_json()
        proxy = ClaudeOpenRouterProxy()
        or_body = proxy.claude_to_openrouter_body(claude_request)

        stream = bool(or_body.get("stream"))

        if stream:
            return handle_streaming_request(or_body)
        return handle_non_streaming_request(or_body, proxy)
    except Exception as e:
        return jsonify({"error": f"proxy failed: {str(e)}"}), 500


def handle_non_streaming_request(or_body: Dict[str, Any], proxy: ClaudeOpenRouterProxy):
    try:
        resp = session.post(
            OPENROUTER_URL,
            headers=openrouter_headers(),
            json=or_body,
            timeout=60,
        )
        resp.raise_for_status()
        or_response = resp.json()
        claude_response = proxy.openrouter_to_claude(or_response)
        return jsonify(claude_response)
    except requests.exceptions.RequestException as e:
        return jsonify(
            {
                "error": "OpenRouter request failed",
                "detail": str(e),
                "note": "Check OPENROUTER_API_KEY and that the model openai/gpt-oss-120b is available for your account.",
            }
        ), 500
    except ValueError as e:
        return jsonify(
            {
                "error": "OpenRouter did not return JSON",
                "detail": str(e),
            }
        ), 500


def handle_streaming_request(or_body: Dict[str, Any]):
    """
    Stream OpenRouter SSE to Claude style deltas.
    OpenRouter streams in OpenAI style. We read each data: line and turn it into a delta. :contentReference[oaicite:7]{index=7}
    """
    def generate():
        try:
            with session.post(
                OPENROUTER_URL,
                headers=openrouter_headers(),
                json=or_body,
                timeout=120,
                stream=True,
            ) as r:
                r.raise_for_status()
                # OpenRouter uses text/event-stream, so we read line by line
                for line in r.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    # lines often look like: data: {...}
                    if line.startswith("data:"):
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            break
                        data_json = json.loads(data_str)
                        # OpenAI style: choices[0].delta.content
                        choices = data_json.get("choices", [])
                        if choices:
                            delta_obj = choices[0].get("delta", {})
                            chunk = delta_obj.get("content", "")
                        else:
                            chunk = ""
                        if chunk:
                            event = {
                                "type": "content_block_delta",
                                "delta": {
                                    "type": "text_delta",
                                    "text": chunk,
                                },
                                "model": ANTHROPIC_MODEL_ID,
                            }
                            yield json.dumps(event) + "\n"
                # end of stream
                yield json.dumps(
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "model": ANTHROPIC_MODEL_ID,
                    }
                ) + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return Response(generate(), mimetype="application/json")


@app.route("/v1/models", methods=["GET"])
def list_models():
    """
    Minimal model list for clients that query it.
    """
    return jsonify(
        {
            "data": [
                {
                    "id": ANTHROPIC_MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "anthropic",
                }
            ]
        }
    )


@app.route("/health", methods=["GET"])
def health_check():
    """
    Simple health that pings OpenRouter by calling GET /docs is overkill
    so we just return ok if we have an API key.
    """
    if OPENROUTER_API_KEY:
        return jsonify({"status": "healthy", "openrouter": "configured"})
    return jsonify({"status": "degraded", "openrouter": "no_api_key"}), 200


if __name__ == "__main__":
    print("Starting Claude to OpenRouter Proxy")
    print(f"OpenRouter endpoint: {OPENROUTER_URL}")
    print(f"OpenRouter model: {OPENROUTER_MODEL}")
    print(f"Reporting Anthropic model: {ANTHROPIC_MODEL_ID}")
    app.run(host="0.0.0.0", port=8000, debug=False)
