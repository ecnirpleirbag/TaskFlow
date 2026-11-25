"""
In-memory idempotency store.

Schema: { idempotency_key: { "body_hash": str, "response": any, "status": int } }

An in-memory dict is sufficient for a single-process dev server.
In production, swap this module for a Redis- or DB-backed implementation
without touching any other code – the public interface (check / store) stays
the same.
"""
import hashlib
import json

_store: dict = {}


def _hash_body(body: dict) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()
    ).hexdigest()


def check(key: str, body: dict):
    """
    Returns:
        None                              - key is new; proceed normally
        ('match',    response, status)   - same key AND same body; replay stored response
        ('conflict', None,     422)      - same key, different body
    """
    if key not in _store:
        return None
    entry = _store[key]
    if entry['body_hash'] == _hash_body(body):
        return ('match', entry['response'], entry['status'])
    return ('conflict', None, 422)


def store(key: str, body: dict, response, http_status: int) -> None:
    """Persist a *successful* response so the same key+body replays it."""
    _store[key] = {
        'body_hash': _hash_body(body),
        'response': response,
        'status': http_status,
    }
