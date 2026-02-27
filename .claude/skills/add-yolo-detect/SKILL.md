---
name: add-yolo-detect
description: Adds NPU-accelerated YOLO object detection to NanoClaw. The agent can write a `capture_and_detect` IPC task; the host captures a frame, runs YOLOv5s on the Rockchip NPU, and sends an annotated photo with a detection summary to WhatsApp.
depends: add-camera
---

# Add YOLO Object Detection

This skill adds a `capture_and_detect` IPC task type. When Claude writes the task file,
the host:

1. Captures a frame from `/dev/video0` (or uses a pre-captured photo)
2. Runs `scripts/yolo-detect.py` → NPU inference via `rknnlite`
3. Sends the annotated JPEG to WhatsApp along with a human-readable label list

**Model used:** YOLOv5s (RKNN-quantized), located at
`/home/radxa/YOLO-Test/rk3576_rknn_yolov5_demo/yolov5s.rknn`

## Phase 1: Pre-flight

### Check prerequisites

```bash
# add-camera must already be applied (provides sendImage)
grep -q "capture_photo" src/ipc.ts && echo "add-camera OK" || echo "Apply add-camera first"

# Python dependencies
python3 -c "import cv2, numpy, rknnlite" && echo "Python deps OK" \
  || pip3 install opencv-python-headless rknn-toolkit-lite2

# Model file
ls /home/radxa/YOLO-Test/rk3576_rknn_yolov5_demo/yolov5s.rknn
```

### Test the detection script standalone

```bash
# Capture a test photo first
fswebcam -d /dev/video0 -r 1280x720 --jpeg 95 --no-banner /tmp/test_input.jpg

# Run detection
python3 scripts/yolo-detect.py \
  --image /tmp/test_input.jpg \
  --annotate /tmp/test_annotated.jpg \
  --conf 0.5
# Expected: JSON with "success": true and a "detections" array
```

## Phase 2: Apply Code Changes

### Copy the detection script

```bash
cp .claude/skills/add-yolo-detect/add/scripts/yolo-detect.py scripts/yolo-detect.py
chmod +x scripts/yolo-detect.py
```

### Apply IPC and CLAUDE.md changes

```bash
npx tsx scripts/apply-skill.ts .claude/skills/add-yolo-detect
```

Or merge manually using the intent files in `modify/`.

### Build

```bash
npm run build
```

## Phase 3: Verify

### Drop a test IPC task

```bash
GROUP="main"
TASKS="/home/radxa/nanoclaw/data/ipc/${GROUP}/tasks"
mkdir -p "$TASKS"
echo '{
  "type": "capture_and_detect",
  "chatJid": "17038709442@s.whatsapp.net",
  "caption": "🔍 YOLO scan"
}' > "${TASKS}/detect_$(date +%s).json"
```

You should receive an annotated image in WhatsApp with bounding boxes and a label list.

### Check logs

```bash
journalctl --user -u nanoclaw -f | grep -iE "yolo|detect|npu"
```

## How the agent uses it

Claude can trigger detection with:

```bash
echo '{
  "type": "capture_and_detect",
  "chatJid": "<TARGET_JID>",
  "caption": "📸 Here is what I see"
}' > /workspace/ipc/tasks/detect_$(date +%s).json
```

The host returns the annotated image. Claude never processes image data directly.

## Troubleshooting

### rknnlite import error

```bash
pip3 install rknn-toolkit-lite2
# If the package name differs for RK3399, check Radxa docs for the correct wheel
```

### Detection returns 0 objects

- Lower `--conf` threshold: try `0.3`
- Ensure adequate lighting on the scene
- The model is COCO-trained (80 classes: person, car, bottle, etc.)

### RKNN runtime init fails

The NPU service must be running:
```bash
systemctl status rknpu2
# If not running:
sudo systemctl start rknpu2
```
