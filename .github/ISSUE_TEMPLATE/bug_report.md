---
name: Bug Report
about: Report a bug to help improve arkiv
title: "[Bug] "
labels: bug
---

## Environment

- **OS**: (e.g., macOS 14.3 / Windows 11 / Ubuntu 22.04)
- **Python**: (e.g., 3.12.1)
- **Whisper backend**: (mlx-whisper / faster-whisper CUDA / faster-whisper CPU)
- **GPU**: (e.g., Apple M2 Max / RTX 4070 / None)

## Diagnostics

Paste **one** of these (whichever you can run):

- **From source/CLI:** `python health.py` output, or
- **Desktop app:** the health JSON — `curl http://127.0.0.1:<port>/api/health`
  (the port is in the app's log, below), or just paste the tail of the log file:
  `~/Library/Application Support/com.hevin.arkiv/arkiv/logs/backend.log`

```
(paste output here)
```

## Steps to Reproduce

1. ...
2. ...
3. ...

## Expected Behavior

What you expected to happen.

## Actual Behavior

What actually happened. Include error messages or screenshots if applicable.

## Additional Context

Any other relevant information (file formats, media duration, log output, etc.)
