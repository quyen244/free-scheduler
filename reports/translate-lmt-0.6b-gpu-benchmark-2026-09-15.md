# LMT-60-0.6B GPU translation benchmark

Date: 2026-09-15  
Decision: **do not promote LMT-60-0.6B to the live pipeline yet.**

## Scope

This user-authorized benchmark used the first 20 English segments (0.000s to
86.640s) from:

```text
automation/data/hS3VXBeEv0I/transcript.json
```

The source has `language: en` and 243 segments. The test preserved the
production rule of translating one segment per request.

| Item | Value |
| --- | --- |
| GPU | NVIDIA GeForce GTX 1660 Ti, 6,144 MiB, compute capability 7.5 |
| Candidate | `NiuTrans/LMT-60-0.6B`, `Q4_K_M` GGUF |
| Candidate file | 484,220,000 bytes; local SHA-256 `743c6cdc13294b5c470ec0e89e79d64f96ef0cab94ce1285c78da4dc0f56be5c` |
| GPU runtime | `ghcr.io/ggml-org/llama.cpp:server-cuda`, `-ngl 99`, context 2,048 |
| Control | existing `HY-MT1.5-1.8B Q4_K_M`, same GPU runtime and context |
| Timing limit | project duration model at `1.35x` maximum voice speed-up |

The server model-load measurements were 43.2s for LMT and 40.4s for HY-MT.
Those cold-load figures are not included in steady-state translation speed.
The first request after each load also built CUDA graphs (39.2s LMT, 35.7s
HY-MT), so it is reported separately rather than mislabeled as normal cue
latency.

## Results

| Profile/run | Warm 20-cue time | Steady mean/cue | Timing fit | Quality result |
| --- | ---: | ---: | ---: | --- |
| LMT minimal prompt, GPU | 3.191s | 0.160s | 19/20 first pass | No instruction echo; four material semantic/proper-name errors found in review. |
| LMT subtitle-constrained prompt, GPU | 3.533s | 0.177s | 16/20 | **Rejected**: cues 3, 11, and 19 echoed the prompt instead of translating. |
| HY-MT current prompt, GPU | 6.740s for the 19 requests after graph warm-up | 0.355s | 12/20 first pass | More natural in several cues, but still four material errors; the production budget retry was not included in this raw-server control. |
| LMT minimal profile through `translate-service`, CPU | 11.701s | 0.585s per model call | 20/20 after retry | DI, model loading, alignment and production timing-budget code all worked; 22 calls total (20 first-pass + 2 retry calls). |

GPU warm LMT generated about 200–225 output tokens/second and evaluated prompts
at roughly 1,100–1,500 tokens/second. It used about 0.6 GiB above the existing
desktop/render allocation while loaded. The temporary benchmark containers were
removed after measurement.

## Quality review

The source transcript itself contains likely Whisper errors or truncated cues,
including `comment` where the intended word may be `comet`, and cue 11's
`navigated turn`. Those were not counted as model faults.

The minimal LMT profile was fast and non-empty in all 20 responses, but the
following defects block unattended video production:

| Cue | Source meaning | LMT output issue |
| ---: | --- | --- |
| 7 | NASA Jet Propulsion Laboratory | mistranslated as `Phòng thí nghiệm Lực lượng Tàu` |
| 8 | Angeles National Forest | mistranslated as `Sức sống quốc gia Angeles` |
| 12 | no tracks leading into the brush | became `không có tiếng bắt đầu đi vào rặng cây` |
| 16 | above-top-secret clearances | became `mật mã cao` |

The long subtitle prompt was designed from subtitle-translation guidance: keep
facts/names/numbers/tone, omit only spoken filler, and state a cue-length
limit. On this 0.6B model it was too instruction-heavy and was reproduced in
three outputs. The short, model-native prompt is therefore the only viable
LMT-0.6B prompt tested here, but it does not clear semantic QA.

The existing HY-MT control also had material errors (for example, Antarctica
became Arctic and canine units became goat units), so neither model has earned
unreviewed publishing. HY-MT nevertheless remains the live default because it
is already integrated and the new candidate has not demonstrated a quality
improvement.

## Implementation and verification

`translate-service` remains one FastAPI service and one job pipeline. A new
`TranslationProfile` dependency owns model-specific prompt and sampling
policy; `hunyuan-mt`, `lmt-60`, and `lmt-60-subtitle` are injected through
`TRANSLATE_PROFILE`. The job API, SQLite state, alignment guard, timing budget,
callback behavior, and n8n workflow have no model-family branches.

Verification passed:

```text
10 passed: profile injection plus existing prompt, truncation and seed tests
docker compose config --quiet
git diff --check
```

## Recommendation

For the requested fastest production candidate, LMT-60-0.6B has excellent GPU
throughput but fails the quality gate. Keep `hunyuan-mt` as the configured
default. The next justified experiment is `LMT-60-1.7B Q4_K_M` with the same
20-cue suite: it should remain small enough for the GTX 1660 Ti while testing
whether the missing capacity fixes proper nouns and factual terms. Do not
change the live workflow or start an E2E source run until that comparison has a
quality pass.
