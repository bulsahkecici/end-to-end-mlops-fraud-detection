from __future__ import annotations

import json
import logging

from src.logging_config import JsonFormatter


def test_json_formatter_includes_deployment_identity_fields():
    record = logging.LogRecord(
        name="src.api",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="model_loaded",
        args=(),
        exc_info=None,
    )
    record.deployment_id = "deployment-20261001"
    record.action = "rollback"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["deployment_id"] == "deployment-20261001"
    assert payload["action"] == "rollback"
