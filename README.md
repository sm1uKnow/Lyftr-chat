# Lyftr Chat

A multi-turn AI chat built with React, FastAPI, LiteLLM and SQLite. Each answer shows the responding model, response source, server latency and request token counts.

## Live URLs

| Deliverable | URL |
| --- | --- |
| Frontend | Pending deployment to Render |
| Backend | Pending deployment to Railway |
| Public repository | Pending publication to GitHub |
| Demo video | Pending recording against the live URLs |

### Deploy on Railway and Render

1. Create two keys in [Google AI Studio](https://aistudio.google.com/apikey). Use one key per independent Google Cloud project if you want the second key to remain usable when the first project's Gemini quota is exhausted. Confirm both projects have free access to `gemini-3.1-flash-lite`. Leave billing disabled to stay on the provider's free tier. [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) · [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
2. Create an empty **public** GitHub repository, then run the commands below from this directory. Replace `YOUR_USERNAME` and the repo name as appropriate. GitHub may prompt you to sign in.
3. On Railway, choose **Deploy from GitHub repo** and select the repository. In the service settings, set the **Root Directory** to `/backend` so Railway detects its Dockerfile. Attach a volume mounted at `/data`. Set the **Healthcheck Path** to `/health`. [Railway Dockerfiles](https://docs.railway.com/builds/dockerfiles) · [Railway volumes](https://docs.railway.com/volumes/reference)
4. Add the backend variables below in the Railway service's **Variables** tab. In **Networking → Public Networking**, generate the service domain. Use that HTTPS URL as the backend base URL. [Railway public networking](https://docs.railway.com/networking/public-networking)
5. On Render, create a **Static Site** linked to the same repo. Set **Root Directory** to `frontend`, **Build Command** to `npm ci && npm run build`, and **Publish Directory** to `dist`. Add `VITE_API_BASE_URL` with the Railway HTTPS URL. [Render static sites](https://render.com/docs/static-sites)
6. When Render assigns the site URL, set Railway's `ALLOWED_ORIGINS` to that HTTPS origin, without a trailing slash. Redeploy the backend and open both URLs from a private browser window. Replace the pending entries above with the real URLs, run the verification script, and record the demo.

```sh
git init -b main
git add .
git diff --cached --stat
git commit -m "Build Lyftr chat with LiteLLM failover and caching"
git remote add origin https://github.com/YOUR_USERNAME/lyftr-chat.git
git push -u origin main
```

| Railway variable | Value |
| --- | --- |
| `LLM_API_KEYS` | Two actual API keys separated by a comma. Set this only in the backend. |
| `LLM_MODELS` | `gemini/gemini-3.1-flash-lite` |
| `DATABASE_URL` | `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | Your Render site's HTTPS origin |
| `ENABLE_FAILURE_SIMULATION` | `true`, required for the assignment |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `15` (default) |
| `PORT` | Use the value Railway injects |

The free hosting path uses a Railway trial or free allowance for the API and a free Render static site. Railway includes a volume on those plans. Usage can consume the allowance and temporarily suspend the API, so check Railway's dashboard before submission. The backend needs one replica and a mounted volume. Keep provider keys in Railway's service variables or an ignored local `.env`. [Railway free plan](https://docs.railway.com/pricing/free-plan)

## Run locally: with Docker and without

**Docker** requires Docker Desktop or Docker Engine with Compose v2. Copy `.env.example` to `.env` and replace the two API key placeholders. Leave `DATABASE_URL=sqlite:////data/app.db`.

```sh
make up
# Frontend http://localhost:3000
# Backend  http://localhost:8000
make down
```

On Windows without Make, use `docker compose up --build -d` and `docker compose down`. The named volume survives `make down`. Use `docker compose logs -f backend` to inspect startup.

**Without Docker** requires Python 3.12 and Node.js 22.12 or later. For PowerShell, from the repo root run:

```powershell
Copy-Item .env.example .env
# Edit .env with the two keys and change DATABASE_URL to sqlite:///./app.db.
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --env-file ../.env --port 8000
```

On macOS or Linux, use `python3.12 -m venv .venv` and `.venv/bin/python` in the corresponding commands. From a second terminal, run `cd frontend`, `npm ci`, then `npm run dev`. For local development the frontend uses `http://localhost:8000` by default. Vite reads `VITE_API_BASE_URL` from the environment or a frontend-local env file.

## Design decisions: key fallback, caching, SQLite session storage

| Area | Implementation |
| --- | --- |
| Key fallback | The application gateway calls LiteLLM once per key in configured order. Each failed attempt, including rate limits, quota failures, upstream errors and timeouts, advances to the next key. Attempts have a timeout and automatic retries are disabled inside the SDK. This makes the public key ID and attempt count deterministic. If every attempt fails, the gateway returns a JSON 503. |
| Response cache | LiteLLM's built-in local response cache stores successful reply text and usage for one hour. The cache key is a SHA-256 hash of the configured LiteLLM model and the full ordered input, including the system prompt and prior turns. Matching across sessions is possible when their model and full input match. Simulated requests bypass cache reads and writes so the test exercises the remaining real provider keys. Cache entries are local to the backend process. |
| SQLite history | The backend loads each session's system prompt and history, then appends both messages and assistant metadata in one SQLite transaction after successful inference. Startup creates the tables and an index. WAL enables concurrent reads and a per-session lock serializes turns within the API worker. Deploy one worker and one replica. |
| Metadata | LiteLLM-normalized provider usage supplies prompt and completion counts for each response. A cache hit replays the original usage, sets `key_id` to `null` and resets `fallback_attempts` to `0`. Latency uses server elapsed time through response resolution, including history lookup and retries. |
| Public access | A new UUID starts each browser session. A history URL is accessible to anyone who knows the ID. Use non-sensitive demo prompts and share only intentional demo transcripts. |

The backend exposes `GET /health`, `POST /chat`, and `GET /sessions/{session_id}/messages`.

## Verification: how to see failover, cache hits and per-message token usage

Backend tests use a mocked provider so they run without keys. From `backend`:

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

From the repo root, run the real-provider check against a deployed backend with the simulation switch enabled:

```sh
python scripts/verify_live.py https://YOUR-BACKEND.up.railway.app
```

The script checks all three endpoints, live inference, cache reuse and latency, stored metadata, contextual recall, simulated 429 and 500 failover, all-key 503, a blank-message 422 and missing history. For a manual UI check, send a unique prompt, click **New chat**, then send the exact same prompt. The first reply displays `LIVE_INFERENCE`; the second displays `LLM_CACHE` with the same answer and token counts. Add a follow-up in the same session to see input tokens reflect the earlier messages. Test persistence by saving a successful session ID, restarting the backend, and fetching its history again.

### Suggested 2–3 minute demo

| Time | Screen | What to show |
| --- | --- | --- |
| 0:00–0:25 | Deployed React app | Send a new prompt and point out the model, source, latency, and token counts. |
| 0:25–0:50 | Same browser session | Ask a follow-up that refers to the first message and point out the new prompt token count. |
| 0:50–1:10 | New chat | Send the original prompt again and show the identical reply and `LLM_CACHE`. |
| 1:10–2:15 | Terminal | Run `scripts/verify_live.py` with the public backend URL. Point out `/health`, chronological history, `key_2` after `key_1=429` and `key_1=500`, and the 503 plus missing history when all keys fail. |
| 2:15–2:35 | Public repo | Briefly show the two Dockerfiles and the named SQLite volume. |

Record with your voice, upload as an unlisted video, and confirm the link opens in a private browser window. Submit the frontend, backend, repository and video URLs through the [assignment form](https://docs.google.com/forms/d/e/1FAIpQLSfaPS5_NliZuEjhYXppeafBrNM9u0hp-q-vT8vDuv9GU-0THA/viewform).

## Setup Used: your editor and AI tools

Developed with ChatGPT Work Mode and Codex. The intended local setup is VS Code, Python 3.12, Node.js 22 and Docker Compose v2.

## Bonus features (if any): what you added beyond the requirements and why

Session settings let users set or clear a system prompt for the next turn. The UI also includes prompt starters, reply copying and a way to restore a failed message for editing or retrying.
