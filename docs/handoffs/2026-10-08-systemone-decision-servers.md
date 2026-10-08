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

## civ6-mcp consumer design requirements

The server handoff above supersedes the earlier assumption that local Clef deployment is outstanding and the blanket exclusion of Laya. It records local Clef-Flash and Laya services plus a hosted Clef 27B reference. These are candidates for a separately preregistered architecture comparison, not additions to the five-model screen or Stage 3 injections. The library freeze and same-menu LLM control still apply. No decision-model request, including a warm-up, may use a library position during Part 1.

Repository checks confirm `Registry.systemone_endpoint_ids()` and `Registry.systemone_url()` already exist, and `openai_url()` rejects these endpoints. The arena's `endpoint_registry.resolve_gateway()` and `benchmark_backend.probe_health()` remain chat-specific; the latter calls `backend.chat(...)`. There is no arena SystemOne backend yet. Keep this transport separation in the benchmark scripted actor and runner; a decision response must not be fabricated into a `Reply` or tool-call batch.

| Local endpoint | Registry host / GPU | Decision URL | Server behavior recorded in the handoff |
|---|---|---|---|
| `riz-gpu1-clef` | `riz-llm` / 1 | `http://192.168.20.196:11450/v1/systemone` | Clef-Flash lazy-loads a child worker; about 28s cold versus 0.2s warm, 600s idle unload, `/ready` and `/unload`. |
| `home-gpu1-laya` | `home-llm` / 1 | `http://192.168.20.146:11450/v1/systemone` | Laya preloads three checkpoints, unloads after ten idle minutes, and has a separate batch route. |

The separate design and implementation plan must cover these concrete dependencies:

1. **Decision transport and identity.** Build a dedicated typed request/answer backend for `state` and `questions`, keyed by registry kind `systemone` and resolved with `systemone_url(..., network="lan")`. Preserve `choice`, `noul`, and ordered `score` answer shapes, question IDs, raw probabilities, usage and Laya routing/abstention fields. Pin the requested and actually routed checkpoint: Laya's generic `model` field is not by itself evidence of checkpoint identity. Keep unsupported seed control unavailable. Do not route these endpoints through LiteLLM or the OpenAI chat adapter. Local and hosted transports can share protocol types, but hosted URL, authentication and response-envelope handling differ.
2. **Health and protocol admission.** For local servers, request root `/health` and require JSON `status == "ok"`; never send `/v1/chat/completions`, whose reported 404 is expected. Health proves server availability, not a loaded model or valid decisions. A synthetic, non-library SystemOne probe must additionally validate answer shape, selected option membership, probability coverage/range and model/routing identity. Preserve distinct configuration/protocol errors (including 422), capacity/load/worker errors (503), and inference timeout (504); none is a scored game decision. The current chat admission probe stays intact for chat models.
3. **Cold and warm timing.** Preregister whether runs include loading or use a synthetic warm-up outside timed decisions. Clef health does not load its worker; `/ready` can verify `loaded: true` after warm-up. Laya has no `/ready` route in this contract, so verify its actual inference/routing result and operational evidence rather than borrowing Clef's route. Record cold start, queue wait where observable, warm inference, unload/reload events, and the full decision pipeline separately. Serialize Clef requests. Batch inference and Laya's up-to-64-state route are separate operating conditions, not an unreported latency advantage. The chat benchmark's fifteen-round allowance and latency formula do not define a SystemOne decision budget; the separate experiment must define its budget and same-menu control.
4. **One observable candidate menu and comparable signals.** Generate legal candidate IDs/descriptions from public tool observations, with the same state facts, IDs, ordering and descriptions for the decision actor and its same-menu LLM control. Freeze menu and observation digests before selection; neither generator nor selector receives rubric targets or scoring snapshots. Account for Clef's recorded 16,384-token request budget and Laya's 100-option cap/description trimming when constructing a common bounded input. Detect effective truncation instead of assuming identical submitted JSON means identical observed menus. Freeze invalid-choice, tie and abstention handling before trials. Compare choice probabilities; Clef's chosen-option probability and Laya's entropy-derived `confidence` are different statistics. Do not translate `output_tokens: 0` into zero inference cost.
5. **GPU isolation and telemetry.** Reuse measured process/GPU evidence from `benchmark_live_evidence.collect_gpu_evidence` and the existing conflict gates. The Redis keys `llm:endpoint:riz-gpu1-clef` and `llm:endpoint:home-gpu1-laya` can supplement that evidence with health, loaded models and free VRAM, keyed by `(host, gpu_index)`. Record freshness; missing/stale telemetry is unknown, not an idle GPU. Reads before `2026-10-08 20:57 UTC` were empty according to the outage record and cannot establish historical isolation. The vendored `home-gpu0-cpp` is GPU0 and Laya GPU1, but revalidate each experiment's actual model placement. Clef shares riz-llm GPU1 with other workloads; warm-up or `/unload` changes residency, so refresh conflict evidence around those transitions. The current gate uses direct GPU/process evidence, not Redis as its sole source.
6. **Hosted reference and interpretation limits.** Support Workers AI's account-scoped URL and `{"success": true, "result": ...}` envelope for `@cf/cloudflare/clef-flash` and `@cf/cloudflare/clef`; validate envelope errors separately from decision content. Prefer running that leg on riz-llm, where the existing account/token configuration resides, rather than moving credentials as part of this plan. The reported max probability difference of 0.006 with no choice disagreements covers five records/fourteen questions; it is a small parity check, not general equivalence. The sharper 27B distributions on the reported game-like states justify considering that reference, but do not establish better decisions, calibration or benchmark scores. Laya availability likewise does not establish quality or remove the need for a controlled evaluation.

These dependencies are the handoff for a separate design, not extra [Part 1 implementation tasks](../superpowers/plans/2026-10-08-arena-benchmark-plan-3-part-1.md) or gates. Part 1 delivers verified public discoverability, archives, scoring and script evidence for that future consumer. Server availability alone does not alter the screen roster, experimental architecture, held-out access rules, or the prohibition on model-informed position tuning.
