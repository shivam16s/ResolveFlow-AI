from __future__ import annotations

from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.llm_client import LLMClient, load_env_file  # noqa: E402


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


def main() -> None:
    test_load_env_file_and_named_model_aliases()
    test_rejects_unknown_model_alias()
    print("llm client tests passed")


if __name__ == "__main__":
    main()
