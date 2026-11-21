import concurrent.futures
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import ProviderKey, Settings
from app.gateway import litellm
from app.main import create_app

MODEL = "gemini/gemini-3.1-flash-lite"
OTHER_MODEL = "groq/llama-3.1-8b-instant"


def answer(**request):
    messages = request["messages"]
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=f"Reply: {messages[-1]['content']}"
        ))],
        usage=SimpleNamespace(prompt_tokens=sum(len(item["content"].split()) for item in messages),
                              completion_tokens=4),
    )


class ContractTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(temp) / "test.db"
        self.settings = self.make_settings()
        self.client = self.enterContext(TestClient(create_app(self.settings)))
        self.provider = self.enterContext(patch("app.gateway.litellm.acompletion", side_effect=answer))

    def make_settings(self, enabled=True, second_model=MODEL):
        return Settings(
            database_path=self.path,
            allowed_origins=("https://chat.example",),
            enable_failure_simulation=enabled,
            providers=(ProviderKey("key_1", MODEL, "mock-primary"),
                       ProviderKey("key_2", second_model, "mock-secondary")),
            timeout_seconds=1,
        )

    def chat(self, session="first", message="Explain caching.", failure=None, **extra):
        headers = {"X-Simulate-Failure": failure} if failure else {}
        response = self.client.post(
            "/chat", json={"session_id": session, "message": message, **extra}, headers=headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_health_and_cors(self):
        self.assertEqual(self.client.get("/health").json(), {
            "status": "ok", "keys_configured": 2, "cache_enabled": True
        })
        response = self.client.options("/chat", headers={
            "Origin": "https://chat.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "https://chat.example")

    def test_invalid_requests_do_not_write_history(self):
        cases = [
            {"session_id": "bad"}, {"message": "hi"}, {"session_id": "", "message": "hi"},
            {"session_id": "bad", "message": "   "}, {"session_id": "bad", "message": 1},
            {"session_id": "bad", "message": "a" * 4001}, {"session_id": "x" * 129, "message": "hi"},
        ]
        for payload in cases:
            with self.subTest(payload=payload.keys()):
                res = self.client.post("/chat", json=payload)
                self.assertEqual(res.status_code, 422)
                self.assertIn("detail", res.json())
        res = self.client.post("/chat", content="{", headers={"Content-Type": "application/json"})
        self.assertEqual(res.status_code, 422)
        res = self.client.get("/sessions/bad/messages")
        self.assertEqual((res.status_code, res.json()), (404, {"detail": "session not found"}))
        self.provider.assert_not_awaited()

    def test_cache_hit_matches_text_and_usage(self):
        live = self.chat(message="  " + "Explain caching." + "  ")
        cached = self.chat(session="second")
        self.assertEqual(cached["reply"], live["reply"])
        self.assertEqual(live["metadata"]["resolution_source"], "LIVE_INFERENCE")
        self.assertEqual(cached["metadata"], {
            **live["metadata"], "resolution_source": "LLM_CACHE", "key_id": None,
            "fallback_attempts": 0, "latency_ms": cached["metadata"]["latency_ms"],
        })
        self.assertIs(type(cached["metadata"]["latency_ms"]), int)
        self.provider.assert_awaited_once()
        self.assertEqual(self.chat(session="third", message="Explain tokens.")["metadata"]["resolution_source"],
                         "LIVE_INFERENCE")

    def test_context_prompt_and_session_isolation(self):
        one = self.chat(message="My name is Sagar.", system_prompt="Answer in one sentence.")
        two = self.chat(message="What is my name?")
        request = self.provider.await_args.kwargs
        self.assertEqual(request["messages"], [
            {"role": "system", "content": "Answer in one sentence."},
            {"role": "user", "content": "My name is Sagar."},
            {"role": "assistant", "content": one["reply"]},
            {"role": "user", "content": "What is my name?"},
        ])
        self.assertGreater(two["metadata"]["input_tokens"], one["metadata"]["input_tokens"])
        independent = self.chat(session="other", message="What is my name?", system_prompt="Answer in one sentence.")
        self.assertEqual(independent["metadata"]["input_tokens"], one["metadata"]["input_tokens"])
        self.assertEqual(two["metadata"]["output_tokens"], 4)
        history = self.client.get("/sessions/first/messages").json()
        self.assertEqual(history["session_id"], "first")
        self.assertEqual([item["role"] for item in history["messages"]], ["user", "assistant"] * 2)
        self.assertEqual(history["messages"][-1]["metadata"], two["metadata"])

    def test_cache_distinguishes_prompt_and_history(self):
        self.chat(system_prompt="Respond in Hindi.")
        metadata = self.chat(session="other", system_prompt="Respond in English.")["metadata"]
        self.assertEqual(metadata["resolution_source"], "LIVE_INFERENCE")
        self.assertEqual(self.chat(system_prompt="Respond in Hindi.")["metadata"]["resolution_source"],
                         "LIVE_INFERENCE")
        self.provider.assert_awaited()

    def test_simulation_bypasses_warm_cache_and_does_not_affect_following_request(self):
        self.chat()
        for status in (429, 500, 502, 503):
            result = self.chat(session=f"fail-{status}", failure=f"key_1={status}")
            self.assertEqual(result["metadata"]["key_id"], "key_2")
            self.assertEqual(result["metadata"]["fallback_attempts"], 1)
            self.assertEqual(result["metadata"]["resolution_source"], "LIVE_INFERENCE")
            self.assertEqual(self.provider.await_args.kwargs["api_key"], "mock-secondary")
        self.assertEqual(self.chat(session="new", message="New prompt.")["metadata"]["key_id"], "key_1")

    def test_every_key_failed_is_503_and_does_not_write_history(self):
        self.chat()
        for header in ("*=429", "key_1=429,key_2=500"):
            blocked = self.client.post("/chat", json={
                "session_id": "first", "message": "Rejected", "system_prompt": "Never saved",
            }, headers={"X-Simulate-Failure": header})
            self.assertEqual((blocked.status_code, blocked.json()), (503, {"detail": "all providers unavailable"}))
        self.assertEqual(len(self.client.get("/sessions/first/messages").json()["messages"]), 2)
        self.provider.assert_awaited_once()

    def test_provider_errors_automatically_try_next_key(self):
        for exception in (
            litellm.RateLimitError("quota exhausted", model=MODEL, llm_provider="gemini"),
            litellm.ServiceUnavailableError("provider outage", model=MODEL, llm_provider="gemini"),
        ):
            with self.subTest(error=type(exception).__name__):
                self.provider.side_effect = [exception, answer(messages=[{"content": "hello"}])]
                metadata = self.chat(session=type(exception).__name__, message=type(exception).__name__)["metadata"]
                self.assertEqual(metadata["key_id"], "key_2")
                self.assertEqual(metadata["fallback_attempts"], 1)
        self.provider.side_effect = RuntimeError("credential contents must stay private")
        response = self.client.post("/chat", json={"session_id": "no-turn", "message": "hello"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("credential", response.text)
        self.assertEqual(self.client.get("/sessions/no-turn/messages").status_code, 404)

    def test_cache_reuses_fallback_model(self):
        self.provider.side_effect = [
            RuntimeError("primary rejected"),
            answer(messages=[{"content": "fallback completed"}]),
        ]
        with TestClient(create_app(self.make_settings(second_model=OTHER_MODEL))) as client:
            one = client.post("/chat", json={"session_id": "a", "message": "same"}).json()
            two = client.post("/chat", json={"session_id": "b", "message": "same"}).json()
        self.assertEqual(one["metadata"]["model"], OTHER_MODEL)
        self.assertEqual(two["reply"], one["reply"])
        self.assertEqual(two["metadata"]["resolution_source"], "LLM_CACHE")
        self.assertEqual(two["metadata"]["model"], OTHER_MODEL)

    def test_history_survives_restart_and_simulation_can_be_disabled(self):
        self.chat(session="saved")
        with TestClient(create_app(self.make_settings(enabled=False))) as restarted:
            self.assertEqual(len(restarted.get("/sessions/saved/messages").json()["messages"]), 2)
            response = restarted.post("/chat", json={"session_id": "new", "message": "abc"},
                                      headers={"X-Simulate-Failure": "*=429"})
            self.assertEqual(response.json()["metadata"]["key_id"], "key_1")

    def test_concurrent_turns_share_serialized_history(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = list(pool.map(lambda message: self.chat(message=message), ["one", "two"]))
        self.assertEqual(len(results), 2)
        self.assertEqual([len(call.kwargs["messages"]) for call in self.provider.await_args_list], [2, 4])
        self.assertEqual(len(self.client.get("/sessions/first/messages").json()["messages"]), 4)

    def test_key_pool_environment_mapping(self):
        with patch.dict(os.environ, {"LLM_API_KEYS": "a,b", "LLM_MODELS": f"{MODEL},{OTHER_MODEL}"}):
            settings = Settings.from_env()
        self.assertEqual([(key.id, key.model) for key in settings.providers],
                         [("key_1", MODEL), ("key_2", OTHER_MODEL)])
        self.assertNotIn("secret", repr(settings.providers))


if __name__ == "__main__":
    unittest.main()
