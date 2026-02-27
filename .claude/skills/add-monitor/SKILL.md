---
name: add-monitor
description: Adds continuous surveillance mode. A lightweight Python loop captures frames, runs YOLO on the NPU, and sends WhatsApp alerts when target objects (e.g. person) are detected. Bypasses Claude for sub-5-second response time.
depends: add-camera, add-yolo-detect
---

# Add Continuous Monitor

This skill adds `start_monitor` and `stop_monitor` IPC task types plus a host-side
Python daemon (`scripts/monitor.py`) that runs a tight detection loop without
involving Claude containers.

## Architecture

```
User: "每10秒看一次有没有人"
  → Omo writes start_monitor IPC task
    → ipc.ts writes /tmp/nanoclaw-monitor.json + spawns monitor.py
      → monitor.py loop: camera → NPU YOLO → match labels → write send_image IPC task
        → ipc.ts picks up send_image → WhatsApp
```

Total latency per cycle: ~2-3 seconds (vs ~40s through Claude).

## Phase 1: Pre-flight

```bash
# Verify dependencies
python3 -c "import cv2, numpy, rknnlite" && echo "OK"

# Verify add-camera and add-yolo-detect are applied
grep -q "capture_photo" src/ipc.ts && echo "add-camera OK"
grep -q "capture_and_detect" src/ipc.ts && echo "add-yolo-detect OK"
```

## Phase 2: Apply

```bash
cp .claude/skills/add-monitor/add/scripts/monitor.py scripts/monitor.py
chmod +x scripts/monitor.py
npx tsx scripts/apply-skill.ts .claude/skills/add-monitor
npm run build
```

## Phase 3: Verify

### Start monitoring

```bash
GROUP="main"
TASKS="data/ipc/${GROUP}/tasks"
mkdir -p "$TASKS"
echo '{
  "type": "start_monitor",
  "chatJid": "17038709442@s.whatsapp.net",
  "interval": 10,
  "detectLabels": ["person"]
}' > "${TASKS}/monitor_$(date +%s).json"
```

Stand in front of the camera. Within 10 seconds you should receive an annotated
photo on WhatsApp.

### Stop monitoring

```bash
echo '{
  "type": "stop_monitor",
  "chatJid": "17038709442@s.whatsapp.net"
}' > "${TASKS}/stop_$(date +%s).json"
```

### Check logs

```bash
# Monitor process logs go to stdout (not journald)
ps aux | grep monitor.py

# IPC logs
journalctl --user -u nanoclaw -f | grep -iE "monitor|send_image"
```

## Troubleshooting

### Monitor starts but no alerts

- Verify the camera is working: `fswebcam -d /dev/video0 /tmp/test.jpg`
- Check that `detectLabels` matches COCO labels (lowercase: "person", not "Person")
- Lower `confidenceThreshold` to 0.3

### Monitor doesn't start

- Check if `scripts/monitor.py` exists and is executable
- Check Python deps: `python3 -c "from rknnlite.api import RKNNLite"`

### Too many alerts

- Increase `interval` (e.g. 30 seconds)
- Increase `confidenceThreshold` (e.g. 0.7)
