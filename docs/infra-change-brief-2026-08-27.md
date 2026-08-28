# Brother Eye infrastructure change brief — 2026-08-27

Audience: consumer projects (Civ6 LLM arena, Living Emerald / Pokémon Emerald, Fallout NV / Living Vegas) and any agent session working in those repos. Paste or `@`-include this file in the project's CLAUDE.md / AGENTS.md. Everything here is live on both inference hosts as of 2026-08-27 (brothereye `main` @ `656f8ef2`).

## 1. What changed (one paragraph)

llama.cpp on both hosts was upgraded from a May-20 build to upstream `6fdd0ac8` (2026-08-27), which unlocked the 2026 model generation. Fourteen new model entries were added to the LiteLLM gateway catalogue, downloaded (~236 GB) and smoke-verified: **Qwen 3.8-27B**, **Granite 4.2 (3B/8B/30B)**, **Ornith 1.5 (9B, 35B-A3B)**, **Nemotron 3.5 Lightning 30B-A3B**, **Meta Muse-Glimmer-30B**, **poolside Laguna-XS-2.1**, **Microsoft Fara 1.5-9B**, **LFM2.5-8B-A1B**, **Ling-3.0-tiny**. Nothing was removed; every previous alias (gemma4-26b, qwen3.6-27b, devstral-24b, …) still works exactly as before. The deployment principle is **one complete model per GPU, no splitting**; the 5060 Ti on home-llm stays dedicated to the Kelex reranker.

## 2. How to call models (unchanged)

OpenAI-compatible gateway (LiteLLM):

| From | Base URL |
|---|---|
| LAN / other machines (Riz-PC, WSL, gaming PC) | `https://api.brothereye.net/v1` |
| riz-llm host processes | `http://127.0.0.1:4000/v1` |
| Docker containers on riz-llm | `http://litellm:4000/v1` |

Auth: `Authorization: Bearer $LITELLM_OPENAI_API_KEY` (value in `/opt/brothereye/.env` on riz-llm — never commit it). `GET /v1/models` lists everything routable right now. Health: `GET /health/readiness` (no auth).

Alias conventions: every local model has a base alias (`qwen3.8-27b`), a `-cpp` alias (same thing, explicit llama.cpp backend) and a `local/` prefix alias. Prefer the `-cpp` form in new code (`qwen3.8-27b-cpp`) — it can never resolve to a cloud or legacy Ollama route. Cloud: `gpt-oss-20b` only.

Client gotchas that apply to almost every new model:

- **They are thinking models.** The answer comes back in `message.content`; the chain of thought in `message.reasoning_content`. Send `max_tokens ≥ 512` (a 64-token cap returns an empty `content` because the budget was spent thinking). Granite 4.2 supports `thinking` on/off/low via its chat template; Qwen 3.8 is hybrid-thinking.
- **First request after idle takes 10–60 s** (llama-swap loads the model on demand; it unloads after 10 min idle). Budget for this in game-turn deadlines, or keep the model warm with a periodic tiny request.
- **One model per GPU at a time.** Requesting a different model on the same endpoint evicts the current one. Don't alternate between two large models on one GPU inside a hot loop.
- **Vision:** `qwen3.8-27b`, `ornith-1.5-*`, `muse-glimmer-30b`, `fara1.5-9b` accept OpenAI-style `image_url` content parts (base64 data URLs work).
- `fara1.5-9b` leaks a literal `</think>` into `content` — strip it if you use that model.

## 3. GPU topology and where each model lives

| GPU | Role | What runs there |
|---|---|---|
| riz-llm GPU 0 (3090) | visual/voice lane | ComfyUI, Halo, Herald; LLMs on demand only |
| riz-llm GPU 1 (3090) | primary inference | any single-GPU model below (`riz-gpu1-cpp` telemetry name) |
| home-llm GPU 0 (3090) | secondary inference / bake-off box | same single-GPU models, weight 25 in routing |
| home-llm GPU 1 (5060 Ti 16 GB) | **Kelex reranker — reserved** | nothing else (see §6) |
| riz-llm GPU 0+1 split | unified profile only | the three `-q8`/Nemotron split entries (§4) |

Living Emerald's dedicated session lanes (`living-emerald-llama@0/@1`, ports 11540/11541, acquired through the GPU-session controller) are unchanged.

## 4. The new models

Single-GPU entries (served on riz-gpu0, riz-gpu1 and home-gpu0; routable through the gateway today):

| Alias | What it is | Ctx | Vision | Est./measured VRAM @ctx | Best use |
|---|---|---|---|---|---|
| `qwen3.8-27b` | Qwen 3.8 27B dense, UD-Q4_K_M | 32K | yes | 22.0 est., fits one 3090 | **leading general/coding/agentic candidate**; successor to `qwen3.6-27b` |
| `granite4.2-30b` | IBM Granite 4.2 30B dense, Q4_K_M | 16K | no | 20.1 est., comfortable | stable tool-calling / structured output |
| `granite4.2-8b` | Granite 4.2 8B, Q8_0 | 32K | no | ~12.9 | fast lane; candidate for Living Emerald |
| `granite4.2-3b` | Granite 4.2 3B, Q8_0 | 32K | no | ~5 | classification, cheap turns |
| `ornith-1.5-35b` | Ornith 1.5 35B-A3B MoE, Q4_K_M | 32K | yes | 23.9 est. / **22.1 measured** — loadable, qualification pending | fast agentic coding challenger (3B active) |
| `ornith-1.5-9b` | Ornith 1.5 9B, Q8_0 | 32K | yes | ~12.8 | fast lane; candidate for Living Emerald |
| `muse-glimmer-30b` | Meta Muse-Glimmer 30B, Q4_K_M | 32K | yes | ~18.5 | general/VL alternative |
| `laguna-xs-2.1` | poolside Laguna-XS-2.1 33B/3B-active coder | 32K | no | 22.4 est., tight | agentic coding |
| `fara1.5-9b` | Microsoft Fara 1.5 9B computer-use VLM, Q8_0 | 32K | yes | ~12.8 | screen grounding / GUI agents |
| `lfm2.5-8b-a1b` | LiquidAI LFM2.5 8B/1B-active | 32K | no | ~6 | very fast router / draft model |
| `ling-3.0-tiny` | InclusionAI Ling 3.0 tiny MoE | 32K | no | ~5 | very fast small model |

