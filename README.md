# TaskFlow API

A Django REST Framework backend that lets you create tasks in plain English and get structured, validated task objects back.

> **Example:** `POST /api/tasks/from-text/` with `{"text": "Remind me to pay rent on the 1st of every month"}` returns a fully structured task with `due_date`, `recurrence`, `priority`, etc.

---

## Table of Contents
1. [Setup](#setup)
2. [Running the server](#running-the-server)
3. [Endpoints](#endpoints)
4. [Error shape](#error-shape)
5. [AI / NL parser](#ai--nl-parser)
6. [Idempotency](#idempotency)
7. [Filtering, search & pagination](#filtering-search--pagination)
8. [Testing notes](#testing-notes)

---

## Setup

### Prerequisites
- Python 3.11+
- Docker (for Postgres)

### 1 — Start Postgres
```bash
docker compose up -d
```
This starts Postgres on port 5433 (mapped to container port 5432) with database `taskflow`, user `postgres`, password `postgres`.

### 2 — Create and activate a virtual environment
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate
```

### 3 — Install dependencies
```bash
pip install -r requirements.txt
```

### 4 — Configure environment
```bash
cp .env.example .env
# Edit .env if your Postgres credentials differ
```

### 5 — Run migrations
```bash
python manage.py migrate
```

### 6 — Start the dev server
```bash
python manage.py runserver
```

API is now available at `http://localhost:8000/api/`.

### Optional: LLM key
If you replace `parse_task_text` with an LLM call, set the key in `.env`:
```
LLM_API_KEY=sk-...
```
The app reads it via `os.environ.get("LLM_API_KEY")`. If the key is missing and your LLM wrapper requires it, return `503` in the error shape instead of crashing. **Never commit `.env` or the key itself.**

---

## Endpoints

### Tasks — CRUD

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/tasks/` | List tasks (filterable, searchable, paginated) |
| `POST` | `/api/tasks/` | Create a task |
| `GET` | `/api/tasks/{id}/` | Retrieve a task |
| `PUT` | `/api/tasks/{id}/` | Full update |
| `PATCH` | `/api/tasks/{id}/` | Partial update |
| `DELETE` | `/api/tasks/{id}/` | Delete (cascades to subtasks) |

#### Create task — example
```http
POST /api/tasks/
Content-Type: application/json

{
  "title": "Buy groceries",
  "description": "Milk, eggs, bread",
  "priority": "high",
  "due_date": "2026-11-25"
}
```
**201 Created**
```json
{
  "id": 1,
  "title": "Buy groceries",
  "description": "Milk, eggs, bread",
  "completed": false,
  "due_date": "2026-11-25",
  "priority": "high",
  "recurrence": null,
  "parent": null,
  "is_subtask": false,
  "subtask_count": 0,
  "created_at": "2026-11-21T10:00:00Z",
  "updated_at": "2026-11-21T10:00:00Z"
}
```

---

### Natural-language creation

#### `POST /api/tasks/from-text/`

```http
POST /api/tasks/from-text/
Content-Type: application/json

{"text": "Remind me to pay rent on the 1st of every month"}
```
**201 Created**
```json
{
  "id": 2,
  "title": "pay rent",
  "description": "Remind me to pay rent on the 1st of every month",
  "due_date": "2026-12-01",
  "recurrence": "monthly",
  "priority": "medium",
  ...
}
```

Error cases:
- Empty text → `400 VALIDATION_ERROR`
- Parser throws → `502 PARSER_ERROR`
- Parser returns invalid data (e.g. empty title) → `502 PARSER_OUTPUT_INVALID`

---

### Subtasks

#### `POST /api/tasks/{id}/subtasks/` — create a subtask
```http
POST /api/tasks/1/subtasks/
Content-Type: application/json

{"title": "Pick up milk", "description": "Full-fat 2L", "priority": "low"}
```
**201 Created** — returns the subtask object (with `is_subtask: true`).

#### `GET /api/tasks/{id}/subtasks/` — list subtasks
**200 OK** — array of subtask objects.

Constraints:
- A subtask **cannot** have its own subtasks → `400 FORBIDDEN`
- Deleting a parent task cascades and deletes all its subtasks

---

### Auto-breakdown

#### `POST /api/tasks/{id}/breakdown/`
Splits a task into subtasks automatically.

```http
POST /api/tasks/1/breakdown/
```
**201 Created**
```json
[
  {"id": 3, "title": "Plan: Buy groceries", ...},
  {"id": 4, "title": "Execute: Buy groceries", ...},
  {"id": 5, "title": "Review: Buy groceries", ...}
]
```
If the task description contains "and" or commas, those become the split points.

---

## Error shape

**Every** `4xx` and `5xx` response from this API uses this exact shape:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Validation failed",
    "details": [
      {"field": "title", "issue": "must not be empty"},
      {"field": "due_date", "issue": "must be a real YYYY-MM-DD date"}
    ]
  }
}
```

| `code` | HTTP status | Meaning |
|--------|-------------|---------|
| `VALIDATION_ERROR` | 400 | Input failed validation |
| `NOT_FOUND` | 404 | Resource not found |
| `FORBIDDEN` | 400 | Operation not allowed (e.g. subtask of subtask) |
| `IDEMPOTENCY_CONFLICT` | 422 | Same key, different body |
| `PARSER_ERROR` | 502 | NL parser threw an exception |
| `PARSER_OUTPUT_INVALID` | 502 | Parser returned invalid data |
| `BREAKDOWN_ERROR` | 502 | Breakdown engine failed |

---

## AI / NL parser

The parser lives in `tasks/ai.py` and exposes two functions:

```python
def parse_task_text(text: str, today: datetime.date) -> dict:
    """Returns {title, description, due_date, recurrence}"""

def break_down_task(task: Task) -> list[dict]:
    """Returns [{title, description, priority, completed}, ...]"""
```

**Current implementation:** rule-based, using `re` and `datetime`. Handles:
- `today`, `tomorrow`, `next week`, `next month`
- Ordinal dates: `on the 1st`, `15th`
- Recurrence: `every day/week/month/year`, `daily`, `weekly`, `monthly`, `yearly`
- Filler phrase stripping: `"Remind me to …"`, `"I need to …"`, etc.

**To swap in an LLM:** replace only the body of `parse_task_text`. Keep the same signature and return shape. Set `LLM_API_KEY` in `.env`.

---

## Idempotency

Supported on:
- `POST /api/tasks/`
- `POST /api/tasks/from-text/`
- `POST /api/tasks/{id}/breakdown/`

**How to use:** add an `Idempotency-Key: <uuid>` header.

| Scenario | Result |
|----------|--------|
| First request with key | Processes normally, stores response |
| Same key + same body | Returns cached response, no DB write |
| Same key + different body | `422 IDEMPOTENCY_CONFLICT` |

Only successful responses are stored. A client can fix validation errors and retry with the same key.

---

## Filtering, search & pagination

All usable together in one request:

```
GET /api/tasks/?priority=high&completed=false&search=rent&ordering=-due_date&page=2
```

| Parameter | Type | Example |
|-----------|------|---------|
| `priority` | `low` / `medium` / `high` | `?priority=high` |
| `completed` | boolean | `?completed=false` |
| `due_date` | date | `?due_date=2026-12-01` |
| `due_date_after` | date | `?due_date_after=2026-11-01` |
| `due_date_before` | date | `?due_date_before=2026-12-31` |
| `has_parent` | boolean | `?has_parent=false` (top-level only) |
| `search` | string | `?search=grocery` (title + description) |
| `ordering` | field name | `?ordering=-due_date,priority` |
| `page` | int | `?page=2` |
| `page_size` | int | `?page_size=20` (default 10) |

---

## Testing notes

The full Postman test collection is saved in [`taskflow_postman_collection.json`](./taskflow_postman_collection.json), and verification screenshots (Postman responses + DB admin state) are in [`ResultScreenShots/`](./ResultScreenShots/).

### What the AI-generated checklist caught
- Happy-path CRUD, basic validation, and filter combinations.

### What I added / hardened manually
- **Impossible date:** `due_date: "2026-02-30"` → 400 (DRF rejects it at the field level).
- **Subtask of a subtask:** POST to `/subtasks/` on a subtask → 400 FORBIDDEN.
- **Idempotency conflict:** same key, different body → 422 (`IDEMPOTENCY_CONFLICT`).
- **Idempotency replay:** same key, same body → replays cached 201 response without creating a duplicate row.
- **Parser bad output:** temporarily return `{"title": "", ...}` from `parse_task_text` → 502 `PARSER_OUTPUT_INVALID`, no DB row created.
- **Cascade delete:** delete a parent task and verify its subtasks are removed in the DB client / Django Admin.
- **Breakdown of a subtask** → 400 FORBIDDEN.
- **Unknown task ID** on all retrieve/update/delete/subtask/breakdown endpoints → 404 `NOT_FOUND`.
