# Intent: src/channels/whatsapp.ts (add-camera)

## What changed

Added `sendImage(jid, imagePath, caption?)` public method to `WhatsAppChannel`.

## Location

After the existing `sendMessage()` method, before `isConnected()`.

## Invariants

- `sendImage` reads the file from disk with `fs.readFileSync` — the host is responsible for creating the file
- When `ASSISTANT_HAS_OWN_NUMBER` is false, the caption is prefixed with `${ASSISTANT_NAME}: ` (same as `sendMessage`)
- If not connected, the method logs a warning and returns (does NOT queue — images are not queued like text)
- On error, the method throws (caller catches and sends a fallback text message)
- `fs` was already imported; no new imports needed beyond what `add-voice-transcription` brought in

## Diff summary

```
+ async sendImage(jid: string, imagePath: string, caption?: string): Promise<void> {
+   if (!this.connected) { logger.warn(...); return; }
+   const imageBuffer = fs.readFileSync(imagePath);
+   const captionText = caption ? (ASSISTANT_HAS_OWN_NUMBER ? caption : `${ASSISTANT_NAME}: ${caption}`) : undefined;
+   await this.sock.sendMessage(jid, { image: imageBuffer, ...(captionText ? { caption: captionText } : {}) });
+   logger.info({ jid, imagePath }, 'Image sent');
+ }
```
