from unittest.mock import MagicMock, call, patch

from tools import make_get_activity_data, make_submit_findings


def test_make_get_activity_data_returns_rows():
    csv_content = "event_time,user_host,command_type,argument\n2026-04-14,root@localhost,Connect,root\n"

    with patch("tools.s3") as mock_s3:
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: csv_content.encode("utf-8"))
        }
        tool_fn = make_get_activity_data("my-bucket", "raw/2026-04-14/user-activity.csv")
        rows = tool_fn()

    assert len(rows) == 1
    assert rows[0]["user_host"] == "root@localhost"
    mock_s3.get_object.assert_called_once_with(Bucket="my-bucket", Key="raw/2026-04-14/user-activity.csv")


def test_make_get_activity_data_closes_over_key():
    """Different keys produce different tool instances that each read their own key."""
    csv_a = "event_time,user_host,command_type,argument\n2026-04-07,userA@host,Connect,\n"
    csv_b = "event_time,user_host,command_type,argument\n2026-04-14,userB@host,Connect,\n"

    with patch("tools.s3") as mock_s3:
        mock_s3.get_object.side_effect = [
            {"Body": MagicMock(read=lambda: csv_a.encode("utf-8"))},
            {"Body": MagicMock(read=lambda: csv_b.encode("utf-8"))},
        ]
        tool_a = make_get_activity_data("bucket", "raw/2026-04-07/user-activity.csv")
        tool_b = make_get_activity_data("bucket", "raw/2026-04-14/user-activity.csv")
        rows_a = tool_a()
        rows_b = tool_b()

    assert rows_a[0]["user_host"] == "userA@host"
    assert rows_b[0]["user_host"] == "userB@host"
    calls = mock_s3.get_object.call_args_list
    assert calls[0] == call(Bucket="bucket", Key="raw/2026-04-07/user-activity.csv")
    assert calls[1] == call(Bucket="bucket", Key="raw/2026-04-14/user-activity.csv")


# ── submit_findings: structured output replaces regex JSON parsing ───────────

def _underlying(tool_fn):
    """Strands @tool wraps the callable. Unwrap to the original function for direct tests."""
    # Strands exposes the decorated function under several attribute names across
    # versions; probe the common ones and fall back to calling the wrapper's invoke.
    for attr in ("func", "_func", "fn", "_fn", "__wrapped__"):
        inner = getattr(tool_fn, attr, None)
        if callable(inner):
            return inner
    return tool_fn


def test_submit_findings_captures_payload_into_sink():
    tool_fn, sink = make_submit_findings()
    findings = [
        {"ism_control_id": "ISM-1586", "control_description": "Privileged Access Logging",
         "status": "FAIL", "finding": "Root login outside hours", "evidence": "..."},
    ]
    assert sink["findings"] is None

    _underlying(tool_fn)(findings)

    assert sink["findings"] == findings


def test_submit_findings_factories_are_isolated():
    """Each factory call returns an independent sink — Lambda container reuse must not leak state."""
    tool_a, sink_a = make_submit_findings()
    tool_b, sink_b = make_submit_findings()

    _underlying(tool_a)([{"ism_control_id": "ISM-0109"}])

    assert sink_a["findings"] == [{"ism_control_id": "ISM-0109"}]
    assert sink_b["findings"] is None
