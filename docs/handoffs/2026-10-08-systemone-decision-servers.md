# Handoff: SystemOne decision-model servers (Clef-Flash + Laya)

Date: 2026-10-08. Source of truth for the infra side is the brothereye repo
(`services/clef/`, `models.yaml`, `infra/registry/endpoints.json`), main `9455ddc3`.
This file is for the civ6-mcp session on the gaming PC: what exists, how to call it,
and what civ6-mcp still has to build.

## What is live

| Server | Host / GPU | URL (LAN) | Model | Resident VRAM | Cold / warm latency |
|---|---|---|---|---|---|
| Clef-Flash | riz-llm, GPU 1 (RTX 3090) | `http://192.168.20.196:11450/v1/systemone` | `Cloudflare/clef-flash` (9B, Apache-2.0) | 18.6 GB while hot, **0 when idle** | 28 s cold, 0.2 s warm |
| Laya 0.4.1 | home-llm, GPU 1 (RTX 5060 Ti) | `http://192.168.20.146:11450/v1/systemone` | `convaiinnovations/laya` x3 checkpoints (421M each) | ~4.8 GB, idle-unloads after 10 min | ~0.1 s |

Both speak Jev's `POST /v1/systemone` wire protocol, so one civ6-mcp adapter covers
local Clef, local Laya, hosted Clef on Workers AI, and Jev itself. No auth on either
server; they are LAN-only like llama-swap. Neither is a chat model: `/v1/chat/completions`
returns 404 on both, so the benchmark's current chat-completion admission probe will fail
against them by design (see "civ6-mcp work" below).

Clef 27B (55 GB) does not fit any local card. Use the hosted `@cf/cloudflare/clef` on
Workers AI as the quality reference (see "Hosted Clef").

## Wire protocol

Request body (identical for Clef and Laya):

```json
{
  "model": "clef-flash",
  "state": {"turn": 84, "city": {"name": "Roma", "population": 7}},
  "questions": {
    "build": {"type": "choice", "instructions": "What should Roma build next?",
              "criteria": {"Settler": "Found a new city.", "Library": "+2 science."}},
    "threatened": {"type": "noul", "instructions": "Is the city under military threat?"},
    "expand": {"type": "score", "instructions": "How strongly prioritise expansion?",
               "criteria": ["not at all", "slightly", "moderately", "strongly"]}
  }
}
```

- `state` may be a string or any JSON value. Clef also accepts `images` / `videos`.
- `model` is optional on the Clef server (defaults to `clef-flash`); Laya ignores it
  for routing unless you pin a checkpoint (`english`, `multilingual`, `typed-decisions`).
- Question types: `choice` (criteria is a map option -> description), `noul` (yes/no,
  no criteria), `score` (criteria is an ordered list of level labels).
- Clef token budget is 16384 tokens per request. Laya caps at 100 options per choice
  question and trims option descriptions once they exceed its head budget.

Response body:

```json
{
  "model": "clef-flash",
  "answers": {
    "build": {"type": "choice", "choice": "Library", "confidence": 0.253,
              "probabilities": {"Settler": 0.11, "Library": 0.253, "...": 0}},
    "threatened": {"type": "noul", "noul": 0.494},
    "expand": {"type": "score", "score": 1.41, "confidence": 0.5,
               "legend": {"0": "not at all", "1": "slightly", "2": "moderately", "3": "strongly"},
               "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5, "3": 0.0}}
  },
  "usage": {"input_tokens": 340, "output_tokens": 0}
}
```

`output_tokens` is always 0: these are single-forward-pass heads, not generators.
Laya adds `answer_confidence`, `abstention`, `action`, and a `routing` block; its
`confidence` is 1 minus normalised entropy, which is NOT Clef's `confidence`
(Clef's is simply the chosen option's probability). Compare on `probabilities`,
never on `confidence`, when mixing the two.

Curl smoke:

```bash
curl -s http://192.168.20.196:11450/v1/systemone -H 'content-type: application/json' -d '{
  "state": {"invoice": {"vendor": "Acme", "total": 1250.0, "status": "overdue"}},
  "questions": {"status": {"type": "choice", "instructions": "Invoice status?",
                "criteria": {"paid": "Paid.", "overdue": "Past due.", "draft": "Not sent."}},
                "large": {"type": "noul", "instructions": "Total above 1000 USD?"}}}'
```

Expected: `overdue` at ~0.97, `noul` ~0.97 on Clef; `overdue` ~0.98, `noul` ~0.85 on Laya.

## Per-server operational contract

**Clef (riz-llm).** Routes: `GET /health` (never loads the model), `GET /ready`
(adds `loaded`, `idle_s`, load/unload counters), `GET /v1/models`, `POST /v1/systemone`,
`POST /unload`. The model lives in a child process spawned on the first request and
killed after 600 s idle or on `POST /unload`; killing it releases the whole CUDA context.
The first request after idle blocks ~28 s. Errors: loader `ValueError` -> 422, CUDA OOM
-> 503, worker death or load failure -> 503, inference timeout -> 504. Requests are
serialized (one GPU, one worker); send them sequentially or expect queueing.

