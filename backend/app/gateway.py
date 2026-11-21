import asyncio
import hashlib
import json
import logging
from dataclasses import asdict, dataclass

import litellm
from litellm.caching import Cache, LiteLLMCacheType

from .config import Settings

logger = logging.getLogger(__name__)
SIMULATED_STATUSES = {"429", "500", "502", "503"}


class ProvidersUnavailable(Exception):
    pass


def simulated_failures(header: str | None, enabled: bool) -> dict[str, int]:
    if not enabled or not header:
        return {}
    failures = {}
    for pair in header.split(","):
        key_id, _, status = (part.strip() for part in pair.partition("="))
        if status not in SIMULATED_STATUSES:
            raise ValueError("X-Simulate-Failure statuses must be 429, 500, 502, or 503")
        failures[key_id] = int(status)
    return failures


@dataclass(frozen=True)
class Completion:
    reply: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Resolution:
    completion: Completion
    model: str
    key_id: str | None
    fallback_attempts: int


class Gateway:
    def __init__(self, settings: Settings):
        self.keys = settings.providers
        self.timeout = settings.timeout_seconds
        self.cache = Cache(type=LiteLLMCacheType.LOCAL, ttl=3600)

    @staticmethod
    def cache_key(model: str, messages: list[dict]) -> str:
        payload = json.dumps([model, messages], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def complete(self, messages: list[dict], failures: dict[str, int]) -> Resolution:
        if not failures:
            for model in dict.fromkeys(key.model for key in self.keys):
                cached = self.cache.get_cache(cache_key=self.cache_key(model, messages))
                if cached is not None:
                    return Resolution(Completion(**cached), model, None, 0)

        attempts = 0
        for key in self.keys:
            simulated_status = failures.get(key.id, failures.get("*"))
            if simulated_status is not None:
                logger.info("Simulated provider failure for %s (%s)", key.id, simulated_status)
                attempts += 1
                continue

            try:
                result = await asyncio.wait_for(
                    litellm.acompletion(
                        model=key.model,
                        messages=messages,
                        api_key=key.secret,
                        timeout=self.timeout,
                        num_retries=0,
                        caching=False,
                        max_tokens=2048,
                    ),
                    timeout=self.timeout,
                )
                reply = result.choices[0].message.content
                usage = result.usage
                if not isinstance(reply, str) or not reply.strip() or usage is None:
                    raise ValueError("Incomplete provider response")
                tokens = (usage.prompt_tokens, usage.completion_tokens)
                if any(type(count) is not int or count < 0 for count in tokens):
                    raise ValueError("Provider omitted valid token counts")
                completion = Completion(reply, *tokens)
            except Exception as error:
                # Provider exceptions can contain request data. Log only the class and public key id.
                logger.warning("Provider %s failed (%s)", key.id, type(error).__name__)
                attempts += 1
                continue

            if not failures:
                self.cache.add_cache(asdict(completion), cache_key=self.cache_key(key.model, messages))
            return Resolution(completion, key.model, key.id, attempts)

        raise ProvidersUnavailable
