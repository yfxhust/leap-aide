#!/usr/bin/env bash
# ==============================================================================
# agent-browser wrapper
# ==============================================================================
#
# Installed at /usr/local/bin/agent-browser (earlier in PATH than npx).
# Simply resolves to the real agent-browser binary and exec's it.
#
# ── CDP URL resolution (SessionDisplay integration) ────────────────────────
#
# Policy: pure pass-through, with ONE exception — when this session's
# display config exists (pinned session). A pinned session's browser
# desktop is the only legitimate attach target, so the wrapper strips
# any caller-supplied --cdp / --session and injects the config's
# cdp_url. Everything else is deliberately none of the wrapper's
# business: whether an unpinned session may touch desktop resources at
# all is gated by the terminal approval patterns
# (app.compat.desktop_guard / BROWSER_DESKTOP_BLOCK_STRATEGY), not by
# argument rewriting here. The wrapper does not read that variable —
# under strategy=off an unpinned session's --cdp really does go
# through, which is the point of "fully open".
#
# The single source of truth for a pinned session's CDP endpoint is its
# display config, written by SessionDisplay at pin time and deleted at
# unpin:
#   ~/.hermes/webui/sessions_display/<session_id>.json
#
# LeapAide never injects BROWSER_CDP_URL into the child env — this wrapper
# resolves the endpoint straight from the session's own JSON via
# HERMES_SESSION_KEY (injected per-session by _IsolatedPopen), so there
# is no env copy that can race or go stale across concurrent sessions.

set -euo pipefail

# ── Session-display JSON resolution ────────────────────────────────────────
SESSIONS_DIR="${HOME}/.hermes/webui/sessions_display"
# Extract session_id from HERMES_SESSION_KEY (strip "agent:main:webui:dm:"
# prefix; a bare session id passes through unchanged).
SID="${HERMES_SESSION_KEY:-}"
SID="${SID##*:}"

# PINNED=true (display config exists) triggers the strip-and-inject
# override below; FINAL_CDP is the endpoint to inject (empty when the
# config has no cdp_url — strip still applies, nothing is injected).
PINNED=false
FINAL_CDP=""
if [[ -n "$SID" ]]; then
    JSON_FILE="${SESSIONS_DIR}/${SID}.json"
    if [[ -f "$JSON_FILE" ]]; then
        PINNED=true
        FINAL_CDP=$(python3 -c "import json; print(json.load(open('$JSON_FILE')).get('cdp_url', ''))" 2>/dev/null || echo "")
    fi
fi

if [[ "$PINNED" == "true" ]]; then
    # Rebuild argument list: strip any caller-supplied --cdp / --session
    # (the session's desktop is the only attach target), then inject the
    # config's endpoint when present.
    # (--cdp takes priority over --session in agent-browser >= 0.13)
    NEW_ARGS=()
    SKIP_NEXT=false
    for arg in "$@"; do
        if [[ "$SKIP_NEXT" == "true" ]]; then
            SKIP_NEXT=false
            continue
        fi
        case "$arg" in
            --cdp)
                SKIP_NEXT=true    # skip --cdp and its value
                ;;
            --cdp=*)
                ;;               # delete existing --cdp=...
            --session)
                SKIP_NEXT=true    # skip --session and its value
                ;;
            --session=*)
                ;;               # delete existing --session=...
            *)
                NEW_ARGS+=("$arg")
                ;;
        esac
    done
    if [[ -n "$FINAL_CDP" ]]; then
        NEW_ARGS+=("--cdp" "$FINAL_CDP")
    fi
    if ((${#NEW_ARGS[@]})); then
        set -- "${NEW_ARGS[@]}"
    fi
fi

# ── Recursion guard ──────────────────────────────────────────────────────────
# If already running through this wrapper, go directly to npx
# with a clean PATH to prevent infinite wrapper → npx → wrapper loops
if [[ -n "${HERMES_AB_WRAPPER_GUARD:-}" ]]; then
    _clean_path=""
    IFS=':' read -ra _path_arr <<< "$PATH"
    for _p in "${_path_arr[@]}"; do
        [[ "$_p" != "/usr/local/bin" ]] && _clean_path="${_clean_path}${_clean_path:+:}$_p"
    done
    PATH="$_clean_path" exec npx agent-browser "$@"
fi
export HERMES_AB_WRAPPER_GUARD=1

REAL_AB=""
for candidate in $(find /root/.npm/_npx -maxdepth 6 \( -type f -o -type l \) -name 'agent-browser' 2>/dev/null | head -5); do
    if head -c 100 "$candidate" 2>/dev/null | grep -q 'HERMES_SESSION_KEY'; then
        continue  # skip wrapper copies
    fi
    REAL_AB="$candidate"
    break
done

if [[ -z "$REAL_AB" ]]; then
    # Strip /usr/local/bin from PATH so npx doesn't invoke this wrapper again
    _clean_path=""
    IFS=':' read -ra _path_arr <<< "$PATH"
    for _p in "${_path_arr[@]}"; do
        [[ "$_p" != "/usr/local/bin" ]] && _clean_path="${_clean_path}${_clean_path:+:}$_p"
    done
    PATH="$_clean_path" exec npx agent-browser "$@"
fi

exec "$REAL_AB" "$@"
