# Intent: src/ipc.ts (add-camera)

## What changed

1. Added `sendImage` to the `IpcDeps` interface
2. Added `capture_photo` case to `processTaskIpc` switch statement
3. Added `os` import (used for `os.tmpdir()`)

## Location of new case

After the `register_group` case, before `default`.

## Authorization model

`capture_photo` follows the same pattern as `sendMessage` IPC tasks:
- `chatJid` must be provided
- Non-main groups can only send to their own registered JID

## Invariants

- `execSync` is used (blocking) so the image exists before `sendImage` is called
- Temp file is always cleaned up in `finally` block, even on error
- On capture failure, a polite error text is sent to the chat instead
- The camera device path is hardcoded as `/dev/video0` — adjust here if the device changes
- `fswebcam` resolution is 1280×720, JPEG quality 95, `--no-banner` to suppress overlay

## Diff summary

```
interface IpcDeps {
  sendMessage: ...;
+ sendImage: (jid: string, imagePath: string, caption?: string) => Promise<void>;
  ...
}

// In processTaskIpc switch:
+ case 'capture_photo': {
+   const targetJid = data.chatJid;
+   // auth checks ...
+   const tmpPath = path.join(os.tmpdir(), `nanoclaw-photo-${Date.now()}.jpg`);
+   execSync(`fswebcam -d /dev/video0 -r 1280x720 --jpeg 95 --no-banner "${tmpPath}"`, { timeout: 15000 });
+   await deps.sendImage(targetJid, tmpPath, caption ?? '📷');
+   break;
+ }
```
