"""Cross-session env isolation and session-display CDP resolution (regressions).

Hermes runs each tool call on a ThreadPoolExecutor worker wrapped in
propagate_context_to_thread() (tools/thread_context.py), which copies
contextvars into the worker. The old threading.local-based overrides in
config._set_thread_env never reached those workers, so a tool-spawned
subprocess inherited the raw process-global os.environ — leaking one
concurrent session's values into another session's terminal commands.

The CDP endpoint itself deliberately does NOT ride any env var: the
single source of truth is the per-session display config written at pin
time ($HERMES_WEBUI_STATE_DIR/sessions_display/<session_id>.json).
In-process SDK readers get it through the app.compat.browser_cdp patch
(session id from the ContextVar-carried HERMES_SESSION_KEY → JSON), and
the agent-browser wrapper resolves the same JSON in the child env. These
tests pin down both mechanisms against concurrent-session interference.
"""

import contextvars
import json
import os
import subprocess
import threading

from app.api import config as app_config


def _run_on_copied_context(fn):
    """Run fn on a worker thread with the parent's contextvars copied.

    Mirrors tools/thread_context.propagate_context_to_thread — the
    propagation Hermes applies to concurrent tool-worker submissions.
    """
    ctx = contextvars.copy_context()
    box = {}

    def _target():
        try:
            box["value"] = ctx.run(fn)
        except Exception as exc:  # pragma: no cover - diagnostic path
            box["exc"] = exc

    t = threading.Thread(target=_target)
    t.start()
    t.join(timeout=30)
    if "exc" in box:
        raise box["exc"]
    return box.get("value")


