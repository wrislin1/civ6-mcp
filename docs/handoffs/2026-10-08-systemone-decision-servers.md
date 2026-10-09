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

## Addendum 2026-10-09: Kev-9B and Von added (four local servers)

Two more SystemOne servers are live and vendored (brothereye `454be718`, vendor refresh in
this commit). Same wire protocol, same `Registry.systemone_url(id, network="lan")` call.

| id | Server | Host / compute | URL (LAN) | Resident | Cold / warm |
|---|---|---|---|---|---|
| `riz-gpu1-kev` | Kev-9B (jaredpalmer/kev-9b, LoRA + pointer head on Qwen3.5-9B-Base, Apache-2.0) | riz-llm GPU 1 | `http://192.168.20.196:11451/v1/systemone` | 21.7 GB while hot, 0 idle | 29 s / 0.1-0.25 s |
| `home-cpu-von` | Von 1.3 (wfzyx/von, 395M ModernBERT encoder, Apache-2.0) | home-llm **CPU** (OpenVINO) | `http://192.168.20.146:11451/v1/systemone` | RAM only, always resident | 2.3 s first / 0.1-0.45 s |

**GPU-1 eviction rule (matters for benchmark sequencing).** Clef-Flash (18.6 GB) and Kev-9B
(21.7 GB) share riz-llm GPU 1 and cannot both be hot. Each server POSTs the other's
`/unload` before loading, so a request to the cold one costs one ~29 s swap and never OOMs.
Alternating Clef and Kev per record pays a swap per record: run all records against one,
then the other. `GET /ready` on either shows `loaded` and `evict_urls`.

**Protocol notes.** Kev's front defaults `model` to `kev-latest` and rewrites the response
`model` to `kev-9b`; Kev also serves `POST /v1/systemone/permute` (option-order stability)
and `/separate`. Kev and Von report `usage.output_tokens` > 0 (they count answer tokens);
Clef and Laya report 0, so do not assert zero across servers. Von runs with
`--noul-decision raw` (calibrated P(yes); the default `band` mode would remap every noul
outside 0.2..0.8). Von is English-only. `home-cpu-von` is the first registry endpoint with
`gpu_indexes: []`; any conflict rule keyed on GPU must skip it (no `vram_*` fields in its
telemetry record either).

**Health shapes for the admission probe.** `GET {base minus /v1}/health` returns
`status == "ok"` on all four. Clef and Kev add `loaded: bool`; Laya `loaded: [...]`; Von
`engine: "von-1.3"` (always loaded).

**Evidence** (`services/clef/evidence/four-way-2026-10-09.json`, 5 records / 14 questions,
reference hosted Clef 27B): choice agreement Clef-Flash 5/5, Kev-9B 3/5, Laya 2/5, Von 2/5.
Kev is as decisive as the 27B (invoice overdue 1.00, barbarian threat noul 0.75 vs Flash's
0.49) but picks differently on both Civ records (Warrior over Library; surprise war over
pressure). Laya and Von both chose Settler on the city-build record. For the arena this
means: Clef-Flash is the local model that tracks the strongest reference; Kev is the local
model that commits hardest; the two encoders are the low-latency floor, not contenders.
