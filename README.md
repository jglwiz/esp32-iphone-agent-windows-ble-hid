# ESP32 iPhone Agent for Windows — BLE HID

English | [简体中文](README_CN.md)

Enable an AI agent on Windows to observe and operate an iPhone using ESP32-S3 BLE HID and AirPlay.

Project slug: `esp32-iphone-agent-windows-ble-hid`. The current implementation provides a serial-to-BLE keyboard/mouse bridge and agent-operated hardware tests; visual feedback uses a separately provided AirPlay capture service.

**Control path: PC → USB serial → ESP32-S3 → BLE HID → iPhone.** The board stays connected to the PC by USB; the iPhone receives input over Bluetooth. AirPlay can provide separate visual feedback. BLE HID does not return screen images.

## Status

Firmware 0.2.0 has been tested on a Waveshare ESP32-S3 and an iPhone 17e with keyboard input, Backspace, mouse movement, clicks, dragging, and vertical scrolling in both directions. Full automatic reconnection, lock-screen recovery, long-term stability, and coordinate calibration remain unverified or incomplete. Text output depends on the iPhone's hardware keyboard input method: a Chinese input method converted ASCII underscores into long dashes during testing. See the [validation record](doc/dev/validation.md) (Chinese) for evidence and limitations.

The primary tested environment is Windows with a CH343 serial interface and a board with 16 MB Flash / 8 MB PSRAM. Other ESP32 variants, operating systems, and iOS versions have not been comprehensively tested.

## Quick start: PC client

Prerequisites: the board is already running this project's firmware, the iPhone is paired with `S3-iPhone-HID`, and AssistiveTouch is enabled. See the [usage guide](doc/user/usage.md) (Chinese) for firmware build prerequisites.

Run from the project root using Python 3.10 or later; development has used a Python 3.11 environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r pc\requirements.txt
.\.venv\Scripts\python.exe pc\bridge.py status
```

The client automatically selects a single detected CH343. Specify a port explicitly when needed:

```powershell
.\.venv\Scripts\python.exe pc\bridge.py --port COM4 status
```

Confirm the target on the phone before sending keyboard or mouse input, following the [usage guide](doc/user/usage.md). A `sent` response means the local BLE stack accepted the reports; verify the actual result on the iPhone.

## Project structure

| Path | Purpose |
| --- | --- |
| firmware/serial_ble_hid/ | Arduino firmware and HID report descriptors |
| pc/ | Python serial CLI, dependencies, and optional screenshot client |
| scripts/ | Firmware build and upload scripts |
| tests/ | Client unit tests, hardware serial checks, and diagnostic tools |
| tests/case1_wechat_send_hello/ | Self-contained hardware test case with tooling, doc/, and ignored runs/ artifacts |
| doc/user/ | Installation, usage, build, and recovery instructions |
| doc/dev/ | Design decisions, implementation plan, and hardware validation records |
| AGENTS.md | Agent working conventions and documentation navigation |

Local `.build/`, `.build-deps/`, `.runtime/`, `.backups/`, and test `runs/` directories are excluded from version control.

## Development and testing

Run client unit tests without connecting hardware:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Hardware tests run separately from unit tests. The WeChat case operates a real iPhone and sends a message; see its [test procedure](tests/case1_wechat_send_hello/doc/test-case.md) (Chinese). Design choices and remaining work are documented in the [implementation plan](doc/dev/plan.md) (Chinese).

## Release readiness

The source is published under the MIT License. The build script still depends on tooling in the original parent workspace, and the screenshot service must be provided separately. See the [remaining release tasks](doc/dev/plan.md#独立发布前待办) (Chinese).

## License

[MIT](LICENSE). Modification, redistribution, and commercial use are permitted, provided the copyright and permission notices are retained. Third-party dependencies remain under their respective licenses.
