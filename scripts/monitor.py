#!/usr/bin/env python3
"""
monitor.py — Lightweight host-side surveillance loop for NanoClaw.

Bypasses Claude entirely. Captures frames from a USB camera, runs YOLOv5s
on the NPU, and writes IPC task files to send results via WhatsApp.

Controlled via JSON config file; nanoclaw's IPC handler writes/deletes
this file to start/stop monitoring.

Config file: /tmp/nanoclaw-monitor.json
  {
    "chatJid": "17038709442@s.whatsapp.net",
    "interval": 10,
    "detectLabels": ["person"],
    "confidenceThreshold": 0.5,
    "sendAnnotated": true,
    "groupFolder": "main"
  }

To stop: delete the config file or write {"stop": true}.
"""

import json
import os
import signal
import sys
import time

import cv2
import numpy as np

# ─── Paths ────────────────────────────────────────────────────────────────────
CONFIG_PATH    = "/tmp/nanoclaw-monitor.json"
IPC_BASE       = os.path.expanduser("~/nanoclaw/data/ipc")
MODEL_PATH     = "/home/radxa/YOLO-Test/rk3576_rknn_yolov5_demo/yolov5s.rknn"
LABELS_PATH    = "/home/radxa/YOLO-Test/rk3576_rknn_yolov5_demo/coco_labels.txt"
CAMERA_DEVICE  = "/dev/video0"

# ─── YOLOv5 constants ─────────────────────────────────────────────────────────
MODEL_INPUT  = 640
NUM_CLASSES  = 80
MAX_DETS     = 50
ANCHORS = [
    [(10, 13), (16, 30),  (33, 23)],
    [(30, 61), (62, 45),  (59, 119)],
    [(116, 90),(156, 198),(373, 326)],
]
STRIDES = [8, 16, 32]

def load_labels(path: str) -> list[str]:
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip()]

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

def decode_outputs(outputs, img_h, img_w, conf_thresh, nms_thresh):
    all_boxes = []
    for head_idx, feat in enumerate(outputs):
        feat = feat[0]
        gh, gw = feat.shape[1], feat.shape[2]
        feat = feat.reshape(3, NUM_CLASSES + 5, gh, gw).transpose(0, 2, 3, 1)
        stride  = STRIDES[head_idx]
        anchors = ANCHORS[head_idx]
        for a_idx in range(3):
            data     = feat[a_idx]
            box_xy   = data[..., :2]
            box_wh   = data[..., 2:4]
            obj_conf = data[..., 4]
            cls_conf = data[..., 5:]
            if obj_conf.max() < conf_thresh:
                continue
            scores     = obj_conf[..., None] * cls_conf
            max_scores = scores.max(axis=-1)
            mask       = max_scores > conf_thresh
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
    return result[:MAX_DETS] if len(result) > MAX_DETS else result

