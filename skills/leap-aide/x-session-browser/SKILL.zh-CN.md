---
name: x-session-browser
description: 所有浏览器相关任务（访问网址、打开页面、点击元素、登录网站、查账户页等）必须通过本技能驱动 Pinned Session 中可见的 Chrome 完成。
version: 1.0.0
author: LeapAide
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [browser, cdp, observer, pinned-session, leap-aide]
---

<div align="center">

[English](SKILL.md) | **中文**

</div>

# x-session-browser 技能

每个 Pinned Session 绑定一个独立且彼此隔离的浏览器桌面（Xvfb + x11vnc + 一个带独立 Profile 的 Chrome）。浏览器桌面由 LeapAide 掌管，在 Session 被 Pin 时启动并初始化。

你的职责是通过 Chrome CDP 驱动浏览器桌面中**既有**的 Chrome 实例完成浏览器任务，让用户在观察窗口中实时看到操作过程。任何情况下都不要自己额外拉起一个新的 Chrome 实例。

Pinned Session 中一切浏览器相关任务都**必须**经由本技能完成——访问网址、打开页面、点击元素、填写表单、登录网站、读取渲染后的页面内容，一律如此。绝不用自己的自动化代码替代（Python + Selenium/Playwright/pyautogui/Xlib、shell 脚本、裸 CDP 客户端……）：那样会绕过用户可见的 Chrome，用户看不到过程，属于禁止行为。

本技能不是通用浏览器自动化，也不适用于未 Pin 的 Session。

## 使用时机

只要出现浏览器相关任务就使用本技能，无论它是不是更大任务的一个子步骤：访问 http/https 网址、打开 Web 页面、点击页面元素、填写表单、登录网站、查看账户/余额页面、读取渲染后的页面内容。在 Pinned Session 中，这些必须通过本技能驱动既有 Chrome 完成；自己写自动化代码替代属于禁止行为。

以下场景禁止使用：

- 未 Pin 的会话 —— 只有 Pinned Session 才会创建并初始化浏览器桌面；那里适用的严格规则见“普通 Session”一节。
- 不需要浏览器的任务。

## 前置条件

**如何判定当前会话是否 Pinned（每次都要验证，绝不假设）：**

1. 取 session_id = `HERMES_SESSION_KEY` 的最后一段（webui 格式为 `agent:main:webui:dm:<session_id>`）。注意：格式本身不说明任何问题——每个 webui 会话都是这个格式，无论是否 Pin。
2. 检查 `$HERMES_WEBUI_STATE_DIR/sessions_display/<session_id>.json` 是否存在（`HERMES_WEBUI_STATE_DIR` 默认为 `~/.hermes/webui`）。该文件在会话被 Pin 时写入、Unpin 时删除——它是唯一的事实来源。

判定：

- 配置文件存在 → Pinned Session，使用本技能的 CDP 通道；端点就是该文件的 `cdp_url` 字段。
- 配置文件不存在 → 普通 Session，按下方“普通 Session”一节执行。绝不因为任务是浏览器任务、或本技能被显式调用，就推断“已 Pin”。

健康的 Pinned 会话配置：

```
$ cat $HERMES_WEBUI_STATE_DIR/sessions_display/<session_id>.json
{"display": 99, "vnc_port": 5900, "cdp_port": 6900, "cdp_url": "http://127.0.0.1:6900", ...}
```

## 工作方式

- 优先使用 Hermes Agent SDK 内置 browser tools（`browser_navigate`、`browser_snapshot`、`browser_click` 等）。它们从会话 display 配置解析本会话的 CDP 端点，自动附着到浏览器桌面的 Chrome，无需启动新 Chrome。
- `agent-browser` CLI 已由 LeapAide 安装的 wrapper 接管（位于 `/usr/local/bin/agent-browser`）：在 Pinned Session 中，你传入的 `--cdp`、`--session` 会被剥离，`--cdp` 会被替换为本会话浏览器桌面的端点；在普通 Session 中，wrapper 原样透传你的参数。
- Chrome 二进制同样被 wrapper 接管，传入的 `--headless` 及其变体会被剥离。wrapper 会从浏览器桌面配置读取 `DISPLAY` 并注入，将 Chrome 绑定到对应桌面。若会话没有浏览器桌面，wrapper 透传参数，行为与原生一致。


总之：wrapper 保证 Pinned Session 中所有浏览器调用都附着到浏览器桌面上的那一个 Chrome。你只需发起调用，连接目标不受你控制，也不需要你控制。

## 快速参考

