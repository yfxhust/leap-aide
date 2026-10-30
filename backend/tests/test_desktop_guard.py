"""Desktop-boundary terminal gating via BROWSER_DESKTOP_BLOCK_STRATEGY.

app.compat.desktop_guard appends regex tuples to the Hermes approval
system's compiled pattern lists (runtime-effective: both detectors walk
their lists per call). The strategy env var decides which list gains
the patterns:

- approval → DANGEROUS_PATTERNS(_COMPILED) → webui approval prompt
- hard     → HARDLINE_PATTERNS(_COMPILED) → unconditional block
- off      → nothing appended

Because the SDK's lists are module-level global state, every test here
snapshots and restores them — otherwise one strategy's patterns leak
into the next test (and into the rest of the suite).

The wrapper tests pin the other half of the boundary with a different
rule: agent-browser-wrapper.sh rewrites arguments ONLY when the
session's display config exists (pinned — the observation contract
strips caller-supplied ``--cdp``/``--session`` and injects the
session's own endpoint). Unpinned sessions get pure pass-through; the
strategy env var is deliberately not read by the wrapper.
"""

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

try:
    from tools import approval
except Exception:
    approval = None

pytestmark = pytest.mark.skipif(
    approval is None,
    reason="tools.approval not available in this environment",
)

from app.compat import desktop_guard

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "backend" / "scripts" / "agent-browser-wrapper.sh"

ENDPOINT_CMD = "curl -s http://127.0.0.1:6900/json/version"
BENIGN_COMMANDS = [
    "echo done",
    "curl -s https://example.com/health",
    "cat /home/user/.hermes/webui/sessions_display/sessA.json",
    "grep -r Xvfb /var/log",
    "ls /tmp",
]


# ── Global-state hygiene ─────────────────────────────────────────────────────


@pytest.fixture
def guard(monkeypatch):
    """Install-capable desktop_guard with SDK lists snapshotted."""
    snap = {
        "dangerous": list(approval.DANGEROUS_PATTERNS),
        "dangerous_c": list(approval.DANGEROUS_PATTERNS_COMPILED),
        "hardline": list(approval.HARDLINE_PATTERNS),
        "hardline_c": list(approval.HARDLINE_PATTERNS_COMPILED),
        "aliases": dict(approval._PATTERN_KEY_ALIASES),
    }
    monkeypatch.delenv(desktop_guard.STRATEGY_ENV, raising=False)
    desktop_guard._installed = False
    desktop_guard._installed_strategy = None
    yield desktop_guard
    approval.DANGEROUS_PATTERNS[:] = snap["dangerous"]
    approval.DANGEROUS_PATTERNS_COMPILED[:] = snap["dangerous_c"]
    approval.HARDLINE_PATTERNS[:] = snap["hardline"]
    approval.HARDLINE_PATTERNS_COMPILED[:] = snap["hardline_c"]
    approval._PATTERN_KEY_ALIASES.clear()
    approval._PATTERN_KEY_ALIASES.update(snap["aliases"])
    desktop_guard._installed = False
    desktop_guard._installed_strategy = None


def _dangerous_keys():
    return [desc for _, desc in approval.DANGEROUS_PATTERNS_COMPILED]


def _hardline_keys():
    return [desc for _, desc in approval.HARDLINE_PATTERNS_COMPILED]


# ── Strategy selection ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", "approval"),
        ("approval", "approval"),
        ("APPROVAL", "approval"),
        (" hard ", "hard"),
        ("block", "hard"),
        ("off", "off"),
        ("none", "off"),
        ("nonsense", "approval"),
        (None, "approval"),
    ],
)
def test_normalize_strategy(raw, expected):
    assert desktop_guard.normalize_strategy(raw) == expected


def test_approval_strategy_flags_dangerous_only(guard, monkeypatch):
    monkeypatch.setenv(desktop_guard.STRATEGY_ENV, "approval")
    guard.install()

    key = "browser desktop CDP/VNC endpoint access"
    assert key in _dangerous_keys()
    assert key not in _hardline_keys()

    is_dangerous, pattern_key, description = approval.detect_dangerous_command(
        ENDPOINT_CMD
    )
    assert is_dangerous is True
    assert description == key
    assert pattern_key == key

    # Approval tier stays approvable: not a hardline block.
    assert approval.detect_hardline_command(ENDPOINT_CMD) == (False, None)


def test_hard_strategy_blocks_only(guard, monkeypatch):
    monkeypatch.setenv(desktop_guard.STRATEGY_ENV, "hard")
    guard.install()

    key = "browser desktop CDP/VNC endpoint access"
    assert key in _hardline_keys()
    assert key not in _dangerous_keys()

    is_hardline, description = approval.detect_hardline_command(ENDPOINT_CMD)
    assert is_hardline is True
    assert description == key


