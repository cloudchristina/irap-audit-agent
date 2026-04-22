import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from handler import (
    build_s3_key,
    get_credentials,
    query_general_log,
    resolve_window,
    rows_to_csv,
)


def test_rows_to_csv_empty():
    result = rows_to_csv([])
    assert result == "event_time,user_host,command_type,argument\n"


def test_rows_to_csv_with_data():
    rows = [{"event_time": "2026-04-14 02:00:00", "user_host": "root@localhost",
             "command_type": "Connect", "argument": "root@localhost on  using TCP/IP"}]
    result = rows_to_csv(rows)
    assert "root@localhost" in result
    assert "Connect" in result
    lines = result.strip().split("\n")
    assert len(lines) == 2  # header + 1 data row


def test_rows_to_csv_quotes_commas():
    rows = [{"event_time": "2026-04-14", "user_host": "user@host",
             "command_type": "Query", "argument": "SELECT 1, 2"}]
    result = rows_to_csv(rows)
    assert '"SELECT 1, 2"' in result


def test_get_credentials_parses_json():
    with patch("handler.secretsmanager") as mock_sm:
        mock_sm.get_secret_value.return_value = {
            "SecretString": json.dumps({"username": "u", "password": "p"})
        }
        creds = get_credentials("arn:aws:secretsmanager:ap-southeast-2:123456789012:secret:test")
        assert creds == {"username": "u", "password": "p"}


# ── window resolution (idempotency-critical) ─────────────────────────────────

def test_resolve_window_from_eventbridge_input():
    """EventBridge passes {window_end, window_days}; we compute a closed-open interval."""
    event = {"window_end": "2026-04-19T14:00:00Z", "window_days": 7}
    start, end = resolve_window(event)
    assert end == datetime(2026, 4, 19, 14, 0, 0, tzinfo=timezone.utc)
    assert start == datetime(2026, 4, 12, 14, 0, 0, tzinfo=timezone.utc)


def test_resolve_window_accepts_offset_form():
    event = {"window_end": "2026-04-19T14:00:00+00:00", "window_days": 1}
    start, end = resolve_window(event)
    assert (end - start).days == 1


def test_resolve_window_falls_back_to_now_when_absent():
    """Manual invocation without window_end uses NOW() — the only non-idempotent path."""
    start, end = resolve_window({})
    assert end.tzinfo is not None
    assert (end - start).days == 7


def test_resolve_window_is_deterministic_under_retry():
    """Same event → same window: this is what guarantees the S3 key is idempotent."""
    event = {"window_end": "2026-04-19T14:00:00Z", "window_days": 7}
    assert resolve_window(event) == resolve_window(event)


def test_build_s3_key_uses_window_end_date():
    end = datetime(2026, 4, 19, 14, 0, 0, tzinfo=timezone.utc)
    assert build_s3_key(end) == "raw/2026-04-19/user-activity.csv"


# ── SQL parameterisation ─────────────────────────────────────────────────────

def test_query_general_log_uses_parameterised_bounds():
    cursor = MagicMock()
    cursor.description = [("event_time",), ("user_host",), ("command_type",), ("argument",)]
    cursor.fetchall.return_value = []
    start = datetime(2026, 4, 12, tzinfo=timezone.utc)
    end = datetime(2026, 4, 19, tzinfo=timezone.utc)

    query_general_log(cursor, start, end)

    # Bounds must be passed as parameters (not string-interpolated) — defence against
    # accidental injection and required for the DB-side query planner.
    args, _ = cursor.execute.call_args
    assert args[1] == (start, end)
    assert "%s" in args[0]
