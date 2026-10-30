"""Terminal-level enforcement of the browser-desktop boundary (runtime patch).

The x-session-browser skill tells an unpinned session that desktop
resources are OFF LIMITS, but prompt text alone is not a constraint: a
Regular Session has still deliberated over reading another session's
display JSON and dialing its Chrome on :6901. The native browser-tool
path is already pinned to the session's own display config (see
``app.compat.browser_cdp``); this module closes the *terminal* path by
extending the Hermes approval system's regex blocklists.

``tools/approval.py`` detects dangerous commands by iterating
``DANGEROUS_PATTERNS_COMPILED`` (approval prompt in manual mode, LLM
judge in smart mode, pass in yolo) and hardline commands by iterating
``HARDLINE_PATTERNS_COMPILED`` (unconditional block, never approvable).
Both detectors walk their lists at call time, so appending compiled
tuples at server startup takes effect without touching any SDK file.

``BROWSER_DESKTOP_BLOCK_STRATEGY`` picks the strength (process-wide,
read once at install; changing it requires a restart):

- ``approval`` (default, also on unknown values) — append to the
  dangerous list: matched commands raise an approval request the user
  can allow or deny in the webui.
- ``hard`` — append to the hardline list: matched commands are refused
  outright, even under yolo.
- ``off`` — append nothing: terminal commands touching desktop
  resources pass through ungated.

The agent-browser wrapper deliberately does NOT read this variable.
It overrides connection targets only when the session's display config
exists (pinned — the observation contract) and is a pure pass-through
otherwise, so unpinned access under ``off`` is genuinely open, and
under ``approval``/``hard`` the command-level patterns are the single
gate (an approved command really runs, un-stripped).

Deliberately NOT patterned: reading session display JSON files (the
skill's own pinned-verdict procedure must read its own file) and
launching Chrome/agent-browser itself (an unpinned session may run its
own headless browser). Only explicit *connection targets* — CDP/VNC
endpoints, ``--cdp`` flags, X display binds, desktop tooling — are
gated.

Honest boundary: detection runs on the *command string*. Any endpoint
written as a literal in the command — curl, inline ``python -c``,
heredoc scripts, websocket/websocat clients, MCP ``--browser-url``,
``export D=http://… && curl $D`` — is caught. An endpoint hidden
inside a file created by a non-terminal tool (``write`` a script, then
``python that.py``) is not visible here; neither are runtime-obfuscated
endpoints. This is a tripwire, not a sandbox — the pinned/unpinned
distinction itself stays structural (display-JSON resolution in
browser tools and the wrapper).

No SDK file is modified: this appends to lists the SDK already
iterates at runtime (AGENTS.md forbids editing the SDK checkout).
"""

import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

STRATEGY_ENV = "BROWSER_DESKTOP_BLOCK_STRATEGY"

# Mirrors tools.approval._RE_FLAGS so our patterns behave like the
# shipped ones (the detectors feed them lowercased command variants).
_RE_FLAGS = re.IGNORECASE | re.DOTALL

# Fallback for tools.approval._CMDPOS (start of line, after a command
# separator ; && || |, after $(/backtick, or after sudo/env wrappers) —
# used to anchor tool-name patterns so `grep Xvfb /var/log` does not
# read as launching Xvfb. Hardline false positives cannot be waved
# through, so the anchor matters.
_CMDPOS_FALLBACK = r"(?:^|[;&|`\n]|&&|\|\||\$\()"

# (needs_cmdpos_anchor, regex, description). The description doubles as
# the approval pattern_key, so it must be stable English prose.
_GUARD_PATTERNS = [
    # curl/httpx/websocket straight at a desktop CDP (69xx) or VNC (59xx)
    # port. Flags/values can appear anywhere in the command, so no anchor.
    # 0.0.0.0 connects to the local host on Linux — same reach, same gate.
    (
        False,
        r"(?:(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):(?:59|69)\d\d)\b",
        "browser desktop CDP/VNC endpoint access",
    ),
    # Any tool explicitly selecting a CDP endpoint (agent-browser,
    # chrome-remote-interface, custom scripts …).
    (
        False,
        r"(?<![\w-])--cdp(?:=|\s)",
        "explicit --cdp endpoint override",
    ),
    # export DISPLAY=:99 / env DISPLAY=:0 … — binding to a desktop.
    (
        False,
        r"DISPLAY=:",
        "binding to an X display",
    ),
    # Desktop infrastructure tooling, anchored to command position so it
    # only fires when the tool is actually being invoked.
    (
        True,
        r"(x11vnc|xvfb-run|xvfb|xdotool|xrandr|xwininfo)\b",
        "browser desktop X/VNC tooling",
    ),
]

