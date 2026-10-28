"""Independent, deliberately narrow rules for synthetic same-window evidence."""

from typing import Any


def find_conflicts(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    health, logs = {}, {}
    for item in observations:
        if item.get("role") != "tool" or not item.get("success"):
            continue
        output = item.get("content")
        if not isinstance(output, dict):
            continue
        service = output.get("service")
        if item.get("name") == "get_service_health":
            health[service] = item
        if item.get("name") == "search_error_logs":
            logs[service] = item
    conflicts = []
    for service in health.keys() & logs.keys():
        h, log = health[service], logs[service]
        entries = log["content"].get("entries", [])
        # This rule is specific to the benchmark's synchronized synthetic snapshot.
        if h["content"].get("status") == "healthy" and any(
            e.get("severity") == "error" and e.get("count", 0) >= 100 for e in entries
        ):
            conflicts.append({"code": "health_log_conflict", "service": service,
                              "evidence_refs": [h["call_id"], log["call_id"]]})
    return conflicts
