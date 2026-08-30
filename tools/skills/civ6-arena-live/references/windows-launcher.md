# Windows launch and save loading

Use this procedure from gaming-PC WSL when Civ VI is closed, hung, at the main
menu, or in an unverified save.

The native companion checkout is `C:\Users\wrisl\dev\civ6-mcp`
(`/mnt/c/Users/wrisl/dev/civ6-mcp` from WSL). It is a GitHub clone, not a
separate codebase.

1. Run `tools/skills/civ6-arena-live/scripts/firetuner-owner-map.sh`. Resolve
   unintended owners by exact PID; never compete with an active arena or MCP
   server for FireTuner.
2. After pushing WSL `main`, sync Windows with
   `git -C /mnt/c/Users/wrisl/dev/civ6-mcp pull --ff-only`.
3. Run these from the WSL repo root:

   ```bash
   tools/skills/civ6-arena-live/scripts/windows-civ6-launcher.sh preflight
   tools/skills/civ6-arena-live/scripts/windows-civ6-launcher.sh \
     restart-and-load CHANNELS_GATE_V1_T157
   ```

   Substitute the requested save name. Use `load` instead of
   `restart-and-load` only when preserving the current game process matters.
   Do not allocate a PTY: Windows console cursor queries can hang under WSL
   PTYs.
4. Wait for the helper to exit. Success lists every OCR step and ends with
   `FireTuner port confirmed open`; the helper does not retain a client.
5. Verify the exact turn/civ through an existing owner's safe API. If none
   exists, start `uv run civ-mcp` with a PTY, query
   `curl http://127.0.0.1:8000/api/overview`, then stop the verifier. Re-run the
   owner map and ensure it is gone before starting arena. Treat this exact-state
   check as authoritative: a menu click that appears successful is not proof
   that the requested save loaded.

### Save-selection recovery details (proven on v9)

- `_ocr_game_window` returns line-center coordinates. Use them directly;
  adding half the OCR width/height moves the click off target.
- At 1920x1080 the bottom `Load` button can be clipped from OCR. Once the exact
  save row is visibly selected, `Enter` reliably activates it.
- Stop the generic fallback-grid sequence after the exact row is selected. A
  later fallback click can change the selection to a different save (v9 first
  selected T163, then silently moved to T182). The overview gate caught the
  wrong turn before arena started.
- If the overview does not match the requested turn and civilization, restart
  the menu load from a clean game process; never resume against the wrong
  state.

Windows Application Control blocks `.venv\Scripts\python.exe` here. The wrapper
uses signed system Python plus the companion `.venv` packages; do not replace
it with Windows `uv run`. If the checkout/environment is missing, clone GitHub
there and run:

```bash
/mnt/c/Users/wrisl/.local/bin/uv.exe sync \
  --directory 'C:\Users\wrisl\dev\civ6-mcp' \
  --extra launcher-windows
```