def test_off_strategy_appends_nothing(guard, monkeypatch):
    monkeypatch.setenv(desktop_guard.STRATEGY_ENV, "off")
    before_d = len(approval.DANGEROUS_PATTERNS)
    before_dc = len(approval.DANGEROUS_PATTERNS_COMPILED)
    before_h = len(approval.HARDLINE_PATTERNS)
    before_hc = len(approval.HARDLINE_PATTERNS_COMPILED)

    guard.install()

    assert len(approval.DANGEROUS_PATTERNS) == before_d
    assert len(approval.DANGEROUS_PATTERNS_COMPILED) == before_dc
    assert len(approval.HARDLINE_PATTERNS) == before_h
    assert len(approval.HARDLINE_PATTERNS_COMPILED) == before_hc
    assert approval.detect_dangerous_command(ENDPOINT_CMD)[0] is False
    assert approval.detect_hardline_command(ENDPOINT_CMD) == (False, None)
    assert guard._installed_strategy == "off"


def test_default_strategy_is_approval(guard, monkeypatch):
    """No env var → approval (never silently open, never bricked)."""
    monkeypatch.delenv(desktop_guard.STRATEGY_ENV, raising=False)
    guard.install()
    assert guard._installed_strategy == "approval"
    assert "browser desktop CDP/VNC endpoint access" in _dangerous_keys()


def test_install_is_idempotent(guard, monkeypatch):
    monkeypatch.setenv(desktop_guard.STRATEGY_ENV, "approval")
    guard.install()
    d1 = len(approval.DANGEROUS_PATTERNS_COMPILED)
    guard.install()  # marker set — no second append
    assert len(approval.DANGEROUS_PATTERNS_COMPILED) == d1

    # Even with the marker reset (e.g. re-init in tests), descriptions
    # dedupe so patterns never pile up.
    guard._installed = False
    guard.install()
    assert len(approval.DANGEROUS_PATTERNS_COMPILED) == d1


def test_detection_covers_all_four_patterns(guard, monkeypatch):
    monkeypatch.setenv(desktop_guard.STRATEGY_ENV, "hard")
    guard.install()

    # Every pattern fires in hardline detection, and each has its own key.
    cases = {
        "curl http://127.0.0.1:6900/json/version": (
            "browser desktop CDP/VNC endpoint access"
        ),
        # 0.0.0.0 connects to the local host on Linux — same reach.
        "curl http://0.0.0.0:6900/json/version": (
            "browser desktop CDP/VNC endpoint access"
        ),
        # Inline scripts carrying the endpoint: heredoc / python -c —
        # the whole body is part of the command string, so the endpoint
        # pattern sees it (browser-use, playwright connect_over_cdp,
        # raw websocket clients all land here).
        "python - <<'EOF'\nfrom browser_use import Browser\nb = Browser(cdp_url='http://localhost:6900')\nEOF": (
            "browser desktop CDP/VNC endpoint access"
        ),
        "python -c \"import websocket; websocket.create_connection('ws://127.0.0.1:6900/devtools/page/1')\"": (
            "browser desktop CDP/VNC endpoint access"
        ),
        "agent-browser --cdp http://evil open https://x": (
            "explicit --cdp endpoint override"
        ),
        "env DISPLAY=:99 firefox https://x": "binding to an X display",
        "x11vnc -display :99": "browser desktop X/VNC tooling",
        "Xvfb :99 &": "browser desktop X/VNC tooling",
    }
    for command, expected_key in cases.items():
        is_hardline, description = approval.detect_hardline_command(command)
        assert is_hardline is True, command
        assert description == expected_key, command


def test_benign_commands_never_match(guard, monkeypatch):
    """No false positives from the guard patterns themselves.

    Checked against the compiled guard patterns directly so shipped SDK
    patterns cannot mask or fake a result.
    """
    compiled = guard.guard_patterns()
    for command in BENIGN_COMMANDS:
        lowered = command.lower()
        for pattern_re, description in compiled:
            assert not pattern_re.search(lowered), (command, description)


def test_endpoint_pattern_requires_host():
    """A bare port number in prose is not a host:port endpoint token."""
    endpoint_re = re.compile(
        desktop_guard._GUARD_PATTERNS[0][1], desktop_guard._RE_FLAGS
    )
    assert endpoint_re.search("echo port 6900 is fine") is None
    assert endpoint_re.search("echo http://127.0.0.1:6900") is not None


# ── agent-browser wrapper (pinned override, unpinned pass-through) ───────────
#
# Policy under test: the wrapper rewrites arguments ONLY when the
# session's display config exists (pinned). Unpinned sessions get pure
# pass-through — the strategy env var is deliberately not read here;
# unpinned desktop access is gated by the terminal patterns above.


