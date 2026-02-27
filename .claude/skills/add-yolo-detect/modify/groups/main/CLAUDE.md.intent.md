# Intent: groups/main/CLAUDE.md (add-yolo-detect)

## What changed

Expanded the "USB Camera" section to document both IPC commands:
- `capture_photo` — plain photo (from add-camera skill)
- `capture_and_detect` — photo + NPU YOLO inference + annotated image

## Invariants

- The section still opens with "## USB Camera"
- `capture_photo` documentation is preserved verbatim (add-camera owns it)
- The new `capture_and_detect` block follows immediately
- Guidance at the end clarifies when to use each command
- Agent is reminded it never handles image files directly
