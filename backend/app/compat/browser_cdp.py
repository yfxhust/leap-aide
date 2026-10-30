"""Session-display-JSON-based CDP resolution for the Hermes SDK (runtime patch).

The SDK resolves the browser CDP endpoint from the process-global
``os.environ['BROWSER_CDP_URL']`` (``tools/browser_tool.py``:
``_get_cdp_override_raw``). LeapAide sessions run concurrently in one
process, so that global must never carry a per-session value — two pinned
sessions would read each other's port and cross-attach to the wrong Chrome.

Single source of truth: the per-session display config written by
SessionDisplay at pin time —
``$HERMES_WEBUI_STATE_DIR/sessions_display/<session_id>.json`` (its
``cdp_url`` field). This module replaces the SDK's override getter with
one that:

1. takes the session id from the ContextVar-carried ``HERMES_SESSION_KEY``
   (``app.compat.env_overrides``; Hermes tool workers inherit it via
   ``propagate_context_to_thread()``);
2. reads that session's display JSON;
3. returns ``cdp_url`` when the JSON exists (pinned), ``""`` when it does
   not (unpinned — browser tools fall through to local headless);
4. with no session context in the thread (prewarm, CLI-style), falls back
   to the SDK's original lookup (global env / ``browser.cdp_url`` config).

Because unpinning deletes the JSON, in-process consumers see desktop state
in real time — there is no injected env copy that can go stale.

No SDK file is modified: this is a runtime monkey-patch installed from
LeapAide-owned code (AGENTS.md forbids editing the SDK checkout).
"""

import json
import logging
import threading

logger = logging.getLogger(__name__)

# Legacy sentinel ("resolve me from the session JSON") that earlier LeapAide
# versions wrote to the global env. Nothing writes it anymore; still
# translated to "" on the fallback path so a stale value in a long-lived
# shell never reads as a live endpoint.
CDP_URL_SENTINEL = "http://127.0.0.1:19999"

_install_lock = threading.Lock()
_installed = False


def _normalize(value: str) -> str:
    value = (value or "").strip()
    return "" if value == CDP_URL_SENTINEL else value


def _session_cdp_url():
    """Return this session's CDP URL from its display JSON, or None.

    None means "no session context in this thread" — callers fall back to
    the SDK's original lookup. An empty string is a real answer: the
    session has no browser desktop (unpinned), which must read as "no CDP
    configured" so the browser tools use local headless.
    """
    from app.compat.env_overrides import env_override_ctx

    overrides = env_override_ctx.get() or {}
    session_key = (overrides.get("HERMES_SESSION_KEY") or "").strip()
    if not session_key:
        return None
    # webui format is agent:main:webui:dm:<session_id>; a bare session id
    # (routes.py sync path) also works with rsplit.
    session_id = session_key.rsplit(":", 1)[-1]
    if not session_id:
        return None

    try:
        from app.api import config as _app_config

        json_path = _app_config.SESSIONS_DISPLAY_DIR / f"{session_id}.json"
        data = json.loads(json_path.read_text())
    except FileNotFoundError:
        return ""
    except Exception:
        # Unreadable config → treat as no desktop rather than guessing.
        logger.debug(
            "compat.browser_cdp: failed reading display config for %s",
            session_id, exc_info=True,
        )
        return ""
    return _normalize(str(data.get("cdp_url") or ""))


def make_patched_getter(orig):
    """Build the ContextVar/JSON-aware replacement for ``_get_cdp_override_raw``.

    Split out from :func:`install` so tests can exercise the logic against
    a stub ``orig`` without importing the heavy SDK module.
    """

    def _patched():
        session_url = _session_cdp_url()
        if session_url is not None:
            return session_url
        # No session context (background thread / CLI-style): SDK
        # precedence, with the legacy sentinel never treated as live.
        return _normalize(orig())

    return _patched


def make_patched_camofox(orig):
    """Camofox must yield to a session browser desktop.

    ``browser_camofox.is_camofox_mode`` reads the global env directly
    (deliberately avoiding a browser_tool import), so the session-JSON
    signal never reaches it on its own. ``browser_tool`` also binds the
    function to a private alias at import time — patch both names.
    """

    def _patched():
        if (_session_cdp_url() or ""):
            return False  # this session has a CDP desktop — no camofox
        return orig()

    return _patched


def install() -> None:
    """Patch the SDK's CDP/camofox lookups (idempotent)."""
    global _installed
    with _install_lock:
        if _installed:
            return
        try:
            from tools import browser_camofox as _cf
            from tools import browser_tool as _bt
        except Exception:
            # SDK not importable in this process (e.g. unit tests without
            # the hermes checkout on sys.path) — nothing to patch.
            logger.debug(
                "compat.browser_cdp: SDK browser modules not importable; skip",
                exc_info=True,
            )
            return

        if not getattr(_bt, "_leap_aide_cdp_patched", False):
            _bt._get_cdp_override_raw = make_patched_getter(
                _bt._get_cdp_override_raw
            )
            # browser_tool binds is_camofox_mode to a private alias at
            # import time (module top level); keep it in agreement.
            if hasattr(_bt, "_is_camofox_mode"):
                _bt._is_camofox_mode = make_patched_camofox(
                    _bt._is_camofox_mode
                )
            _bt._leap_aide_cdp_patched = True

        if not getattr(_cf, "_leap_aide_cdp_patched", False):
            _cf.is_camofox_mode = make_patched_camofox(_cf.is_camofox_mode)
            _cf._leap_aide_cdp_patched = True

        _installed = True
        logger.info(
            "compat.browser_cdp: session-display-JSON CDP resolution patch installed"
        )
