# Intent: src/ipc.ts (add-monitor)

## What changed

1. Added `spawn` to `child_process` imports
2. Added module-level variables: `monitorProcess`, `MONITOR_CONFIG`, `MONITOR_SCRIPT`
3. Added three new IPC task types: `start_monitor`, `stop_monitor`, `send_image`

## start_monitor

- Main-group only authorization
- Writes a JSON config to `/tmp/nanoclaw-monitor.json`
- Spawns `scripts/monitor.py` as a detached child process (if not already running)
- Sends a confirmation message to the user

## stop_monitor

- Main-group only authorization
- Writes `{"stop": true}` to the config file
- Kills the monitor process via SIGTERM
- Deletes the config file

## send_image

- Used by `monitor.py` to send images through WhatsApp without going through Claude
- Reads `imagePath` from the task, sends via `deps.sendImage`, then deletes the temp file
- No special authorization (the IPC directory structure already provides group scoping)