def _run_wrapper(tmp_path, args, session_key=None, json_body=None, extra_env=None):
    """Run the wrapper with a stubbed agent-browser; return its stdout.

    json_body: dict written as the session's display config (creates the
    file — i.e. "pinned"); None leaves the config absent (unpinned).
    """
    home = tmp_path / "home"
    (home / ".hermes" / "webui" / "sessions_display").mkdir(
        parents=True, exist_ok=True
    )
    if json_body is not None:
        (home / ".hermes" / "webui" / "sessions_display" / "sessA.json").write_text(
            json.dumps(json_body)
        )

    # Stub agent-browser that echoes its argv.
    stub = tmp_path / "ab-stub"
    stub.write_text("#!/usr/bin/env bash\necho \"ARGS:$*\"\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    # Shim `find` so the wrapper's real-binary discovery lands on the stub
    # (the wrapper only greps the file for a wrapper-copy fingerprint,
    # which the stub does not contain).
    shim = tmp_path / "shim"
    shim.mkdir()
    find_shim = shim / "find"
    find_shim.write_text(f'#!/usr/bin/env bash\necho "{stub}"\n')
    find_shim.chmod(find_shim.stat().st_mode | stat.S_IEXEC)

    env = {
        "PATH": f"{shim}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
    }
    if session_key:
        env["HERMES_SESSION_KEY"] = session_key
    if extra_env:
        env.update(extra_env)

    out = subprocess.run(
        ["bash", str(WRAPPER), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


_PINNED_JSON = {
    "display": 99,
    "vnc_port": 5900,
    "cdp_port": 6900,
    "cdp_url": "http://127.0.0.1:6900",
}


def test_wrapper_pinned_strips_and_injects_own_endpoint(tmp_path):
    """Pinned (JSON present): only this session's endpoint survives."""
    stdout = _run_wrapper(
        tmp_path,
        ["--cdp", "http://127.0.0.1:6901", "--session", "other", "open", "https://x"],
        session_key="agent:main:webui:dm:sessA",
        json_body=_PINNED_JSON,
    )
    assert "--cdp http://127.0.0.1:6900" in stdout
    assert "6901" not in stdout
    assert "--session" not in stdout


def test_wrapper_pinned_strips_equals_forms(tmp_path):
    """Inline --cdp=URL / --session=NAME forms are overridden too."""
    stdout = _run_wrapper(
        tmp_path,
        ["--cdp=http://127.0.0.1:6901", "--session=other", "tabs"],
        session_key="agent:main:webui:dm:sessA",
        json_body=_PINNED_JSON,
    )
    assert "--cdp=http://127.0.0.1:6901" not in stdout
    assert "--session=other" not in stdout
    assert "--cdp http://127.0.0.1:6900" in stdout
    assert "tabs" in stdout


def test_wrapper_pinned_broken_json_strips_without_inject(tmp_path):
    """Config exists but has no cdp_url: still strip, inject nothing."""
    stdout = _run_wrapper(
        tmp_path,
        ["--cdp", "http://127.0.0.1:6901", "open", "https://x"],
        session_key="agent:main:webui:dm:sessA",
        json_body={"display": 99},
    )
    assert "--cdp" not in stdout
    assert "6901" not in stdout
    assert "open https://x" in stdout


def test_wrapper_unpinned_passes_cdp_through(tmp_path):
    """Unpinned (no JSON): pure pass-through — nothing stripped."""
    stdout = _run_wrapper(
        tmp_path,
        ["--cdp", "http://127.0.0.1:6901", "--session", "other", "open", "https://x"],
        session_key="agent:main:webui:dm:sessA",
        json_body=None,
    )
    assert "--cdp http://127.0.0.1:6901" in stdout
    assert "--session other" in stdout
    assert "open https://x" in stdout


def test_wrapper_unpinned_passes_equals_forms_through(tmp_path):
    """Inline forms pass through untouched when unpinned."""
    stdout = _run_wrapper(
        tmp_path,
        ["--cdp=http://127.0.0.1:6901", "--session=other", "tabs"],
        session_key=None,
        json_body=None,
    )
    assert "--cdp=http://127.0.0.1:6901" in stdout
    assert "--session=other" in stdout
    assert "tabs" in stdout


def test_wrapper_unpinned_ignores_global_env(tmp_path):
    """A user-set global BROWSER_CDP_URL no longer gets injected.

    Deliberate behavior change with the JSON-only policy: the wrapper's
    single exception is the session display config. The global env is
    still honored by the in-process SDK tools (CLI /browser connect),
    just not rewritten onto agent-browser argv here.
    """
    stdout = _run_wrapper(
        tmp_path,
        ["tabs"],
        session_key="agent:main:webui:dm:sessA",
        json_body=None,
        extra_env={"BROWSER_CDP_URL": "http://127.0.0.1:6902"},
    )
    assert "--cdp" not in stdout
    assert "6902" not in stdout
    assert "tabs" in stdout