_install_lock = threading.Lock()
_installed = False
_installed_strategy = None


def normalize_strategy(raw: str) -> str:
    """Map an env-var value to ``approval`` / ``hard`` / ``off``.

    Unknown values fall back to ``approval`` — failing open on a typo
    would silently disable the gate; failing closed would brick the
    terminal on a typo. Approval keeps a human in the loop either way.
    """
    value = (raw or "").strip().lower()
    if value in ("hard", "block"):
        return "hard"
    if value in ("off", "none", "open"):
        return "off"
    return "approval"


def _cmdpos() -> str:
    """The SDK's command-position anchor, with a faithful fallback."""
    try:
        from tools import approval as _approval

        return getattr(_approval, "_CMDPOS") or _CMDPOS_FALLBACK
    except Exception:
        return _CMDPOS_FALLBACK


def guard_patterns():
    """Compiled ``(regex, description)`` pairs with anchors resolved."""
    anchor = _cmdpos()
    return [
        (
            re.compile((anchor + pattern) if needs_anchor else pattern, _RE_FLAGS),
            description,
        )
        for needs_anchor, pattern, description in _GUARD_PATTERNS
    ]


def install() -> None:
    """Append desktop-boundary patterns per strategy (idempotent)."""
    global _installed, _installed_strategy
    with _install_lock:
        if _installed:
            return
        try:
            from tools import approval as _approval
        except Exception:
            # SDK not importable in this process (e.g. unit tests without
            # the hermes checkout on sys.path) — nothing to gate.
            logger.debug(
                "compat.desktop_guard: tools.approval not importable; skip",
                exc_info=True,
            )
            return

        strategy = normalize_strategy(os.environ.get(STRATEGY_ENV, ""))
        _installed_strategy = strategy

        if strategy == "off":
            logger.info(
                "compat.desktop_guard: strategy=off; terminal desktop "
                "gating disabled"
            )
            _installed = True
            return

        if strategy == "hard":
            target_name = "HARDLINE"
            raw_list = _approval.HARDLINE_PATTERNS
            compiled_list = _approval.HARDLINE_PATTERNS_COMPILED
        else:
            target_name = "DANGEROUS"
            raw_list = _approval.DANGEROUS_PATTERNS
            compiled_list = _approval.DANGEROUS_PATTERNS_COMPILED

        # Descriptions are the identity of an entry: skip if a previous
        # install (or a future SDK version) already carries them, so the
        # idempotency marker is belt-and-braces rather than load-bearing.
        existing = {description for _, description in compiled_list}
        appended = 0
        anchor = _cmdpos()
        for needs_anchor, pattern, description in _GUARD_PATTERNS:
            if description in existing:
                continue
            source_pattern = anchor + pattern if needs_anchor else pattern
            raw_list.append((source_pattern, description))
            compiled_list.append((re.compile(source_pattern, _RE_FLAGS), description))
            if strategy == "approval":
                # Register the canonical key for approval bookkeeping.
                # The alias map is built at SDK import from
                # DANGEROUS_PATTERNS only, so it would otherwise rely on
                # _approval_key_aliases' {pattern_key} fallback. (No
                # legacy regex key: these patterns never had one — new
                # approvals are keyed by the description string.)
                _approval._PATTERN_KEY_ALIASES.setdefault(
                    description, {description}
                )
            appended += 1

        logger.info(
            "compat.desktop_guard: strategy=%s, %d desktop pattern(s) "
            "appended to %s_PATTERNS",
            strategy, appended, target_name,
        )
        _installed = True
