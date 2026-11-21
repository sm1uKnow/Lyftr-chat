"""Run the public API acceptance checks without any provider credentials."""
import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def request(url, *, payload=None, simulation=None):
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    if simulation:
        headers["X-Simulate-Failure"] = simulation
    body = json.dumps(payload).encode() if payload is not None else None
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=100) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        try:
            return error.code, json.load(error)
        except ValueError as invalid_json:
            raise RuntimeError(f"{url} returned HTTP {error.code} without a JSON body") from invalid_json


def check(condition, description, data=None):
    if not condition:
        raise RuntimeError(f"Failed: {description}. Received {data!r}")


def verify(base_url):
    def chat(session_id, message, simulation=None):
        return request(f"{base_url}/chat", payload={"session_id": session_id, "message": message},
                       simulation=simulation)

    def history(session_id):
        return request(f"{base_url}/sessions/{session_id}/messages")

    suffix = uuid4().hex[:8]
    first, second, third, fourth, fifth = [f"verify-{suffix}-{i}" for i in range(1, 6)]
    message = f"My project code is LYFTR-{suffix}. Reply with just the project code."

    status, health = request(f"{base_url}/health")
    check(status == 200 and health.get("status") == "ok" and health.get("keys_configured", 0) >= 2
          and health.get("cache_enabled") is True, "GET /health", (status, health))
    print(f"GET /health                      {status} {health}")

    status, live = chat(first, message)
    check(status == 200 and live.get("session_id") == first and live.get("reply", "").strip(),
          "first POST /chat succeeds", (status, live))
    lm = live["metadata"]
    check(lm.get("resolution_source") == "LIVE_INFERENCE" and bool(lm.get("model"))
          and type(lm.get("latency_ms")) is int and type(lm.get("input_tokens")) is int
          and type(lm.get("output_tokens")) is int, "live inference metadata", lm)
    print(f"POST /chat                       {status} {lm}")

    status, cached = chat(second, message)
    check(status == 200, "cache request succeeds", (status, cached))
    cm = cached["metadata"]
    check(cm.get("resolution_source") == "LLM_CACHE" and cm.get("key_id", False) is None
          and cm.get("fallback_attempts") == 0 and cached["reply"] == live["reply"]
          and all(cm.get(key) == lm[key] for key in ("input_tokens", "output_tokens", "model"))
          and type(cm.get("latency_ms")) is int and cm["latency_ms"] < lm["latency_ms"],
          "exact cache reuse and lower latency", cm)
    print(f"POST /chat (same first message)  {status} {cm}")

    status, followup = chat(first, "What was my project code? Reply with just the code.")
    check(status == 200 and f"LYFTR-{suffix}".lower() in followup.get("reply", "").lower(),
          "follow-up recalls the earlier turn", (status, followup))
    print(f"POST /chat (follow-up)           {status} {followup['reply']}")

    status, stored = history(first)
    check(status == 200 and stored.get("session_id") == first
          and [item.get("role") for item in stored["messages"]] == ["user", "assistant"] * 2
          and stored["messages"][1]["metadata"] == lm, "saved history and metadata", (status, stored))
    print(f"GET /sessions/.../messages       {status} {len(stored['messages'])} messages in order")

    for session_id, error_code in ((third, 429), (fourth, 500)):
        status, failover = chat(session_id, message, f"key_1={error_code}")
        fm = failover.get("metadata", {})
        check(status == 200 and fm.get("key_id") == "key_2"
              and fm.get("fallback_attempts") == 1 and fm.get("resolution_source") == "LIVE_INFERENCE",
              f"failover for simulated {error_code}", (status, failover))
        print(f"POST /chat (key_1={error_code})          {status} {fm}")

    status, failure = chat(fifth, message, "*=429")
    check((status, failure) == (503, {"detail": "all providers unavailable"}),
          "all keys return 503", (status, failure))
    print(f"POST /chat (*=429)               {status} {failure}")

    status, invalid = chat(fifth, "   ")
    check(status == 422 and "detail" in invalid, "blank message returns 422", (status, invalid))
    print(f"POST /chat (blank)               {status}")

    status, missing = history(fifth)
    check((status, missing) == (404, {"detail": "session not found"}),
          "failed requests left no history", (status, missing))
    print(f"GET /sessions/.../messages       {status} {missing}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="Public backend base URL or http://localhost:8000")
    args = parser.parse_args()
    try:
        verify(args.base_url.rstrip("/"))
    except (RuntimeError, URLError, TimeoutError, KeyError, ValueError) as error:
        parser.exit(1, f"{error}\n")