| 资源 | 值 |
|---|---|
| Display 池 | `:99` … `:90`（10 个槽位，每个数字对应一个浏览器桌面，通过 `export DISPLAY=<slot num>` 将进程绑定到指定桌面） |
| VNC 端口 | `5900` … `5909`（浏览器桌面监听 VNC Client 连接的端口） |
| CDP 端口 | VNC + 1000 → `6900` … `6909`（Chrome 暴露的 CDP 控制端口，用以驱动 Chrome 操作） |
| 会话配置 | `$HERMES_WEBUI_STATE_DIR/sessions_display/<session_id>.json` |
| Chrome Profile | `$HERMES_WEBUI_STATE_DIR/chrome_profiles/<session_id>` |
| Chrome 窗口 | 960×1080，位置 (0,0) —— 1920×1080 桌面的左半屏；右半屏显示终端活动 |
| CDP 端点 | 会话配置 JSON 的 `cdp_url` 字段（`http://127.0.0.1:<cdp_port>`） |
| 会话键 | `HERMES_SESSION_KEY=agent:main:webui:dm:<session_id>` |

会话配置示例：

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

## 操作步骤

### Pinned Session

1. 先按“前置条件”做判定。只有判定为 Pinned（会话配置文件存在）才走本节；判定为普通 Session 就转到“普通 Session”一节——绝不假设会话已 Pin。
2. 通过内置 browser tools 和 wrapper 接管的 `agent-browser` 驱动**既有** Chrome 完成浏览器任务，遵循以下要求：
   - 复用已打开的 tab 和已登录的 Profile。用户可能已通过观察窗口手动操作过 Chrome，不要关闭用户打开的 tab。
   - 任何情况下都不要拉起第二个 Chrome——Chrome 只能由 LeapAide 启动。
   - 若 CDP 调用报连接错误，Chrome 可能已崩溃，LeapAide 会在同一 display 和端口上重启它。稍等片刻并重试一次，然后再报告失败。
   - 你的回合结束后，LeapAide 可能自动关闭你的工具打开的多余 tab。如果 tab 状态对任务重要，把多步工作安排在同一回合内完成。

### 普通 Session

本技能的浏览器桌面通道仅适用于 Pinned Session。在未 Pin 的会话中，只需遵守一条硬规则：桌面资源一律禁用。

- 附着或绑定任何 X display——宿主桌面（`DISPLAY=:0` 等）和浏览器桌面池（`:90`–`:99`）都不行——也不要自己启动 Xvfb/X server。
- 不得触碰浏览器桌面基础设施——VNC 端口（`5900`–`5909`）、CDP 端口（`6900`–`6909`）、Xvfb/x11vnc、会话 display 配置、Chrome Profile。

这条规则由 LeapAide 后端强制执行，不只是本技能的文字约定：按 `BROWSER_DESKTOP_BLOCK_STRATEGY` 的取值，触碰这些资源的终端命令会被直接拒绝、转交用户审批，或（`off` 档下）不设拦截。不要尝试——默认一定会被拦下。

除此之外没有需要维持的观察契约：网页任务可以自行选择方式——原生 browser tools（会话没有浏览器桌面时会自动落到本地 headless Chromium）、自己拉起 Chrome/agent-browser、或 CDP 脚本都行。那里不存在用户可见的桌面，也就没有"保持可见"的义务。

## 常见陷阱

- **自己写自动化代码**：在 Pinned Session 中，Python/Selenium/Playwright/pyautogui 脚本会绕过会话可见的 Chrome 和观察契约；Pinned 的浏览器工作都走本技能的 CDP 通道。
- **在未 Pin 的会话中附着桌面**：未 Pin 会话不拥有任何桌面；宿主 display 和其他会话的浏览器桌面都绝不属于你，不得触碰。
- **自己拉起 Chrome**：会绕开会话的 Profile 和浏览器桌面，与环境产生冲突。始终附着，绝不拉起。
- **给 agent-browser 传 `--cdp`**：在 Pinned Session 中，指向非浏览器桌面的端点会被剥离并覆盖，不受你控制；在普通 Session 中会透传，但终端策略仍可能拦截该命令。
- **给 agent-browser 传 `--session`**：在 Pinned Session 中与浏览器桌面冲突，会被 wrapper 剥离，不受你控制。
- **给 Chrome 传 `--headless`**：破坏观察契约，会被 wrapper 剥离。
- **关闭用户 tab 或遮挡桌面视图**：桌面是用户观察任务的窗口，必须保持可用。