Split entries (`gpu_layout: multi`, both riz-llm 3090s). **Not routable through the gateway while it runs the per-gpu profile** (current state); reachable only on the unified llama-swap `http://127.0.0.1:11444/v1` on riz-llm, or after `/ollama-mode unified`. Treat as optional inventory, not defaults:

| Alias | What | Ctx | VRAM |
|---|---|---|---|
| `qwen3.8-27b-q8` | Qwen 3.8 27B Q8_0 | 32K | ~32 GB split |
| `granite4.2-30b-q8` | Granite 4.2 30B Q8_0 | 64K | ~39 GB split |
| `nemotron-3.5-lightning-30b` | NVIDIA Nemotron 3.5 Lightning 30B-A3B Q8_0 (hybrid Mamba; 1M native ctx) | 131K | ~33 GB split; KV only 0.4 GB | long-context execution layer |

Verification level so far: **load + short arithmetic answer on every entry, on both hosts.** Not yet tested: tool calling, JSON/structured output, vision quality, long-context accuracy, concurrency, peak VRAM under load. Vendor benchmarks were run at larger context and higher precision than these deployments.

## 5. Recommendation per project (starting points, not verdicts)

- **Civ6 LLM arena:** start the bake-off with `qwen3.8-27b-cpp` vs `granite4.2-30b-cpp` vs `ornith-1.5-35b-cpp` on recorded game states with the real civ6-mcp tool calls; `qwen3.8-27b-q8` (split, unified profile) only for a single maximum-quality player. Arithmetic/coding scores do not predict strategic play — judge on the recorded states. Keep turn deadlines aware of the 10–60 s cold-load.
- **Living Emerald:** benchmark `granite4.2-8b-cpp` vs `ornith-1.5-9b-cpp` against the existing dialogue/persona/structured-output gate under the 4-second deadline; `gemma4-26b-cpp` stays the quality baseline until one of them wins that gate. Session lanes unchanged.
- **Fallout NV / Living Vegas:** no model decision needed yet (Rung 0 has no LLM integration). When it does, `qwen3.8-27b-cpp` is the default candidate; `fara1.5-9b-cpp` if you need screen grounding.

Run bake-offs on **home-llm's 3090** (`home-gpu0`) so riz-llm's GPUs stay free; target it directly at `http://192.168.20.146:11440/v1` (llama-swap, no auth on LAN) when you need to pin the endpoint instead of letting the gateway route.

## 6. Constraints you must not fight

- **5060 Ti ↔ Kelex reranker is exclusive by design.** `kelex-reranker-worker.service` on home-llm has `Conflicts=ollama@1.service llama-swap@1.service`; starting either 5060 unit tears the reranker down and Kelex research degrades. Manifest entries with `5060` placement exist but are intentionally unserved. Don't enable `llama-swap@1` from a project.
- **Don't hand-edit generated configs.** Models are declared in `/opt/brothereye/models.yaml` → `tools/scripts/gen-configs.py` → LiteLLM/llama-swap configs (+ `peer-sync` to home-llm). New model requests go through that path, in the brothereye repo.
- **Local Ollama on riz-llm is retired.** Anything still hard-coding `:11434` on riz-llm is talking to a stale loopback stub; use the gateway.
- **llama.cpp rebuild recipe:** `/home/riz/llama-cpp-src/build-llamacpp.sh` (Docker, portable AVX2, `GGML_CUDA_NCCL=OFF`), deployed to riz-owned `/opt/llama.cpp` on both hosts; `/opt/llama.cpp/COMMIT` records the version. Rollback copies of the Aug-9 build are under `/home/riz/llama-cpp-src/backup-*`.

## 7. Quick smoke you can paste

```bash
K=$(grep -E '^LITELLM_OPENAI_API_KEY=' /opt/brothereye/.env | cut -d= -f2-)
curl -s https://api.brothereye.net/v1/chat/completions -H "Authorization: Bearer $K" \
  -H 'content-type: application/json' \
  -d '{"model":"qwen3.8-27b-cpp","messages":[{"role":"user","content":"What is 17*3? Just the number."}],"max_tokens":512}' \
  | jq -r '.choices[0].message | {content, reasoning_len: (.reasoning_content|length)}'
```

Expected: `content: "51"`, non-zero `reasoning_len`, first call 20–45 s, subsequent calls ~1 s.

## 8. Where the details live (brothereye repo)

- `models.yaml` — the catalogue (single source of truth), incl. per-model notes on fit status
- `CLAUDE.md` — "2026-08-27 model refresh" and "llama.cpp build + deploy" entries
- `docs/sdrait-slice-8-progress-2026-08-27.md` — why the SDRAIT fine-tune campaign is paused (GPU contention; unrelated to your projects but explains the GPUs being free)
- `tools/scripts/model-placement-report.py` — live "what is where" report
