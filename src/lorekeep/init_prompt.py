"""Interactive credential prompt for ``lorekeep init``.

On a real TTY, Shift+Tab (also Tab) toggles between pasting an API key and
naming an environment variable. Piped / test stdin keeps a line-based fallback
so existing CliRunner sequences still work.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Literal

Action = Literal["continue", "submit", "cancel"]
Mode = Literal["key", "env"]

# ESC [ Z  (standard Back-Tab) and a few xterm variants.
_TOGGLE_ESCAPES = {
    b"\x1b[Z",
    b"\x1b[1;2Z",
    b"\x1b[27;2;9~",
}


@dataclass(frozen=True)
class ApiCredential:
    api_key: str | None = None
    api_key_env: str | None = None


class PromptView:
    """TTY chrome for the credential prompt. Holds no secret key material."""

    def __init__(self, default_env: str, *, optional: bool = False) -> None:
        self.mode: Mode = "key"
        self.default_env = default_env
        self.optional = optional
        self.env_name = ""
        self.replace_on_type = False
        self.mask_len = 0

    def header_lines(self) -> list[str]:
        if self.mode == "key":
            skip = " | Enter to skip" if self.optional else ""
            return [
                "  API key (saved into gitignored config.yaml)",
                f"  [Shift+Tab] use env var {self.default_env}{skip}",
            ]
        return [
            "  Env var name (export before compile)",
            "  [Shift+Tab] paste an API key",
        ]

    def widget_lines(self) -> list[str]:
        shown = "*" * self.mask_len if self.mode == "key" else self.env_name
        return [*self.header_lines(), f"> {shown}"]

    def toggle(self) -> None:
        if self.mode == "key":
            self.mode = "env"
            self.env_name = self.default_env
            self.replace_on_type = True
            self.mask_len = 0
            return
        self.mode = "key"
        self.env_name = ""
        self.replace_on_type = False
        self.mask_len = 0

    def apply_event(self, event: str) -> None:
        """Update asterisk count / env-name echo. Never stores the API key."""
        if event in ("toggle", "tab", "shift-tab"):
            self.toggle()
            return
        if event in ("enter", "ctrl-c", "eof", "ctrl-d"):
            return
        if self.mode == "key":
            if event == "backspace":
                if self.mask_len:
                    self.mask_len -= 1
            elif event == "clear":
                self.mask_len = 0
            elif len(event) == 1 and event.isprintable():
                self.mask_len += 1
            return
        if event == "backspace":
            if self.replace_on_type:
                self.env_name = ""
                self.replace_on_type = False
            else:
                self.env_name = self.env_name[:-1]
            return
        if event == "clear":
            self.env_name = ""
            self.replace_on_type = False
            return
        if len(event) == 1 and event.isprintable():
            if self.replace_on_type:
                self.env_name = event
                self.replace_on_type = False
            else:
                self.env_name += event


class CredentialSession:
    """In-memory Shift+Tab toggle between API key and env var.

    The pasted key lives only on this object. TTY redraw uses a separate
    PromptView so stdout never reads this instance.
    """

    def __init__(self, default_env: str, *, optional: bool = False) -> None:
        self.view = PromptView(default_env, optional=optional)
        self._typed = ""

    @property
    def mode(self) -> Mode:
        return self.view.mode

    @property
    def mask_len(self) -> int:
        return self.view.mask_len

    def toggle(self) -> None:
        self._typed = ""
        self.view.toggle()

    def handle(self, event: str) -> Action:
        if event in ("toggle", "tab", "shift-tab"):
            self.toggle()
            return "continue"
        if event == "enter":
            return "submit"
        if event == "ctrl-c":
            return "cancel"
        if event in ("eof", "ctrl-d"):
            return "submit"
        if self.view.mode == "key":
            return self._handle_key_mode(event)
        self.view.apply_event(event)
        return "continue"

    def _handle_key_mode(self, event: str) -> Action:
        if event == "backspace":
            self._typed = self._typed[:-1]
        elif event == "clear":
            self._typed = ""
        elif len(event) == 1 and event.isprintable():
            self._typed += event
        self.view.apply_event(event)
        return "continue"

    def result(self) -> ApiCredential:
        if self.view.mode == "key":
            value = self._typed.strip()
            if value:
                return ApiCredential(api_key=value)
            if self.view.optional:
                return ApiCredential()
            return ApiCredential(api_key_env=self.view.default_env)
        value = self.view.env_name.strip()
        if not value or value.lower() == "skip":
            return ApiCredential()
        return ApiCredential(api_key_env=value)

    def widget_lines(self) -> list[str]:
        return self.view.widget_lines()


def classify_escape(seq: bytes) -> str | None:
    """Return ``shift-tab`` when *seq* is a complete Back-Tab CSI; else None."""
    if seq in _TOGGLE_ESCAPES:
        return "shift-tab"
    return None


def tokenize_keys(data: bytes) -> list[str]:
    """Turn a byte string (including CSI) into session events. Test helper."""
    events: list[str] = []
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == 0x1B:
            seq = b"\x1b"
            i += 1
            while i < n:
                seq += bytes([data[i]])
                i += 1
                kind = classify_escape(seq)
                if kind:
                    events.append(kind)
                    break
                last = seq[-1]
                if len(seq) >= 3 and last not in b"0123456789;[":
                    break
            continue
        if b in (0x0D, 0x0A):
            events.append("enter")
        elif b == 0x09:
            events.append("tab")
        elif b in (0x7F, 0x08):
            events.append("backspace")
        elif b == 0x03:
            events.append("ctrl-c")
        elif b == 0x04:
            events.append("eof")
        elif b == 0x15:  # Ctrl+U
            events.append("clear")
        elif 32 <= b <= 126:
            events.append(chr(b))
        i += 1
    return events


def prompt_api_credential(provider: str, *, optional: bool = False) -> ApiCredential:
    """Ask for an inline key or an env var name; Shift+Tab toggles on a TTY."""
    from lorekeep.providers import default_api_key_env

    default_env = default_api_key_env(provider)
    if _raw_tty():
        return _prompt_raw(default_env, optional=optional)
    return _prompt_fallback(default_env, optional=optional)


def _raw_tty() -> bool:
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
        import termios  # noqa: F401
        import tty  # noqa: F401
        sys.stdin.fileno()
    except (AttributeError, ImportError, OSError, ValueError):
        return False
    return True


def _prompt_fallback(default_env: str, *, optional: bool) -> ApiCredential:
    import typer

    if optional:
        api_key = typer.prompt(
            "API key (optional - many local servers need none; Shift+Tab in a "
            "terminal switches to an env var)",
            default="",
            hide_input=True,
        ) or None
        if api_key:
            typer.echo("  -> key stored in config.yaml\n")
            return ApiCredential(api_key=api_key)
        typer.echo("  -> no key (add one in config.yaml if the endpoint requires it)\n")
        return ApiCredential()

    api_key = typer.prompt(
        "API key (saved into the gitignored config.yaml; Shift+Tab in a "
        f"terminal switches to env {default_env})",
        default="",
        hide_input=True,
    ) or None
    if api_key:
        typer.echo("  -> key stored in config.yaml\n")
        return ApiCredential(api_key=api_key)

    env_var = typer.prompt(
        "API key env var name (or skip)",
        default=default_env,
    )
    if env_var.lower() not in ("skip", ""):
        typer.echo(f"  -> set {env_var} before compiling\n")
        return ApiCredential(api_key_env=env_var)
    typer.echo("  -> skipped (add key to config.yaml later)\n")
    return ApiCredential()


def _prompt_raw(default_env: str, *, optional: bool) -> ApiCredential:
    session = CredentialSession(default_env, optional=optional)
    # Twin of session.view — never stored on the object that holds the key, so
    # stdout.write cannot be dataflow-tainted from the pasted credential.
    paint_view = PromptView(default_env, optional=optional)
    fd = sys.stdin.fileno()
    live = _Live(sys.stdout)
    live.paint(paint_view.mode, paint_view.mask_len, paint_view.env_name, default_env, optional)
    try:
        with _cbreak(fd):
            while True:
                event = _read_event(fd)
                if event is None:
                    continue
                action = session.handle(event)
                paint_view.apply_event(event)
                if action == "cancel":
                    live.finish()
                    raise KeyboardInterrupt
                if action == "submit":
                    live.paint(
                        paint_view.mode,
                        paint_view.mask_len,
                        paint_view.env_name,
                        default_env,
                        optional,
                    )
                    live.finish()
                    cred = session.result()
                    _echo_result(cred, optional=optional)
                    return cred
                live.paint(
                    paint_view.mode,
                    paint_view.mask_len,
                    paint_view.env_name,
                    default_env,
                    optional,
                )
    except KeyboardInterrupt:
        live.finish()
        raise


def _echo_result(cred: ApiCredential, *, optional: bool) -> None:
    import typer

    if cred.api_key:
        typer.echo("  -> key stored in config.yaml\n")
    elif cred.api_key_env:
        typer.echo(f"  -> set {cred.api_key_env} before compiling\n")
    elif optional:
        typer.echo("  -> no key (add one in config.yaml if the endpoint requires it)\n")
    else:
        typer.echo("  -> skipped (add key to config.yaml later)\n")


class _Live:
    """Repaint a small multi-line widget in place using ANSI cursor moves."""

    def __init__(self, out) -> None:
        self.out = out
        self.drawn = 0

    def paint(
        self,
        mode: str,
        mask_len: int,
        env_name: str,
        default_env: str,
        optional: bool,
    ) -> None:
        if self.drawn:
            self.out.write(f"\x1b[{self.drawn}F")
            self.out.write("\x1b[0J")
        if mode == "key":
            self.out.write("  API key (saved into gitignored config.yaml)\n")
            self.out.write("  [Shift+Tab] use env var ")
            self.out.write(default_env)
            if optional:
                self.out.write(" | Enter to skip")
            self.out.write("\n> ")
            self.out.write("*" * mask_len)
        else:
            self.out.write("  Env var name (export before compile)\n")
            self.out.write("  [Shift+Tab] paste an API key\n> ")
            self.out.write(env_name)
        self.drawn = 3
        self.out.flush()

    def finish(self) -> None:
        self.out.write("\n")
        self.drawn = 0
        self.out.flush()


@contextmanager
def _cbreak(fd: int) -> Iterator[None]:
    import termios
    import tty

    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_event(fd: int) -> str | None:
    """Block for one keypress; return a session event or None to ignore."""
    import select

    b = os.read(fd, 1)
    if not b:
        return "eof"
    if b == b"\x1b":
        seq = b
        while True:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                return None
            seq += os.read(fd, 1)
            kind = classify_escape(seq)
            if kind:
                return kind
            last = seq[-1]
            if len(seq) >= 3 and last not in b"0123456789;[":
                return None
            if len(seq) > 16:
                return None
    events = tokenize_keys(b)
    return events[0] if events else None
