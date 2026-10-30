---
name: x-session-browser
description: All browser/web tasks must use this skill's pinned Chrome.
version: 1.0.0
author: LeapAide
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [browser, cdp, observer, pinned-session, leap-aide]
---

<div align="center">

**English** | [中文](SKILL.zh-CN.md)

</div>

# x-session-browser Skill

Every Pinned Session is bound to its own isolated browser desktop (Xvfb + x11vnc + one Chrome with an isolated profile). LeapAide owns the browser desktop and boots and initializes it when the session is pinned.

Your job is to complete all browser tasks by driving the existing Chrome instance on the browser desktop through Chrome CDP, so the user can watch the process live in the observer window. Never launch an additional Chrome instance yourself.

Every browser-related task in a Pinned Session MUST go through this skill — visiting URLs, opening pages, clicking, filling forms, logging in, reading what a rendered page shows. Never substitute your own automation code (Python with Selenium/Playwright/pyautogui/Xlib, shell scripts, raw CDP clients, ...): it bypasses the user-visible Chrome, is invisible to the user, and is forbidden.

This skill is not generic browser automation and does not apply to unpinned sessions.

## When to Use

Use this skill for ANY browser-related task, whenever one appears — including as a sub-step of a larger task: visiting an http/https URL, opening a web page, clicking page elements, filling forms, logging into a site, checking an account/balance page, or reading what a rendered page shows. In a Pinned Session these MUST be completed by driving the existing Chrome via this skill; writing your own automation code instead is forbidden.

Don't use for:

- Unpinned sessions — only pinned sessions get a browser desktop created and initialized; see Regular Session for the strict rules that apply there.
- Tasks that do not need a browser.

## Prerequisites

**How to tell whether the current session is Pinned (verify every time, never assume):**

1. Take session_id = the last segment of `HERMES_SESSION_KEY` (webui format `agent:main:webui:dm:<session_id>`). Note: the format alone proves nothing — every webui session has it, pinned or not.
2. Check whether `$HERMES_WEBUI_STATE_DIR/sessions_display/<session_id>.json` exists (`HERMES_WEBUI_STATE_DIR` defaults to `~/.hermes/webui`). The file is written when the session is pinned and deleted when it is unpinned — it is the single source of truth.

Verdict:

- Config file exists → Pinned Session; use this skill's CDP path. The endpoint is the `cdp_url` field of that file.
- Config file absent → Regular Session; follow the Regular Session section below. Never infer "pinned" from the task being a browser task or from this skill being explicitly invoked.

Healthy pinned session config:

```
$ cat $HERMES_WEBUI_STATE_DIR/sessions_display/<session_id>.json
{"display": 99, "vnc_port": 5900, "cdp_port": 6900, "cdp_url": "http://127.0.0.1:6900", ...}
```

## How to Run

- Prefer the Hermes Agent SDK's native browser tools (`browser_navigate`, `browser_snapshot`, `browser_click`, ...). They resolve the session's CDP endpoint from the session display config and attach automatically to the browser desktop's Chrome — no new Chrome needed.
- The `agent-browser` CLI is taken over by a LeapAide-installed wrapper (at `/usr/local/bin/agent-browser`): in a Pinned Session, any `--cdp` or `--session` you pass is stripped and `--cdp` is replaced with this session's browser desktop endpoint; in a Regular Session the wrapper passes your arguments through unchanged.
- The Chrome binary is likewise taken over by a wrapper; `--headless` and its variants are stripped. The wrapper reads `DISPLAY` from the browser desktop config and injects it, binding Chrome to the corresponding desktop. If the session has no browser desktop, the wrapper passes arguments through and behavior matches native Chrome.

In short: the wrappers guarantee that every browser call in a pinned session attaches to the one Chrome on the browser desktop. You only need to issue the calls; the connection target is not yours to control — and does not need to be.

## Quick Reference

