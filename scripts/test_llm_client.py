from __future__ import annotations

from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import llm_client as llm_client_module  # noqa: E402
from backend.agent.llm_client import GeminiClientError, GeminiGenerateClient, LLMClient, load_env_file  # noqa: E402


def test_load_env_file_and_named_model_aliases() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env_path = Path(tmpdir) / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "GEMINI_API_KEY=test-key",
                    "GEMINI_PRIMARY_MODEL=primary-model",
                    "GEMINI_SECONDARY_MODEL=secondary-model",
                ]
            ),
            encoding="utf-8",
        )

        values = load_env_file(env_path)
        if values["GEMINI_API_KEY"] != "test-key":
            raise AssertionError(f"env parser failed: {values}")

        primary = LLMClient(model="primary", env_path=env_path)
        secondary = LLMClient(model="secondary", env_path=env_path)
        if primary.model_name != "primary-model" or secondary.model_name != "secondary-model":
            raise AssertionError((primary.model_name, secondary.model_name))
        if primary.model_alias != "primary" or secondary.model_alias != "secondary":
            raise AssertionError("model aliases were not preserved")


def test_rejects_unknown_model_alias() -> None:
    try:
        LLMClient(model="experimental", api_key="test-key")
    except ValueError as exc:
        if "model must be one of" not in str(exc):
            raise AssertionError(f"wrong error: {exc}")
    else:
        raise AssertionError("unknown model alias was accepted")


def test_gemini_api_key_uses_header_not_query_param() -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            return b'{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return FakeResponse()

    original_urlopen = llm_client_module.urlopen
    llm_client_module.urlopen = fake_urlopen
    try:
        client = GeminiGenerateClient(
            api_key="secret-test-key",
            model="gemini-test",
            timeout_seconds=7,
        )
        if client.generate("hello") != "ok":
            raise AssertionError("Gemini text extraction failed")
    finally:
        llm_client_module.urlopen = original_urlopen

    if "?key=" in captured["url"] or "secret-test-key" in captured["url"]:
        raise AssertionError(f"API key leaked into URL: {captured['url']}")

    headers = {key.lower(): value for key, value in captured["headers"].items()}
    if headers.get("x-goog-api-key") != "secret-test-key":
        raise AssertionError(f"API key header missing: {captured['headers']}")
    if captured["timeout"] != 7:
        raise AssertionError(f"timeout was not passed through: {captured}")


def test_gemini_timeout_error_is_wrapped() -> None:
    class TimeoutResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            raise TimeoutError("read timed out")

    def fake_urlopen(request, timeout):
        return TimeoutResponse()

    original_urlopen = llm_client_module.urlopen
    llm_client_module.urlopen = fake_urlopen
    try:
        client = GeminiGenerateClient(api_key="test-key", timeout_seconds=3)
        try:
            client.generate("hello")
        except GeminiClientError as exc:
            if "timed out after 3 seconds" not in str(exc):
                raise AssertionError(f"wrong timeout error: {exc}")
        else:
            raise AssertionError("raw TimeoutError was not wrapped")
    finally:
        llm_client_module.urlopen = original_urlopen


def main() -> None:
    test_load_env_file_and_named_model_aliases()
    test_rejects_unknown_model_alias()
    test_gemini_api_key_uses_header_not_query_param()
    test_gemini_timeout_error_is_wrapped()
    print("llm client tests passed")


if __name__ == "__main__":
    main()
