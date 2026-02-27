---
name: add-camera
description: Adds USB camera capture to NanoClaw. The agent can write an IPC task file to trigger a photo capture; the host runs fswebcam and sends the image directly to WhatsApp.
---

# Add Camera (USB Photo Capture)

This skill extends NanoClaw with a `capture_photo` IPC task type. When Claude writes a
`capture_photo` JSON file into `/workspace/ipc/tasks/`, the host:

1. Runs `fswebcam` to grab a frame from `/dev/video0`
2. Calls `whatsapp.sendImage()` to deliver the JPEG to the target chat

## Phase 1: Pre-flight

### Check system dependencies

```bash
which fswebcam || sudo apt-get install -y fswebcam
ls /dev/video0 || echo "No camera found at /dev/video0"
```

If `fswebcam` is missing, install it. If no camera is attached, plug one in.

### Check if already applied

Look at `src/ipc.ts`. If it contains `case 'capture_photo':`, this skill is already applied.

## Phase 2: Apply Code Changes

### Apply with the skills engine

```bash
npx tsx scripts/apply-skill.ts .claude/skills/add-camera
```

This will three-way merge:
- `src/channels/whatsapp.ts` — adds `sendImage()` method
- `src/ipc.ts` — adds `sendImage` to `IpcDeps` + `capture_photo` task handler
- `src/index.ts` — wires `whatsapp.sendImage` into `startIpcWatcher` deps

If merge conflicts occur, consult the intent files in `modify/src/`.

### Build and validate

```bash
npm run build
```

Build must be clean before proceeding.

## Phase 3: Verify

### Test the IPC path manually

While nanoclaw is running, drop a task file:

```bash
GROUP="main"   # or whatever your group folder is
TASKS="/home/radxa/nanoclaw/data/ipc/${GROUP}/tasks"
mkdir -p "$TASKS"
echo '{
  "type": "capture_photo",
  "chatJid": "17038709442@s.whatsapp.net",
  "caption": "📷 Test shot"
}' > "${TASKS}/photo_$(date +%s).json"
```

The photo should arrive on WhatsApp within a few seconds.

### Check logs

```bash
journalctl --user -u nanoclaw -f | grep -iE "photo|camera|image|fswebcam"
```

## Troubleshooting

### fswebcam: No such file or directory

The camera isn't at `/dev/video0`. Check `ls /dev/video*` and update the `device` constant
in `src/ipc.ts` if needed.

### Image not received

- Confirm the chatJid in the task matches a registered group
- Check that `fswebcam` produces a valid JPEG: `fswebcam -d /dev/video0 /tmp/test.jpg`
- Inspect logs for "Failed to capture or send photo"

### Permission denied on /dev/video0

Add the `radxa` user to the `video` group:

```bash
sudo usermod -aG video radxa
newgrp video
```

## How the agent uses it

In `groups/main/CLAUDE.md`, the agent is instructed to write IPC tasks like:

```bash
echo '{
  "type": "capture_photo",
  "chatJid": "<TARGET_JID>",
  "caption": "📷 Here you go!"
}' > /workspace/ipc/tasks/photo_$(date +%s).json
```

The host handles everything else; the agent never touches image files directly.
