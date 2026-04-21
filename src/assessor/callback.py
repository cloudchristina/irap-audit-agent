import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def trace_callback(**kwargs) -> None:
    event_type = kwargs.get("event_type", "unknown")
    timestamp = datetime.now(timezone.utc).isoformat()

    if event_type == "tool_use":
        logger.info(json.dumps({
            "event": "tool_use",
            "tool": kwargs.get("tool_name"),
            "input": kwargs.get("input"),
            "rows_returned": len(kwargs.get("output", [])) if isinstance(kwargs.get("output"), list) else None,
            "timestamp": timestamp,
        }, default=str))

    elif event_type == "model_response":
        logger.info(json.dumps({
            "event": "model_response",
            "step": kwargs.get("step"),
            "text": kwargs.get("text", "")[:500],
            "timestamp": timestamp,
        }, default=str))

    elif event_type == "final_response":
        text = kwargs.get("text", "")
        logger.info(json.dumps({
            "event": "final_response",
            "response_length": len(text),
            "timestamp": timestamp,
        }, default=str))

    elif event_type == "error":
        logger.error(json.dumps({
            "event": "error",
            "error_type": kwargs.get("error_type"),
            "message": kwargs.get("message"),
            "timestamp": timestamp,
        }, default=str))

    else:
        logger.debug(json.dumps({
            "event": "unhandled",
            "event_type": event_type,
            "timestamp": timestamp,
        }, default=str))