| Resource | Value |
|---|---|
| Display pool | `:99` … `:90` (10 slots, one per browser desktop; `export DISPLAY=<slot num>` binds a process to that desktop) |
| VNC ports | `5900` … `5909` (ports the browser desktop listens on for VNC clients) |
| CDP ports | VNC + 1000 → `6900` … `6909` (Chrome's CDP control port, used to drive Chrome) |
| Session config | `$HERMES_WEBUI_STATE_DIR/sessions_display/<session_id>.json` |
| Chrome profile | `$HERMES_WEBUI_STATE_DIR/chrome_profiles/<session_id>` |
| Chrome window | 960×1080 at position (0,0) — left half of a 1920×1080 desktop; right half shows terminal activity |
| CDP endpoint | `cdp_url` field of the session config JSON (`http://127.0.0.1:<cdp_port>`) |
| Session key | `HERMES_SESSION_KEY=agent:main:webui:dm:<session_id>` |

Example session config:

```json
{
  "display": 99,
  "vnc_port": 5900,
  "cdp_port": 6900,
  "cdp_url": "http://127.0.0.1:6900",
  "started_at": 1730000000.0,
  "last_activity": 1730000000.0
}
```

## Procedure

### Pinned Session

1. Run the Prerequisites verdict first. You are only in a Pinned Session when it says so (an existing session config file); a Regular Session verdict sends you to the Regular Session section — never assume the session is pinned.
2. Drive the **existing** Chrome through the native browser tools and the wrapper-managed `agent-browser`, following these rules:
   - Reuse open tabs and the logged-in profile. The user may have operated Chrome manually through the observer window; do not close tabs the user opened.
   - Never launch a second Chrome — only LeapAide may start Chrome.
   - If a CDP call fails with a connection error, Chrome may have crashed; LeapAide restarts it on the same display and port. Wait briefly and retry once, then report the failure.
   - After your turn ends, LeapAide may auto-close extra tabs your tools opened. If tab state matters to the task, plan multi-step work inside one turn.

### Regular Session

This skill's browser-desktop path applies only to Pinned Sessions. In an unpinned session, keep to one hard rule: desktop resources are OFF LIMITS.

- Never attach to or bind any X display — neither the host display (`DISPLAY=:0` and the like) nor the browser-desktop pool (`:90`–`:99`) — and never start your own Xvfb/X server.
- Never touch browser-desktop infrastructure — VNC ports (`5900`–`5909`), CDP endpoints (`6900`–`6909`), Xvfb/x11vnc, session display configs, or Chrome profiles.

This is enforced by the LeapAide backend, not just by this text: per `BROWSER_DESKTOP_BLOCK_STRATEGY`, terminal commands that reach for those resources are blocked outright, routed to the user for approval, or (under `off`) left ungated. Do not attempt them — they will be caught.

Beyond that there is no observer contract to keep: complete web tasks however you see fit — the native browser tools (they fall back to a local headless Chromium when the session has no browser desktop), your own Chrome or agent-browser, or CDP scripts. There is no user-visible desktop here, so nothing needs to stay visible.

## Pitfalls

- **Writing your own automation code**: in a Pinned Session, Python/Selenium/Playwright/pyautogui scripts bypass the session's visible Chrome and the observer contract; pinned browser work goes through this skill's CDP path.
- **Attaching to a desktop from an unpinned session**: unpinned sessions own no desktop; the host display and other sessions' browser desktops are never yours to touch.
- **Launching Chrome yourself**: bypasses the session's profile and browser desktop and conflicts with the environment. Always attach, never launch.
- **Passing `--cdp` to agent-browser**: in a Pinned Session an endpoint that does not belong to the browser desktop is stripped and overridden — not in your control; in a Regular Session it passes through, but the terminal strategy may still gate the command.
- **Passing `--session` to agent-browser**: in a Pinned Session it conflicts with the browser desktop and is stripped by the wrapper — not in your control.
- **Passing `--headless` to Chrome**: breaks the observer contract and is stripped by the wrapper.
- **Closing user tabs or covering the desktop view**: the desktop is the user's window into the task; keep it usable.
