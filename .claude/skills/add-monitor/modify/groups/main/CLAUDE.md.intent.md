# Intent: groups/main/CLAUDE.md (add-monitor)

## What changed

Added "Start/stop continuous monitoring" subsection under "USB Camera".

## Key instructions for the agent

- Use `start_monitor` for continuous surveillance (user says "keep watching", "alert me", "every N seconds")
- Use `stop_monitor` to stop
- Use single-shot `capture_and_detect` for one-time questions
- Parameters: `interval`, `detectLabels`, `confidenceThreshold` are all configurable
- Monitoring runs on the host Python loop, not through Claude, so it's fast (~2-3s per cycle)