def _child_env_value():
    """Spawn a subprocess that prints TERMINAL_CWD; return what it saw."""
    out = subprocess.run(
        [
            "python3",
            "-c",
            "import os; print(os.environ.get('TERMINAL_CWD', '<unset>'))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


class _PatchedEnviron:
    """Temporarily set/unset a global os.environ entry around a block."""

    def __init__(self, key, value):
        self.key = key
        self.value = value
        self.old = None
        self.had = key in os.environ

    def __enter__(self):
        if self.had:
            self.old = os.environ[self.key]
        os.environ[self.key] = self.value
        return self

    def __exit__(self, *exc):
        if self.had:
            os.environ[self.key] = self.old
        else:
            os.environ.pop(self.key, None)
        return False


# ── Subprocess path (_IsolatedPopen + ContextVar overrides) ─────────────────


def test_override_reaches_context_copied_worker():
    """Session overrides set on the session thread reach copied workers.

    A *foreign* stale value sits in the global os.environ; the per-session
    override must win inside the worker's spawned subprocess.
    """
    app_config._set_thread_env(TERMINAL_CWD="/tmp/sessA")
    try:
        with _PatchedEnviron("TERMINAL_CWD", "/tmp/sessB"):
            seen = _run_on_copied_context(_child_env_value)
    finally:
        app_config._clear_thread_env()
    assert seen == "/tmp/sessA"


def test_empty_override_masks_foreign_global_value():
    """An explicit empty override never sees another session's value."""
    app_config._set_thread_env(TERMINAL_CWD="")
    try:
        with _PatchedEnviron("TERMINAL_CWD", "/tmp/sessB"):
            seen = _run_on_copied_context(_child_env_value)
    finally:
        app_config._clear_thread_env()
    assert seen == ""


def test_no_override_context_is_unaffected():
    """Contexts that never registered overrides keep legacy behavior."""
    app_config._clear_thread_env()
    with _PatchedEnviron("TERMINAL_CWD", "/tmp/sessB"):
        seen = _run_on_copied_context(_child_env_value)
    # No override in this context → the raw global value is inherited,
    # exactly as before the isolation mechanism existed.
    assert seen == "/tmp/sessB"


# ── In-process SDK reader (browser_tool._get_cdp_override_raw) ──────────────
#
# browser_tool runs inside the hermes process and used to read the
# process-global os.environ directly — concurrent pinned sessions would
# read each other's real port and cross-attach. app.compat.browser_cdp
# patches the getter to resolve the endpoint from the session-display
# JSON via the ContextVar-carried HERMES_SESSION_KEY. The tests below
# exercise the patched getter via a stub ``orig`` (the real SDK module is
# heavy to import; install() covers the wiring separately).


def _make_stub_getter():
    """Stub of the SDK's original _get_cdp_override_raw (global-env reader)."""
    return lambda: os.environ.get("BROWSER_CDP_URL", "").strip()


def _write_display_config(tmp_path, session_id, cdp_url="http://127.0.0.1:6900"):
    (tmp_path / f"{session_id}.json").write_text(
        json.dumps(
            {
                "display": 99,
                "vnc_port": 5900,
                "cdp_port": int(cdp_url.rsplit(":", 1)[-1]),
                "cdp_url": cdp_url,
            }
        )
    )


def test_cdp_getter_reads_session_json_despite_foreign_global(
    monkeypatch, tmp_path
):
    """Pinned session A sees ITS endpoint even while B's URL sits in env.

    Trigger path this regresses: A (:6900) and B (:6901) pinned sessions
    run concurrently; global os.environ carries a foreign 6901 (e.g. a
    user-set value). A's browser_navigate → tool worker → patched getter
    must resolve A's own display JSON → 6900, not the foreign 6901.
    """
    from app.compat.browser_cdp import make_patched_getter

    monkeypatch.setattr(app_config, "SESSIONS_DISPLAY_DIR", tmp_path)
    _write_display_config(tmp_path, "sessA", "http://127.0.0.1:6900")
    getter = make_patched_getter(_make_stub_getter())
    app_config._set_thread_env(
        HERMES_SESSION_KEY="agent:main:webui:dm:sessA"
    )
    try:
        with _PatchedEnviron("BROWSER_CDP_URL", "http://127.0.0.1:6901"):
            seen = _run_on_copied_context(getter)
    finally:
        app_config._clear_thread_env()
    assert seen == "http://127.0.0.1:6900"


def test_cdp_getter_unpinned_session_reads_empty(monkeypatch, tmp_path):
    """A session with no display JSON (unpinned) never attaches to a foreign Chrome."""
    from app.compat.browser_cdp import make_patched_getter

    monkeypatch.setattr(app_config, "SESSIONS_DISPLAY_DIR", tmp_path)
    getter = make_patched_getter(_make_stub_getter())
    app_config._set_thread_env(
        HERMES_SESSION_KEY="agent:main:webui:dm:sessB"
    )
    try:
        with _PatchedEnviron("BROWSER_CDP_URL", "http://127.0.0.1:6900"):
            seen = _run_on_copied_context(getter)
    finally:
        app_config._clear_thread_env()
    assert seen == ""


def test_cdp_getter_no_session_context_falls_back_to_orig():
    """Contexts without a session key keep SDK precedence (global env)."""
    from app.compat.browser_cdp import make_patched_getter

    getter = make_patched_getter(_make_stub_getter())
    app_config._clear_thread_env()
    with _PatchedEnviron("BROWSER_CDP_URL", "http://127.0.0.1:6901"):
        assert getter() == "http://127.0.0.1:6901"


def test_cdp_getter_translates_legacy_sentinel():
    """The legacy 19999 sentinel is never a live endpoint — reads as unconfigured."""
    from app.compat.browser_cdp import CDP_URL_SENTINEL, make_patched_getter

    getter = make_patched_getter(_make_stub_getter())
    app_config._clear_thread_env()
    with _PatchedEnviron("BROWSER_CDP_URL", CDP_URL_SENTINEL):
        assert getter() == ""


def test_cdp_getter_unreadable_config_reads_empty(monkeypatch, tmp_path):
    """Corrupt display config → no desktop, rather than guessing a port."""
    from app.compat.browser_cdp import make_patched_getter

    monkeypatch.setattr(app_config, "SESSIONS_DISPLAY_DIR", tmp_path)
    (tmp_path / "sessA.json").write_text("{not json")
    getter = make_patched_getter(_make_stub_getter())
    app_config._set_thread_env(
        HERMES_SESSION_KEY="agent:main:webui:dm:sessA"
    )
    try:
        assert getter() == ""
    finally:
        app_config._clear_thread_env()


def test_install_patches_sdk_module_if_importable():
    """install() rebinds browser_tool._get_cdp_override_raw when SDK is present."""
    from app.compat import browser_cdp

    try:
        from tools import browser_tool as _bt
    except Exception:
        import pytest
        pytest.skip("hermes SDK browser_tool not importable in this process")
    browser_cdp.install()
    assert getattr(_bt, "_leap_aide_cdp_patched", False)
    assert _bt._get_cdp_override_raw.__name__ == "_patched"
