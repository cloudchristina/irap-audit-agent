import io
import json
from unittest.mock import MagicMock, patch

import pytest

from handler import rows_to_csv, query_general_log


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
        from handler import get_credentials
        creds = get_credentials("arn:aws:secretsmanager:ap-southeast-2:123456789012:secret:test")
        assert creds == {"username": "u", "password": "p"}
