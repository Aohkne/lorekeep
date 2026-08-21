"""Shift+Tab API key / env var toggle used by interactive init."""
from __future__ import annotations

from lorekeep.init_prompt import (
    CredentialSession,
    classify_escape,
    tokenize_keys,
)
from lorekeep.providers import default_api_key_env


def _drive(session: CredentialSession, data: bytes) -> str:
    action = "continue"
    for event in tokenize_keys(data):
        action = session.handle(event)
        if action != "continue":
            return action
    return action


class TestDefaultApiKeyEnv:
    def test_openai(self):
        assert default_api_key_env("openai") == "OPENAI_API_KEY"

    def test_deepseek(self):
        assert default_api_key_env("deepseek") == "DEEPSEEK_API_KEY"

    def test_openai_compat_uses_openai_env(self):
        assert default_api_key_env("openai_compat") == "OPENAI_API_KEY"

    def test_together_ai(self):
        assert default_api_key_env("together_ai") == "TOGETHER_AI_API_KEY"


class TestTokenizeKeys:
    def test_shift_tab_csi(self):
        assert tokenize_keys(b"\x1b[Z") == ["shift-tab"]

    def test_shift_tab_xterm_variant(self):
        assert tokenize_keys(b"\x1b[27;2;9~") == ["shift-tab"]

    def test_tab(self):
        assert tokenize_keys(b"\t") == ["tab"]

    def test_enter_and_printable(self):
        assert tokenize_keys(b"sk-ab\r") == ["s", "k", "-", "a", "b", "enter"]

    def test_backspace_and_ctrl_u(self):
        assert tokenize_keys(b"a\x7f\x15") == ["a", "backspace", "clear"]


class TestClassifyEscape:
    def test_unknown_csi_is_none(self):
        assert classify_escape(b"\x1b[A") is None


class TestCredentialSession:
    def test_paste_key(self):
        s = CredentialSession("OPENAI_API_KEY")
        assert _drive(s, b"sk-testKEY\r") == "submit"
        cred = s.result()
        assert cred.api_key == "sk-testKEY"
        assert cred.api_key_env is None

    def test_empty_required_uses_suggested_env(self):
        s = CredentialSession("DEEPSEEK_API_KEY")
        assert _drive(s, b"\r") == "submit"
        cred = s.result()
        assert cred.api_key is None
        assert cred.api_key_env == "DEEPSEEK_API_KEY"

    def test_empty_optional_skips(self):
        s = CredentialSession("OPENAI_API_KEY", optional=True)
        assert _drive(s, b"\r") == "submit"
        cred = s.result()
        assert cred.api_key is None
        assert cred.api_key_env is None

    def test_shift_tab_submits_suggested_env(self):
        s = CredentialSession("OPENAI_API_KEY")
        assert "Shift+Tab" in "\n".join(s.widget_lines())
        assert "OPENAI_API_KEY" in "\n".join(s.widget_lines())
        assert _drive(s, b"\x1b[Z\r") == "submit"
        assert s.mode == "env"
        cred = s.result()
        assert cred.api_key is None
        assert cred.api_key_env == "OPENAI_API_KEY"

    def test_shift_tab_then_type_replaces_suggestion(self):
        s = CredentialSession("OPENAI_API_KEY")
        assert _drive(s, b"\x1b[ZMY_GATEWAY_KEY\r") == "submit"
        assert s.result().api_key_env == "MY_GATEWAY_KEY"

    def test_tab_also_toggles(self):
        s = CredentialSession("OPENAI_API_KEY")
        _drive(s, b"\t")
        assert s.mode == "env"
        _drive(s, b"\t")
        assert s.mode == "key"

    def test_toggle_back_to_key_and_paste(self):
        s = CredentialSession("OPENAI_API_KEY")
        assert _drive(s, b"\x1b[Z\x1b[Zsk-abc\r") == "submit"
        cred = s.result()
        assert cred.api_key == "sk-abc"
        assert cred.api_key_env is None

    def test_env_skip(self):
        s = CredentialSession("OPENAI_API_KEY")
        _drive(s, b"\x1b[Z")
        s.handle("clear")
        assert _drive(s, b"skip\r") == "submit"
        cred = s.result()
        assert cred.api_key is None
        assert cred.api_key_env is None

    def test_ctrl_c_cancels(self):
        s = CredentialSession("OPENAI_API_KEY")
        assert s.handle("ctrl-c") == "cancel"

    def test_widget_hides_key(self):
        s = CredentialSession("OPENAI_API_KEY")
        _drive(s, b"sk-secret")
        text = "\n".join(s.widget_lines())
        assert "sk-secret" not in text
        assert "*" * len("sk-secret") in text
        assert s.mask_len == len("sk-secret")
