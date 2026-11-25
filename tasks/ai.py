"""
Natural-language task parser and automatic breakdown engine.

Public interface
----------------
parse_task_text(text: str, today: datetime.date) -> dict
    Returns a dict with keys:
        title       : str
        description : str
        due_date    : str | None   (YYYY-MM-DD)
        recurrence  : str | None   ('daily' | 'weekly' | 'monthly' | 'yearly')
    Raises ValueError on completely invalid input.

    -- LLM swap point --
    Replace the body of parse_task_text with an LLM call without touching
    anything else. Contract: same signature, same return shape.

    Example:
        from django.conf import settings
        import requests

        def parse_task_text(text: str, today: datetime.date) -> dict:
            if not settings.LLM_API_KEY:
                raise RuntimeError("LLM_API_KEY not configured")
            resp = requests.post(
                "https://api.example.com/chat",
                headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                json={"prompt": build_prompt(text, today)},
            )
            resp.raise_for_status()
            return resp.json()   # must match {title, description, due_date, recurrence}

break_down_task(task: Task) -> list[dict]
    Returns a list of dicts, each suitable for TaskSerializer.
    Raises ValueError if the task is itself a subtask.
"""
import datetime
import re


# ---------------------------------------------------------------------------
# parse_task_text – rule-based version
# ---------------------------------------------------------------------------

def parse_task_text(text: str, today: datetime.date) -> dict:
    """
    Rule-based NL parser. Handles phrases like:
        "tomorrow", "today", "next week", "next month",
        "on the 1st", "15th", "every month", "weekly", …

    Args:
        text  : plain-English task description
        today : today's date (injected for testability)

    Returns:
        dict with title, description, due_date (YYYY-MM-DD|None), recurrence (str|None)

    Raises:
        ValueError: if text is blank
    """
    if not text or not text.strip():
        raise ValueError('text must not be empty')

    text = text.strip()
    lower = text.lower()

    # --- Recurrence ---
    recurrence = None
    if re.search(r'\bevery\s+(day|daily)\b', lower):
        recurrence = 'daily'
    elif re.search(r'\bevery\s+week\b|\bweekly\b', lower):
        recurrence = 'weekly'
    elif re.search(r'\bevery\s+month\b|\bmonthly\b', lower):
        recurrence = 'monthly'
    elif re.search(r'\bevery\s+year\b|\bannually\b|\byearly\b', lower):
        recurrence = 'yearly'

    # --- Due date ---
    due_date = None

    if re.search(r'\btoday\b', lower):
        due_date = today.isoformat()
    elif re.search(r'\btomorrow\b', lower):
        due_date = (today + datetime.timedelta(days=1)).isoformat()
    elif re.search(r'\bnext\s+week\b', lower):
        due_date = (today + datetime.timedelta(weeks=1)).isoformat()
    elif re.search(r'\bnext\s+month\b', lower):
        if today.month == 12:
            due_date = today.replace(year=today.year + 1, month=1, day=1).isoformat()
        else:
            due_date = today.replace(month=today.month + 1, day=1).isoformat()
    else:
        # "on the 1st", "15th", etc.
        ordinal = re.search(r'\b(?:on\s+the\s+)?(\d{1,2})(?:st|nd|rd|th)\b', lower)
        if ordinal:
            day = int(ordinal.group(1))
            try:
                if today.day < day:
                    due_date = today.replace(day=day).isoformat()
                else:
                    nm = today.month % 12 + 1
                    ny = today.year + (1 if today.month == 12 else 0)
                    due_date = today.replace(year=ny, month=nm, day=day).isoformat()
            except ValueError:
                due_date = None

        # Explicit YYYY-MM-DD in text
        explicit = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', text)
        if explicit and not due_date:
            try:
                due_date = datetime.date.fromisoformat(explicit.group(1)).isoformat()
            except ValueError:
                pass

    # --- Title extraction (strip filler phrases) ---
    title = text
    for phrase in [
        r'^remind\s+me\s+to\s+', r'^please\s+', r'^can\s+you\s+',
        r'^i\s+need\s+to\s+', r"^don\'?t\s+forget\s+to\s+",
        r'^make\s+sure\s+to\s+', r'^remember\s+to\s+',
    ]:
        title = re.sub(phrase, '', title, flags=re.IGNORECASE).strip()

    # Strip date/recurrence tokens from title
    title = re.sub(
        r'\b(today|tomorrow|next\s+week|next\s+month|every\s+day|every\s+week|'
        r'every\s+month|every\s+year|daily|weekly|monthly|annually|yearly|'
        r'on\s+the\s+\d{1,2}(?:st|nd|rd|th)|\d{4}-\d{2}-\d{2})\b',
        '', title, flags=re.IGNORECASE,
    ).strip()
    # Strip dangling prepositions left after date removal (e.g. "by", "on", "of", "at")
    title = re.sub(r'\b(by|on|of|at|the)\s*$', '', title, flags=re.IGNORECASE).strip()
    title = re.sub(r'\s{2,}', ' ', title).strip(' .,')

    if not title:
        title = text.strip()

    return {
        'title': title,
        'description': text,
        'due_date': due_date,
        'recurrence': recurrence,
    }


# ---------------------------------------------------------------------------
# break_down_task – rule-based version
# ---------------------------------------------------------------------------

def break_down_task(task) -> list:
    """
    Splits a task into subtasks by splitting on 'and' or commas.
    Falls back to Plan / Execute / Review template when no split points exist.

    Args:
        task : Task model instance

    Returns:
        list of dicts, each with title, description, priority, completed

    Raises:
        ValueError : if task is itself a subtask
    """
    if task.is_subtask:
        raise ValueError('A subtask cannot be broken down further.')

    source = (task.description or task.title).strip()
    parts = re.split(r'\band\b|,', source, flags=re.IGNORECASE)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) < 2:
        parts = [
            f'Plan: {task.title}',
            f'Execute: {task.title}',
            f'Review: {task.title}',
        ]

    return [
        {
            'title': part,
            'description': f'Subtask of: {task.title}',
            'priority': task.priority,
            'completed': False,
        }
        for part in parts
    ]
