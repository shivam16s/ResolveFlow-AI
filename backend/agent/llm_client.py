from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class GeminiClientError(RuntimeError):
    pass


LLM_MODEL_ALIASES = ("primary", "secondary")


class GeminiGenerateClient:
    """Small Gemini generateContent client backed by environment variables."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        env_path: str | Path | None = None,
        timeout_seconds: int = 45,
    ) -> None:
        env_values = load_env_file(env_path)
        self.api_key = api_key or os.getenv(
            "GEMINI_API_KEY") or env_values.get("GEMINI_API_KEY", "")
        self.model = model or os.getenv("GEMINI_MODEL") or env_values.get(
            "GEMINI_MODEL", "gemini-2.5-flash")
        self.timeout_seconds = timeout_seconds

        if not self.api_key.strip():
            raise GeminiClientError(
                "GEMINI_API_KEY is missing. Add it to .env or the process environment.")

    def __call__(self, prompt: str) -> str:
        return self.generate(prompt, max_output_tokens=2048)

    def generate(
        self,
        prompt: str,
        *,
        response_mime_type: str = "text/plain",
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
        thinking_budget: int | None = None,
    ) -> str:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        model_name = quote(self.model, safe="")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": response_mime_type,
        }
        if thinking_budget is not None:
            # gemini-2.5-flash spends part of max_output_tokens on internal
            # reasoning before emitting visible text; tasks that don't need
            # reasoning (e.g. translation) should disable it (budget=0) so the
            # full token budget goes to the actual output instead of being
            # silently consumed by an unpredictable thinking phase.
            generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise GeminiClientError(
                f"Gemini request failed with HTTP {exc.code}: {details}") from exc
        except TimeoutError as exc:
            raise GeminiClientError(
                f"Gemini request timed out after {self.timeout_seconds} seconds") from exc
        except URLError as exc:
            raise GeminiClientError(
                f"Gemini request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise GeminiClientError(
                "Gemini response was not valid JSON") from exc

        return _extract_text(payload)


class LLMClient:
    """Named-model LLM client used by agent modules that need primary/secondary routing."""

    def __init__(
        self,
        model: str = "primary",
        *,
        api_key: str | None = None,
        env_path: str | Path | None = None,
        timeout_seconds: int = 45,
    ) -> None:
        if model not in LLM_MODEL_ALIASES:
            raise ValueError(f"model must be one of {LLM_MODEL_ALIASES}")

        env_values = load_env_file(env_path)
        self.model_alias = model
        self.model_name = _model_name_for_alias(model, env_values)
        self.client = GeminiGenerateClient(
            api_key=api_key,
            model=self.model_name,
            env_path=env_path,
            timeout_seconds=timeout_seconds,
        )

    def __call__(self, prompt: str) -> str:
        return self.generate(prompt)

    def generate(
        self,
        prompt: str,
        *,
        response_mime_type: str = "text/plain",
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
        thinking_budget: int | None = None,
    ) -> str:
        return self.client.generate(
            prompt,
            response_mime_type=response_mime_type,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            thinking_budget=thinking_budget,
        )


def load_env_file(env_path: str | Path | None = None) -> dict[str, str]:
    path = Path(env_path) if env_path is not None else _default_env_path()
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        values[name.strip()] = _unquote_env_value(value.strip())
    return values


def _default_env_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _extract_text(payload: dict[str, Any]) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiClientError(
            f"Gemini response did not contain candidate text: {payload}") from exc

    text_parts = [str(part.get("text", ""))
                  for part in parts if isinstance(part, dict)]
    text = "".join(text_parts).strip()
    if not text:
        raise GeminiClientError(f"Gemini response text was empty: {payload}")
    return text


def _model_name_for_alias(alias: str, env_values: dict[str, str]) -> str:
    primary = (
        os.getenv("GEMINI_PRIMARY_MODEL")
        or env_values.get("GEMINI_PRIMARY_MODEL")
        or os.getenv("GEMINI_MODEL")
        or env_values.get("GEMINI_MODEL")
        or "gemini-2.5-flash"
    )
    secondary = (
        os.getenv("GEMINI_SECONDARY_MODEL")
        or env_values.get("GEMINI_SECONDARY_MODEL")
        or "gemini-2.5-flash-lite"
    )
    return primary if alias == "primary" else secondary