def annotate(frame, detections, labels):
    out = frame.copy()
    for det in detections:
        x1, y1, x2, y2, conf, cls_id = det
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        label = labels[int(cls_id)] if int(cls_id) < len(labels) else str(int(cls_id))
        text  = f"{label} {conf:.2f}"
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        ty = max(y1 - 4, th + 4)
        cv2.rectangle(out, (x1, ty - th - 4), (x1 + tw, ty), (0, 255, 0), -1)
        cv2.putText(out, text, (x1, ty - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    return out

def read_config():
    """Read monitor config. Returns None if monitoring should stop."""
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        if cfg.get("stop"):
            return None
        return cfg
    except (json.JSONDecodeError, IOError):
        return None

def send_via_ipc(group_folder: str, image_path: str, caption: str, chat_jid: str):
    """Write an IPC task file for nanoclaw to send the image."""
    tasks_dir = os.path.join(IPC_BASE, group_folder, "tasks")
    os.makedirs(tasks_dir, exist_ok=True)
    task = {
        "type": "send_image",
        "chatJid": chat_jid,
        "imagePath": image_path,
        "caption": caption,
    }
    task_file = os.path.join(tasks_dir, f"monitor_{int(time.time() * 1000)}.json")
    with open(task_file, "w") as f:
        json.dump(task, f)

def main():
    print("[monitor] Starting monitor daemon, waiting for config...")

    running = True
    def handle_signal(sig, _frame):
        nonlocal running
        print(f"[monitor] Signal {sig} received, shutting down")
        running = False
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    labels = load_labels(LABELS_PATH)
    rknn = None
    cap = None

    while running:
        cfg = read_config()
        if cfg is None:
            # No active monitoring — sleep and check again
            if cap is not None:
                cap.release()
                cap = None
            if rknn is not None:
                rknn.release()
                rknn = None
            time.sleep(2)
            continue

        chat_jid   = cfg.get("chatJid", "")
        interval   = max(cfg.get("interval", 10), 3)  # minimum 3 seconds
        detect_labels = set(cfg.get("detectLabels", ["person"]))
        conf_thresh = cfg.get("confidenceThreshold", 0.5)
        send_annotated = cfg.get("sendAnnotated", True)
        group_folder = cfg.get("groupFolder", "main")

        # Lazy-init camera
        if cap is None:
            cap = cv2.VideoCapture(CAMERA_DEVICE)
            if not cap.isOpened():
                print(f"[monitor] Cannot open camera {CAMERA_DEVICE}, retrying in 5s")
                cap = None
                time.sleep(5)
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            # Warm up auto-exposure
            for _ in range(20):
                cap.read()
            print("[monitor] Camera opened and warmed up")

        # Lazy-init RKNN
        if rknn is None:
            # Suppress RKNN stdout noise
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            old_stdout_fd = os.dup(1)
            os.dup2(devnull_fd, 1)

            from rknnlite.api import RKNNLite
            rknn = RKNNLite()
            ret = rknn.load_rknn(MODEL_PATH)
            if ret != 0:
                os.dup2(old_stdout_fd, 1)
                os.close(devnull_fd)
                os.close(old_stdout_fd)
                print(f"[monitor] Failed to load RKNN model (ret={ret})")
                time.sleep(5)
                rknn = None
                continue
            ret = rknn.init_runtime()
            os.dup2(old_stdout_fd, 1)
            os.close(devnull_fd)
            os.close(old_stdout_fd)
            if ret != 0:
                print(f"[monitor] Failed to init RKNN runtime (ret={ret})")
                rknn.release()
                rknn = None
                time.sleep(5)
                continue
            print("[monitor] RKNN model loaded")

        # Capture frame
        ret_cap, frame = cap.read()
        if not ret_cap or frame is None:
            print("[monitor] Frame capture failed, reopening camera")
            cap.release()
            cap = None
            time.sleep(2)
            continue

        img_h, img_w = frame.shape[:2]

        # Inference
        rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (MODEL_INPUT, MODEL_INPUT))
        inp     = np.expand_dims(resized, 0)

        outputs = rknn.inference(inputs=[inp])
        if outputs is None:
            print("[monitor] Inference returned None")
            time.sleep(interval)
            continue

        dets = decode_outputs(outputs, img_h, img_w, conf_thresh, 0.45)

        # Filter for target labels
        matched = []
        for det in dets:
            cls_id = int(det[5])
            label  = labels[cls_id] if cls_id < len(labels) else str(cls_id)
            if label in detect_labels:
                matched.append((label, float(det[4]), det))

        if matched:
            # Build summary
            summary_lines = [f"• {lbl} ({conf*100:.0f}%)" for lbl, conf, _ in matched[:10]]
            caption = f"🚨 Detected {len(matched)} target(s):\n" + "\n".join(summary_lines)

            # Save image
            ts = int(time.time() * 1000)
            if send_annotated:
                annotated_frame = annotate(frame, np.array([d for _, _, d in matched]), labels)
                img_path = f"/tmp/nanoclaw-monitor-{ts}.jpg"
                cv2.imwrite(img_path, annotated_frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            else:
                img_path = f"/tmp/nanoclaw-monitor-{ts}.jpg"
                cv2.imwrite(img_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 92])

            send_via_ipc(group_folder, img_path, caption, chat_jid)
            print(f"[monitor] Alert sent: {len(matched)} target(s) detected")

        # Sleep until next cycle
        time.sleep(interval)

    # Cleanup
    if cap is not None:
        cap.release()
    if rknn is not None:
        rknn.release()
    print("[monitor] Stopped")

if __name__ == "__main__":
    main()
