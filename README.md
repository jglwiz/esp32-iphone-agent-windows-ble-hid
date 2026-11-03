# ESP32 iPhone Agent for Windows — BLE HID

English | [简体中文](README_CN.md)

Enable an AI agent on Windows to observe and operate an iPhone using ESP32-S3 BLE HID and AirPlay.

Project slug: `esp32-iphone-agent-windows-ble-hid`. The current implementation provides a serial-to-BLE keyboard/mouse bridge and agent-operated hardware tests; the backend includes AirPlay capture and process management.

**Control path: PC → USB serial → ESP32-S3 → BLE HID → iPhone.** The board stays connected to the PC by USB; the iPhone receives input over Bluetooth. AirPlay can provide separate visual feedback. BLE HID does not return screen images.

## Status

For a fresh Windows deployment of the control API and AirPlay capture, follow the [AI-assisted deployment guide](doc/user/deployment.md) (Chinese). It includes backend setup, first-run configuration, phone steps, and read-only acceptance checks.

Firmware **0.4.1** adds a separate button-only report and the Backend `click()` helper, without sending coordinates. Its device name is `S3-Click-Buttons`. Current-device click events passed; movement depends on phone orientation. Follow the placement guidance below. Automatic orientation compensation is not implemented, and coordinate APIs are not claimed stable across arbitrary orientations. See [Backend usage](backend/doc/usage.md#原地点击) and [button design](firmware/serial_ble_hid/doc/mouse-buttons.md) (Chinese). Earlier validation below remains historical.

Firmware 0.2.0 has been tested on a Waveshare ESP32-S3 and an iPhone 17e with keyboard input, Backspace, mouse movement, clicks, dragging, and vertical scrolling in both directions. Full automatic reconnection, lock-screen recovery, long-term stability, and coordinate calibration remain unverified or incomplete. Text output depends on the iPhone's hardware keyboard input method: a Chinese input method converted ASCII underscores into long dashes during testing. See the [validation record](doc/dev/validation.md) (Chinese) for evidence and limitations.

The primary tested environment is Windows with a CH343 serial interface and a board with 16 MB Flash / 8 MB PSRAM. Other ESP32 variants, operating systems, and iOS versions have not been comprehensively tested.

## Usage note: phone orientation

Keep the iPhone upright in portrait, with the earpiece/front-camera end at the top and a stable tilt, and enable Portrait Orientation Lock in Control Center. Avoid upside-down or flat placement, and keep its orientation unchanged during operation. Inverted or rotated placement may cause direction mismatches, such as a rightward command moving the pointer downward. A portrait AirPlay image does not establish the phone's physical orientation.

On 2026-11-03, after the user locked orientation, a movement-only retest showed correct alignment at three asymmetric target points and correct directions on both relative axes. This supports requiring orientation lock for current use; controlled lock-on/off comparisons and long-term testing remain incomplete. See the [retest record](backend/doc/direction-root-cause.md#2026-11-03方向锁定后复测) (Chinese).

For now, the user must maintain the correct placement; the backend does not automatically detect or compensate for orientation. After changing placement, make small movements without clicking in a safe blank area, and use fresh screenshots to confirm direction and actual position before clicking. Stop positioning if the response is wrong. The in-place `click()` only clicks at the actual pointer position; it does not correct previous movement or placement. Upright placement does not replace this check. See the historical [direction investigation](backend/doc/direction-root-cause.md) (Chinese).

## iPhone skill: repository and user scope

The repository includes an [iphone skill](.agents/skills/iphone/SKILL.md) at `.agents/skills/iphone/`. Open this repository in Codex with support for this discovery directory and invoke `$iphone`. Reload or restart the client if it is missing from the selector. Example request:

> Use this repository's $iphone skill to deploy the environment following doc/user/deployment.md, run read-only acceptance checks, and report required phone steps and unverified behavior.

No skill installer is needed. To use it from other projects, run this PowerShell from the repository root to create a user-scoped link:

```powershell
$iphoneRepo = (Get-Location).Path
$iphoneSkills = Join-Path $HOME '.agents/skills'
$iphoneTarget = Join-Path $iphoneSkills 'iphone'
if (Test-Path -LiteralPath $iphoneTarget) { throw 'Target already exists. Choose the intended skill before replacing anything.' }
New-Item -ItemType Directory -Path $iphoneSkills -Force | Out-Null
New-Item -ItemType Junction -Path $iphoneTarget -Target (Join-Path $iphoneRepo '.agents/skills/iphone')
```

Alternatively replace the last line with `Copy-Item -LiteralPath (Join-Path $iphoneRepo '.agents/skills/iphone') -Destination $iphoneTarget -Recurse` to create an independent copy. A link follows its source checkout: pulling, switching branches, or editing through the link affects the global skill. Use a separate stable checkout if you frequently switch branches. A copy requires manual updates and backend API compatibility checks.

The skill recognizes two optional environment variables in the agent/client workflow; these are not backend deployment configuration fields:

```powershell
$env:IPHONE_AGENT_REPO = $iphoneRepo
$env:IPHONE_AGENT_BASE_URL = 'http://127.0.0.1:8892'
```

Explicit task values take precedence over environment variables. Repository and linked skills can locate the checkout from the skill's resolved location. A global copy needs an explicit repository path for deployment, startup, or the repository client. Health and screenshot HTTP requests do not need source files. These variables apply to this terminal and its child processes; provide the path and URL in the chat if an existing GUI has not inherited them.

If `~/.codex/skills/iphone`, `~/.agents/skills/iphone`, or another same-name entry exists, choose the intended entry manually. Same-name skills are not merged, and repository scope should not be assumed to override user scope. Both may be discovered; identify the intended path when invoking one.

Version the skill with backend code and update affected skill content alongside API changes. Commit public skill resources; keep `backend/.deployment.json`, virtual environments, runtime screenshots, and logs ignored. Never embed management tokens in the skill. Normal operation reuses the backend HTTP API without opening another serial connection.

## Build firmware from a fresh clone

Requirements: 64-bit Windows, PowerShell 5.1 or later, Git, an internet connection, and several GB of free disk space. Arduino IDE and a global Arduino CLI installation are not required. Python is only needed for the PC client below.

```powershell
git clone https://github.com/jglwiz/esp32-iphone-agent-windows-ble-hid.git
cd esp32-iphone-agent-windows-ble-hid
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

The first run installs Arduino CLI **1.5.1** and ESP32 core **3.3.12** inside `.tools/`, downloads NimBLE-Arduino **2.3.8** into `.build-deps/`, and compiles into `.build/`. CLI and NimBLE archives are SHA-256 checked. Later runs reuse installed tools and downloads. No parent workspace or global Arduino libraries are used. The default board configuration is ESP32-S3 with 16 MB Flash and 8 MB OPI PSRAM.

The script temporarily maps the repository to a free drive letter to avoid Windows compiler path-length limits, and removes the mapping on completion or error. Keep at least one drive letter from D: through Z: available.

To install dependencies without compiling, append `-SetupOnly`. The default command only compiles. After checking the board, serial port, and backup, build and upload explicitly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1 -Upload -Port COM4
```

Replace `COM4` with your board's port; install its serial driver if Windows does not expose a port. See the [usage guide](doc/user/usage.md) (Chinese) for recovery and troubleshooting.

## Quick start: PC client

Prerequisites: the board is already running this project's firmware, the iPhone is paired with `S3-Click-Buttons`, and AssistiveTouch is enabled. See the [usage guide](doc/user/usage.md) (Chinese) for firmware build prerequisites.

Run from the project root using Python 3.11 or later; development has used a Python 3.11 environment:

```powershell
python -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -e backend
.\backend\.venv\Scripts\python.exe -m iphone_backend.esp32.serial_transport status
```

The client automatically selects a single detected CH343. Specify a port explicitly when needed:

```powershell
.\backend\.venv\Scripts\python.exe -m iphone_backend.esp32.serial_transport --port COM4 status
```

Confirm the target on the phone before sending keyboard or mouse input, following the [usage guide](doc/user/usage.md). A `sent` response means the local BLE stack accepted the reports; verify the actual result on the iPhone.

## Project structure

| Path | Purpose |
| --- | --- |
| firmware/serial_ble_hid/ | Arduino firmware and HID report descriptors |
| backend/ | Unified API, ESP32/AirPlay modules, package, tests and docs |
| .agents/skills/iphone/ | Versioned iPhone skill for repository use or a manual user-scoped link |
| scripts/ | Firmware build and upload scripts |
| tests/ | Client unit tests, hardware serial checks, and diagnostic tools |
| tests/case1_wechat_send_hello/ | Self-contained hardware test case with tooling, doc/, and ignored runs/ artifacts |
| doc/user/ | Installation, usage, build, and recovery instructions |
| doc/dev/ | Design decisions, implementation plan, and hardware validation records |
| AGENTS.md | Agent working conventions and documentation navigation |

Local `.tools/`, `.build/`, `.build-deps/`, `.runtime/`, `.backups/`, and test `runs/` directories are excluded from version control.

## Development and testing

Run client unit tests without connecting hardware:

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

Hardware tests run separately from unit tests. The WeChat case operates a real iPhone and sends a message; see its [test procedure](tests/case1_wechat_send_hello/doc/test-case.md) (Chinese). Design choices and remaining work are documented in the [implementation plan](doc/dev/plan.md) (Chinese).

## Release readiness

The source is published under the MIT License, with standalone firmware dependency installation and compilation. The backend includes the capture worker; UxPlay remains an external runtime configured with UXPLAY_DIR. See the [remaining release tasks](doc/dev/plan.md#独立发布前待办) (Chinese).

## License

[MIT](LICENSE). Modification, redistribution, and commercial use are permitted, provided the copyright and permission notices are retained. Third-party dependencies remain under their respective licenses.

A persistent local FastAPI controller now reuses the serial session and returns action results with AirPlay screenshots. See the [service guide](backend/doc/service.md) (Chinese). Current source uses native absolute HID exclusively for positioning and calibration. Relative movement, relative dragging, and the visual-relative controller have been removed; in-place clicks, scrolling, and keyboard input remain. The 2026-11-03 firmware 0.4.2 and response guard v9 are deployed; 143 backend tests and 3 firmware layout tests pass. Nine-point calibration, five independent holdouts, three reset/reconnect rounds and a 36-point scope validation passed. Verified rectangle: (24,60)–(471,1010), plus separate edge-midpoint evidence. Actual corners remain inset and unverified; physical rotation and long-term reliability are untested. Static snapshots can be returned as observation_only without claiming an effect or replaying input. There is no relative fallback. See the [design pitfalls](backend/doc/direction-root-cause.md) (Chinese). Install with `python -m pip install -e backend` and run `python -m iphone_backend` for the optional service. Do not run direct serial clients while it owns the port.

Firmware **0.3.3** adds modifier-first keyboard reports for the tested iPhone: ordinary HID text now preserves `a_A-1`, and Ctrl+Space again exposes the selected input language. Client text calls are unchanged. See [modifier timing and evidence](firmware/serial_ble_hid/doc/keyboard-modifiers.md) and [native HID validation](backend/doc/native-hid-validation.md) for current scope and limitations; older results above remain historical.

## Unified backend

Use [backend setup](backend/doc/usage.md) to configure absolute runtime paths and start `python -m iphone_backend`. Port 8892 is the only control/frame API; 8891 and 8894 are internal. The manager starts capture and supervises the API. Do not run the direct serial CLI while the API owns the device. See [migration evidence](backend/doc/implementation-status.md).
