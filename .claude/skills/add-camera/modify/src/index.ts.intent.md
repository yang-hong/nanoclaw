# Intent: src/index.ts (add-camera)

## What changed

Added `sendImage` to the `deps` object passed to `startIpcWatcher`.

## Location

Inside the `startIpcWatcher({ ... })` call in `main()`.

## Invariants

- `whatsapp` is the `WhatsAppChannel` instance created just before this call
- The lambda throws if `whatsapp` is not initialised (should not happen in normal flow)
- Signature must match `IpcDeps.sendImage: (jid, imagePath, caption?) => Promise<void>`

## Diff summary

```
startIpcWatcher({
  sendMessage: ...,
+ sendImage: (jid, imagePath, caption) => {
+   if (!whatsapp) throw new Error('WhatsApp channel not ready');
+   return whatsapp.sendImage(jid, imagePath, caption);
+ },
  registeredGroups: ...
```
