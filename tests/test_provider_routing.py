from opencane.config.schema import Config
from opencane.providers.registry import find_by_model


def test_find_by_model_prefers_explicit_prefix_over_keyword_match() -> None:
    spec = find_by_model("deepseek/gpt-4.1")
    assert spec is not None
    assert spec.name == "deepseek"


def test_config_match_prefers_explicit_prefix_when_multiple_provider_keys_present() -> None:
    cfg = Config()
    cfg.agents.defaults.model = "deepseek/gpt-4.1"
    cfg.providers.openai.api_key = "openai-key"
    cfg.providers.deepseek.api_key = "deepseek-key"

    assert cfg.get_provider_name() == "deepseek"