GPU 1 on riz-llm is shared with Effigy, Patchwork, Metron and the unified llama-swap.
Nothing evicts Clef for them, so if a benchmark run coincides with an Effigy job, expect
contention. `POST /unload` frees it on demand. The brothereye GPU-lease (fine-tune lane)
does stop and restore `clef.service` around a GPU-1 lease.

**Laya (home-llm).** Routes: `GET /health`, `POST /v1/systemone`,
`POST /v1/systemone/batch` (`{"states": [...], "questions": {...}}`, up to 64 states).
All three checkpoints preload at unit start and unload after 10 min idle (reload is a
few seconds from the Hub cache). home-llm GPU 0, the benchmark subject GPU, is untouched.

## Registry (vendored into civ6-mcp)

`src/civ_mcp/_vendor/endpoints.json` (vendor commit `28f7a72`, already pulled on the
gaming PC) now carries two endpoints of kind `systemone`:

| id | host | gpu | port | units | lan base |
|---|---|---|---|---|---|
| `riz-gpu1-clef` | riz-llm | 1 | 11450 | `clef.service` | `http://192.168.20.196:11450/v1` |
| `home-gpu1-laya` | home-llm | 1 | 11450 | `laya.service` | `http://192.168.20.146:11450/v1` |

They carry `lan` and `host_loopback` URLs only (no `litellm` context: the gateway cannot
proxy them), `modes: ["decision"]`, `acquisition: "direct"`. The vendored client:

```python
from civ_mcp._vendor import brothereye_registry as reg
registry = reg.load_snapshot(path_to_endpoints_json)
registry.systemone_endpoint_ids()                        # ('home-gpu1-laya', 'riz-gpu1-clef')
registry.systemone_url("riz-gpu1-clef", network="lan")  # 'http://192.168.20.196:11450/v1/systemone'
registry.openai_endpoint_ids()                           # unchanged; systemone ids excluded
registry.openai_url("riz-gpu1-clef", network="lan")     # raises ValueError
```

The live copy at `https://calculator.brothereye.net/endpoints.json` already serves the
same payload, so the remote-first loader works too.

## Telemetry / conflict detection

The brothereye telemetry daemons publish `llm:endpoint:riz-gpu1-clef` and
`llm:endpoint:home-gpu1-laya` to the router Redis every 3 s with the usual fields:
`healthy`, `loaded_models`, `available_models`, `gpu_index`, `vram_free_mb`, etc.
`loaded_models` is `["clef-flash"]` only while Clef's worker is up, and Laya's loaded
checkpoint list. Both are GPU-index 1 on their host, so any conflict rule keyed on
(host, gpu_index) sees them.

Note: the router Redis had been network-detached since the 2026-09-16 boot and was
recreated today, so any `llm:endpoint:*` reads before 2026-10-08 20:57 UTC were empty.

## Hosted Clef (quality reference)

Workers AI serves `@cf/cloudflare/clef-flash` and `@cf/cloudflare/clef` (27B) at
`POST https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/run/<model>` with the
same request body; the response is wrapped as `{"result": <systemone response>, "success": true}`.
The account-scoped Workers AI token and account id live only in `/opt/brothereye/.env` on
riz-llm (`CLOUDFLARE_WORKERS_AI_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`); they are not on the
gaming PC. Mean hosted latency ~0.5 s.

Parity evidence (`services/clef/scripts/compare-hosted.py`, 5 records / 14 questions
including two Civ-style states): local Clef-flash vs hosted clef-flash max |delta p| =
0.006, zero choice disagreements. So the local server is the real decision head.

Observation that matters for model choice: **Clef 27B is far more decisive on game
states than Clef-flash.** City-build pick: Library 0.58 (27B) vs 0.25 (flash);
barbarian-threat noul: 0.81 vs 0.49 (essentially undecided). Flash tracks the 27B's
direction with flatter distributions. Laya answered the same records sensibly but its
confidence scale is different (see protocol section).

## civ6-mcp work still owed

1. **Decision-model backend class** in the arena, keyed on registry kind `systemone`,
   using `registry.systemone_url(...)`. Request and answer dataclasses matching the
   protocol above; treat `choice` probabilities as the primary signal.
2. **Admission probe for that kind**: `GET {base minus /v1}/health` returning JSON with
   `status == "ok"` (both servers), optionally `GET /ready` on Clef to decide whether to
   budget a ~30 s cold start before the first timed decision. Do not send a chat
   completion; it is a 404.
3. Optional **warm-up step** before timed runs: one throwaway Clef request, then verify
   `/ready` reports `loaded: true`.
4. If the benchmark compares against hosted 27B, run that leg from riz-llm (token lives
   there) or copy the two env values across deliberately.
