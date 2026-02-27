#!/usr/bin/env python3
"""
yolo-detect.py — Single-frame YOLO object detection for NanoClaw IPC.

Uses the YOLOv5s RKNN model on the Radxa NPU. Accepts either a camera device
or a pre-captured image, runs inference, and outputs structured JSON.

Usage:
  python3 yolo-detect.py --image /tmp/photo.jpg [--conf 0.5] [--annotate /tmp/annotated.jpg]
  python3 yolo-detect.py --camera /dev/video0  [--conf 0.5] [--annotate /tmp/annotated.jpg]

Output (stdout, JSON):
  {
    "success": true,
    "detections": [
      {"label": "person", "confidence": 0.92, "bbox": [x1, y1, x2, y2]},
      ...
    ],
    "count": 2,
    "annotated_image": "/tmp/annotated.jpg"   // null if --annotate not given
  }

On failure:
  { "success": false, "error": "<message>" }

Dependencies (must be installed on host, NOT inside container):
  pip3 install rknn-toolkit-lite2 opencv-python-headless numpy
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np

# ─── Model paths ──────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
# Default model location (the one already present on the board)
DEFAULT_MODEL  = "/home/radxa/YOLO-Test/rk3576_rknn_yolov5_demo/yolov5s.rknn"
DEFAULT_LABELS = "/home/radxa/YOLO-Test/rk3576_rknn_yolov5_demo/coco_labels.txt"

# ─── YOLOv5 constants ─────────────────────────────────────────────────────────
MODEL_INPUT  = 640
NUM_CLASSES  = 80
MAX_DETS     = 50

ANCHORS = [
    [(10, 13), (16, 30),  (33, 23)],       # P3 / stride 8
    [(30, 61), (62, 45),  (59, 119)],      # P4 / stride 16
    [(116, 90),(156, 198),(373, 326)],     # P5 / stride 32
]
STRIDES = [8, 16, 32]

# ─── Helper: load labels ──────────────────────────────────────────────────────
def load_labels(path: str) -> list[str]:
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip()]

# ─── Helper: NMS ──────────────────────────────────────────────────────────────
def nms(boxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas  = (x2 - x1) * (y2 - y1)
    order  = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou   = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        order = order[np.where(iou <= iou_thresh)[0] + 1]
    return keep

# ─── Helper: decode RKNN outputs ──────────────────────────────────────────────
def decode_outputs(
    outputs: list,
    img_h: int,
    img_w: int,
    conf_thresh: float,
    nms_thresh: float,
) -> np.ndarray:
    """Return (N, 6) array: [x1, y1, x2, y2, confidence, class_id]"""
    all_boxes: list = []

    for head_idx, feat in enumerate(outputs):
        feat = feat[0]                                      # (255, H, W)
        gh, gw = feat.shape[1], feat.shape[2]
        feat = feat.reshape(3, NUM_CLASSES + 5, gh, gw).transpose(0, 2, 3, 1)  # (3,H,W,85)

        stride  = STRIDES[head_idx]
        anchors = ANCHORS[head_idx]

        for a_idx in range(3):
            data    = feat[a_idx]                           # (H, W, 85)
            box_xy  = data[..., :2]                         # already sigmoid
            box_wh  = data[..., 2:4]
            obj_conf= data[..., 4]                          # (H, W)
            cls_conf= data[..., 5:]                         # (H, W, 80)

            if obj_conf.max() < conf_thresh:
                continue

            scores       = obj_conf[..., None] * cls_conf  # (H, W, 80)
            max_scores   = scores.max(axis=-1)              # (H, W)
            mask         = max_scores > conf_thresh
            if not mask.any():
                continue

            ys, xs = np.where(mask)
            for y, x in zip(ys, xs):
                bx = (box_xy[y, x, 0] * 2.0 - 0.5 + x) * stride
                by = (box_xy[y, x, 1] * 2.0 - 0.5 + y) * stride
                bw = (box_wh[y, x, 0] * 2.0) ** 2 * anchors[a_idx][0]
                bh = (box_wh[y, x, 1] * 2.0) ** 2 * anchors[a_idx][1]

                x1 = (bx - bw / 2) / MODEL_INPUT * img_w
                y1 = (by - bh / 2) / MODEL_INPUT * img_h
                x2 = (bx + bw / 2) / MODEL_INPUT * img_w
                y2 = (by + bh / 2) / MODEL_INPUT * img_h

                conf   = float(max_scores[y, x])
                cls_id = int(scores[y, x].argmax())
                all_boxes.append([x1, y1, x2, y2, conf, cls_id])

    if not all_boxes:
        return np.empty((0, 6), dtype=np.float32)

    boxes = np.array(all_boxes, dtype=np.float32)
    keep  = nms(boxes[:, :4], boxes[:, 4], nms_thresh)
    result = boxes[keep]

    if len(result) > MAX_DETS:
        result = result[:MAX_DETS]

    return result

# ─── Helper: annotate image ───────────────────────────────────────────────────
def annotate(frame: np.ndarray, detections: np.ndarray, labels: list[str]) -> np.ndarray:
    out = frame.copy()
    for det in detections:
        x1, y1, x2, y2, conf, cls_id = det
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        cls_id = int(cls_id)
        label  = labels[cls_id] if cls_id < len(labels) else str(cls_id)
        text   = f"{label} {conf:.2f}"

        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        ty = max(y1 - 4, th + 4)
        cv2.rectangle(out, (x1, ty - th - 4), (x1 + tw, ty), (0, 255, 0), -1)
        cv2.putText(out, text, (x1, ty - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    return out

# ─── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="NanoClaw single-frame YOLO detector")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--image",  help="Path to input JPEG/PNG")
    src.add_argument("--camera", help="Camera device (e.g. /dev/video0)")
    parser.add_argument("--model",    default=DEFAULT_MODEL,  help="Path to .rknn model")
    parser.add_argument("--labels",   default=DEFAULT_LABELS, help="Path to labels file")
    parser.add_argument("--conf",     type=float, default=0.5, help="Confidence threshold")
    parser.add_argument("--nms",      type=float, default=0.45, help="NMS IoU threshold")
    parser.add_argument("--annotate", help="Save annotated image to this path")
    parser.add_argument("--width",    type=int, default=1280, help="Camera capture width")
    parser.add_argument("--height",   type=int, default=720,  help="Camera capture height")
    args = parser.parse_args()

    def fail(msg: str) -> None:
        print(json.dumps({"success": False, "error": msg}))
        sys.exit(1)

    # ── Load labels ──────────────────────────────────────────────────
    if not os.path.exists(args.labels):
        fail(f"Labels file not found: {args.labels}")
    labels = load_labels(args.labels)

    # ── Capture frame ─────────────────────────────────────────────────
    if args.image:
        if not os.path.exists(args.image):
            fail(f"Image file not found: {args.image}")
        frame = cv2.imread(args.image)
        if frame is None:
            fail(f"Could not read image: {args.image}")
    else:
        cap = cv2.VideoCapture(args.camera)
        if not cap.isOpened():
            fail(f"Cannot open camera: {args.camera}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        # Discard a few frames to let auto-exposure settle
        for _ in range(3):
            cap.read()
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            fail(f"Failed to capture frame from {args.camera}")

    img_h, img_w = frame.shape[:2]

    # ── Load RKNN model ───────────────────────────────────────────────
    if not os.path.exists(args.model):
        fail(f"RKNN model not found: {args.model}")

    try:
        from rknnlite.api import RKNNLite
    except ImportError:
        fail("rknnlite not installed. Run: pip3 install rknn-toolkit-lite2")

    rknn = RKNNLite()
    ret  = rknn.load_rknn(args.model)
    if ret != 0:
        fail(f"Failed to load RKNN model (ret={ret})")
    ret = rknn.init_runtime()
    if ret != 0:
        fail(f"Failed to init RKNN runtime (ret={ret})")

    # ── Pre-process ───────────────────────────────────────────────────
    rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    resized  = cv2.resize(rgb, (MODEL_INPUT, MODEL_INPUT))
    inp      = np.expand_dims(resized, 0)   # (1, 640, 640, 3)

    # ── Inference ─────────────────────────────────────────────────────
    outputs = rknn.inference(inputs=[inp])
    rknn.release()

    if outputs is None:
        fail("RKNN inference returned None")

    # ── Post-process ──────────────────────────────────────────────────
    dets = decode_outputs(outputs, img_h, img_w, args.conf, args.nms)

    # ── Build JSON result ─────────────────────────────────────────────
    detection_list = []
    for det in dets:
        x1, y1, x2, y2, conf, cls_id = det
        detection_list.append({
            "label":      labels[int(cls_id)] if int(cls_id) < len(labels) else str(int(cls_id)),
            "confidence": round(float(conf), 4),
            "bbox":       [round(float(x1), 1), round(float(y1), 1),
                           round(float(x2), 1), round(float(y2), 1)],
        })

    # ── Annotate + save ───────────────────────────────────────────────
    annotated_path = None
    if args.annotate:
        annotated = annotate(frame, dets, labels)
        cv2.imwrite(args.annotate, annotated, [cv2.IMWRITE_JPEG_QUALITY, 92])
        annotated_path = args.annotate

    result = {
        "success":          True,
        "count":            len(detection_list),
        "detections":       detection_list,
        "annotated_image":  annotated_path,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
