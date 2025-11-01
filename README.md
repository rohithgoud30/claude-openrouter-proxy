# Claude ↔ OpenRouter Proxy

Proxy service that translates Anthropic-style Claude API requests into OpenRouter's OpenAI-compatible chat API. It lets clients that expect Claude endpoints call OpenRouter models (default: `openai/gpt-oss-120b`) without code changes.

## Prerequisites
- Python 3.10 or newer
- OpenRouter API key with access to the target model
- Optional: [`uv`](https://github.com/astral-sh/uv) or `pip` for dependency management

## Quick Start
1. **Install dependencies**
   ```bash
   uv pip install -r requirements.txt
   # or: python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
   ```
2. **Configure environment**
   - Set `OPENROUTER_API_KEY` in your shell *or* create a `.env` file beside `claude_openrouter_proxy.py`:
     ```
     OPENROUTER_API_KEY=sk-or-...
     OPENROUTER_SITE_URL=https://your-domain.example   # optional, used for OpenRouter analytics
     OPENROUTER_SITE_NAME=Claude Proxy                  # optional
     ```
   - The proxy reports itself to clients as `claude-sonnet-4-5-20250929`. Override the underlying OpenRouter model by setting `OPENROUTER_MODEL`.
3. **Run the server**
   ```bash
   python claude_openrouter_proxy.py
   # or
   uv run python claude_openrouter_proxy.py
   ```
   The Flask app listens on `http://0.0.0.0:8000`.

## Endpoints
- `/v1/messages` – Accepts Claude-style chat requests, forwards them to OpenRouter, and returns Claude-format responses. Supports streaming when the client sets `"stream": true`.
- `/v1/messages/count_tokens` – Rough character-based token estimate for Claude clients that query it.
- `/v1/models` – Minimal model list containing the reported Claude model ID.
- `/health` – Returns `{"status": "healthy"}` when the API key is configured; otherwise `{"status": "degraded"}`.

## Usage Examples
**Non-streaming completion**
```bash
curl -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
        "model": "claude-sonnet-4-5-20250929",
        "messages": [{"role": "user", "content": "Say hello from OpenRouter"}]
      }'
```

**Streaming completion**
```bash
curl -N -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
        "model": "claude-sonnet-4-5-20250929",
        "stream": true,
        "messages": [{"role": "user", "content": "Stream a short poem"}]
      }'
```
Each streamed line is a Claude `content_block_delta` JSON object. The final line signals `message_delta` with `stop_reason: "end_turn"`.

## Claude Code Setup Examples
Point the Claude CLI or desktop app at the proxy by keeping only the Claude-style API key and base URL variables and unsetting the token variant. Use one of the platform-specific snippets below.

### macOS / Linux (zsh or bash)
```bash
# Inspect what Claude variables are currently set
env | grep -E 'ANTHROPIC_|ANTHROPIC_BASE_URL'

# Keep the proxy-friendly pair
export ANTHROPIC_API_KEY="dummy-key"
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000"

# Remove the token variant for this shell session
unset ANTHROPIC_AUTH_TOKEN

# Call any OpenRouter model through the proxy
claude --model openai/gpt-oss-120b "Write a short status update."
```
Leave off the quoted prompt (for example just run `claude --model openai/gpt-oss-120b`) to drop into the CLI's interactive chat loop.

### Windows (PowerShell)
```powershell
# Inspect Anthropic-related environment variables
Get-ChildItem Env: | Where-Object { $_.Name -match 'ANTHROPIC_|ANTHROPIC_BASE_URL' }

# Keep the proxy-friendly pair for the current session
$env:ANTHROPIC_API_KEY = "dummy-key"
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:8000"

# Remove the token variant
Remove-Item Env:ANTHROPIC_AUTH_TOKEN -ErrorAction SilentlyContinue

# Call any OpenRouter model exposed by the proxy
claude.exe --model openai/gpt-oss-120b "Draft a release note."
```
Omit the trailing prompt to enter the interactive interface instead of sending a one-off command.

Replace `openai/gpt-oss-120b` with any other OpenRouter model ID your key can access. To persist the environment variables, add the relevant commands to your shell profile or PowerShell `$PROFILE`.

## Deployment Notes
- Behind a reverse proxy, be sure to forward the `Authorization` header and allow streaming responses.
- The proxy reuses a single `requests.Session`; restart the process to pick up new environment variables.
- For production use consider setting `FLASK_ENV=production` or running with a WSGI server (e.g., `gunicorn claude_openrouter_proxy:app`).

## Troubleshooting
- **401/403 from OpenRouter** – Verify `OPENROUTER_API_KEY` and that your account has access to the configured model.
- **Model unavailable** – Override `OPENROUTER_MODEL` with one you can access.
- **Token count mismatch** – `count_tokens` uses a simple character heuristic; clients should treat it as an estimate.
- **Claude Code warns about multiple auth methods** – Clear `ANTHROPIC_AUTH_TOKEN` and keep only `ANTHROPIC_API_KEY` + `ANTHROPIC_BASE_URL` when pointing Claude at the proxy.
