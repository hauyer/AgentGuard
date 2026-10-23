"""Minimal OpenAI-compatible JSON client for opt-in research agents.

No tools or arbitrary code execution are exposed to the model. API keys are read from
an environment variable and are never included in exception text or audit payloads.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class ModelClientError(RuntimeError):
    """Sanitized provider/configuration error; intentionally excludes response bodies."""


Transport = Callable[[str, dict[str, str], bytes, float], bytes]


def _default_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        raise ModelClientError(f"model provider returned HTTP {exc.code}") from None
    except URLError as exc:
        raise ModelClientError(f"model provider connection failed ({type(exc.reason).__name__})") from None
    except TimeoutError:
        raise ModelClientError("model provider request timed out") from None


class OpenAICompatibleJSONClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 20.0,
        transport: Transport | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        local_http = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local_http):
            raise ValueError("base_url must use HTTPS; plain HTTP is allowed only for loopback testing")
        if not parsed.netloc or not model.strip() or not api_key:
            raise ValueError("base_url, api_key, and model are required")
        if not 1 <= timeout <= 120:
            raise ValueError("timeout must be between 1 and 120 seconds")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport or _default_transport
        self.last_usage: dict[str, int] = {}

    @classmethod
    def from_env(cls, *, transport: Transport | None = None) -> "OpenAICompatibleJSONClient":
        api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ModelClientError("set DEEPSEEK_API_KEY or OPENAI_API_KEY to enable the model advisor")
        base_url = os.environ.get("AGENTGUARD_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com/v1"
        model = os.environ.get("AGENTGUARD_LLM_MODEL", "deepseek-chat")
        try:
            timeout = float(os.environ.get("AGENTGUARD_LLM_TIMEOUT", "20"))
        except ValueError:
            raise ModelClientError("AGENTGUARD_LLM_TIMEOUT must be numeric") from None
        return cls(base_url=base_url, api_key=api_key, model=model, timeout=timeout, transport=transport)

    def new_run(self) -> "OpenAICompatibleJSONClient":
        """Clone immutable connection settings so usage state is not shared across runs."""
        return OpenAICompatibleJSONClient(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            timeout=self.timeout,
            transport=self.transport,
        )

    def complete_json(self, *, system_prompt: str, payload: dict[str, Any], max_tokens: int = 512) -> dict[str, Any]:
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }, ensure_ascii=False).encode("utf-8")
        try:
            raw = self.transport(
                f"{self.base_url}/chat/completions",
                {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                body,
                self.timeout,
            )
            response = json.loads(raw.decode("utf-8"))
            content = response["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("model JSON response must be an object")
            usage = response.get("usage") or {}
            self.last_usage = {
                key: int(usage[key]) for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(usage.get(key), int) and usage[key] >= 0
            }
            return parsed
        except ModelClientError:
            raise
        except (KeyError, IndexError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise ModelClientError("model provider returned an invalid JSON response") from None
        except Exception as exc:
            raise ModelClientError(f"model request failed ({type(exc).__name__})") from None
