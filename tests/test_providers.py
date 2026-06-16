from lorekeep.compile.providers import LLMProvider, FakeProvider, LiteLLMProvider


def test_fake_provider_returns_fixed_output():
    p = FakeProvider(responses=['{"nodes":[],"edges":[]}'])
    assert p.extract_json("sys", "user") == '{"nodes":[],"edges":[]}'


def test_fake_provider_raises_when_empty():
    p = FakeProvider(responses=[])
    try:
        p.extract_json("sys", "user")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_litellm_provider_holds_config():
    p = LiteLLMProvider(model="ollama/llama3", api_base="http://localhost:11434")
    assert p.model == "ollama/llama3"
    assert p.api_base == "http://localhost:11434"
    assert isinstance(p, LLMProvider)
