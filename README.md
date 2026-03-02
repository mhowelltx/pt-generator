# PT Generator

AI-powered personal training session plan generator built on the Anthropic Claude API.
Produces NASM-informed, structured workout plans from a short description of the session goal —
available as both a **command-line tool** and a **browser-based web app**.

---

## Features

- Generates complete training sessions (warm-up → main → core/balance → cool-down) with sets, reps, tempo, rest, cues, regressions, and progressions
- Machine settings (seat, lever, pad) and load tracking per exercise
- **Progressive overload** — injects prior session loads so Claude can prescribe appropriate weight increases
- **Client profiles** — constraints, preferred equipment, and machine defaults saved per client
- **Export** to DOCX and Markdown
- **Web UI** with client history browser, download links, server-side validation, and print support
- **JSON API** at `/api/generate` for programmatic access

---

## Prerequisites

- Python 3.11 or later
- An [Anthropic API key](https://console.anthropic.com/)

---

## Installation

```bash
# 1. Clone or download the repository
git clone <repo-url>
cd pt-generator

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create a .env file with your API key
echo ANTHROPIC_API_KEY=your_key_here > .env
```

---

## Running the web app

```bash
# Option A — direct
python -m uvicorn app.web.server:app --host 127.0.0.1 --port 8000 --reload

# Option B — PowerShell script (Windows)
.\start.ps1

# Option C — shell script (macOS / Linux / Git Bash)
bash start.sh
```

Then open **http://localhost:8000** in your browser.

### Web UI pages

| URL | Description |
|-----|-------------|
| `GET /` | Generate a new session (form) |
| `GET /clients` | Browse all client profiles |
| `GET /clients/{slug}` | Client detail — profile + session history |
| `GET /download?file=<path>` | Download an exported file |
| `GET /docs` | Interactive API docs (Swagger UI) |
| `GET /health` | Health check — returns `{"status": "ok"}` |

### JSON API

```bash
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "client": "Jane Smith",
    "focus": "Lower body strength and balance.",
    "duration": 50,
    "export": "both"
  }'
```

`export` accepts `"none"` (default), `"markdown"`, `"docx"`, or `"both"`.
Pass `"constraints": ["shoulder pain"]` or `"equipment": ["dumbbells 5-15", "bands"]`
to override the saved client profile defaults.

---

## Running the CLI

```bash
python -m app.main \
  --client "Jane Smith" \
  --focus "Lower body strength and balance." \
  --duration 50 \
  --export both
```

All options:

| Flag | Default | Description |
|------|---------|-------------|
| `--client` | `Sample Client` | Client name (creates profile on first use) |
| `--focus` | full-body default | One-line session goal |
| `--duration` | `50` | Session length in minutes (1–180) |
| `--constraints` | profile default | Comma-separated injuries/limitations |
| `--equipment` | profile default | Comma-separated available equipment |
| `--session-number` | none | Override auto-numbering |
| `--date` | today | Session date (YYYY-MM-DD) |
| `--export` | `none` | `markdown`, `docx`, or `both` |

---

## Project layout

```
pt-generator/
├── app/
│   ├── config.py           # Model name, token limits, retry settings
│   ├── schema.py           # Pydantic models (TrainingSessionPlan, Block, Exercise, …)
│   ├── prompt_template.py  # System prompt + user prompt builder
│   ├── generation.py       # PlanGenerator — Anthropic tool-use call + retry logic
│   ├── service.py          # Shared pipeline (profile, history, generate, persist)
│   ├── storage.py          # JSON file I/O for client profiles and session history
│   ├── formatter.py        # Rich terminal output for the CLI
│   ├── export_markdown.py  # Markdown export
│   ├── export_docx.py      # DOCX export
│   ├── main.py             # Typer CLI entry point
│   └── web/
│       ├── server.py       # FastAPI app + middleware
│       ├── api.py          # POST /api/generate (JSON)
│       ├── routes.py       # Web UI routes (form, result, clients, download)
│       └── templates/      # Jinja2 HTML templates
├── data/
│   └── clients/<slug>/
│       ├── profile.json    # Constraints, equipment, machine defaults
│       └── history.json    # Session log with loads for progressive overload
├── outputs/
│   └── <client-slug>/      # Exported DOCX and Markdown files
├── requirements.txt
├── .env                    # ANTHROPIC_API_KEY (not committed)
├── start.ps1               # Windows start script
└── start.sh                # macOS / Linux / Git Bash start script
```

---

## Configuration

Edit `app/config.py` to change the model or generation parameters:

```python
MODEL = "claude-sonnet-4-5"   # Anthropic model ID
MAX_TOKENS = 8192              # Max output tokens
TEMPERATURE_GENERATE = 0.4    # Generation temperature (0–1)
MAX_RETRIES = 3                # Retry attempts on transient failures
RETRY_WAIT_SECONDS = 2        # Wait between retries
DEFAULT_DURATION = 50          # Default session duration in minutes
```

---

## Data storage

Client data is stored as plain JSON under `data/clients/<slug>/`:

- **`profile.json`** — client name, constraints, preferred equipment, machine settings, trainer notes
- **`history.json`** — list of past sessions with date, focus, and per-exercise loads

Both directories are git-ignored. Back them up separately if needed.
