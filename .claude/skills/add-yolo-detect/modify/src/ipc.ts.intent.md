# Intent: src/ipc.ts (add-yolo-detect)

## What changed

Added `capture_and_detect` case to `processTaskIpc` switch statement.
Also added `spawnSync` to imports (child_process already imported for `execSync`).

## Location

After the `capture_photo` case, before `default`.

## Authorization model

Same as `capture_photo`: chatJid must be provided, non-main groups restricted to own JID.

## Runtime flow

1. Run `fswebcam` to capture JPEG to a temp file (reuses camera capture logic)
2. Run `python3 scripts/yolo-detect.py --image <tmpPhoto> --annotate <tmpAnnotated> --conf 0.5`
   via `spawnSync` (blocking, 30s timeout)
3. Parse stdout as JSON
4. Build a WhatsApp caption: header + bulleted label list
5. Send `tmpAnnotated` (or `tmpPhoto` if annotation failed) via `deps.sendImage`
6. Clean up both temp files in `finally`

## Invariants

- `spawnSync` is used so both temp files exist during the `sendImage` call
- If yolo-detect.py exits non-zero, the raw photo is sent with an error note
- Label list is capped at 10 entries to avoid overly long captions
- The `scripts/yolo-detect.py` path is relative to `process.cwd()` (the nanoclaw root)

## Diff summary (relative to add-camera version of ipc.ts)

```
+ case 'capture_and_detect': {
+   // capture photo
+   execSync(`fswebcam ...`, { timeout: 15000 });
+   // run YOLO
+   const result = spawnSync('python3', ['scripts/yolo-detect.py', '--image', tmpPhoto,
+                                        '--annotate', tmpAnnotated, '--conf', '0.5']);
+   const json = JSON.parse(result.stdout.toString());
+   // build caption + send annotated image
+   await deps.sendImage(targetJid, tmpAnnotated, caption);
+   break;
+ }
```
