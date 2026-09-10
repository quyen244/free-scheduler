# Step 1 - pipeline acceptance contract evidence

Status: complete  
Verified: 2026-09-09

## Result

Step 1 now has an executable, network-free contract for the desired pipeline.
It does not claim that the current production pipeline already passes the
contract; it defines the target that Steps 2 and 3 must implement.

## Frozen fixture expectations

| Fixture | Expected chunks | Target use |
|---|---:|---|
| 5:00 | 1 | lower accepted duration and single-chunk rule |
| 9:00 | 1 | upper single-chunk rule |
| 10:00 | 2 | first balanced multi-chunk case |
| 13:42 | 3 | main one-brand, seven-target acceptance case |
| 20:00 | 4 | upper accepted duration |

The contract also contains negative cases for 4:59, 20:01, below 720p,
corrupt media, zero chunks, missing assets, wrong target mapping,
pre-approval execution, stale approval, approval replay, and invalid TikTok
publication semantics.

## Test evidence

Command:

```powershell
python -m unittest discover -s automation/acceptance-contract -p "test_*.py" -v
```

Result:

```text
Ran 12 tests
OK
```

The suite uses only Python's standard library and makes no network or provider
calls.

## Verified current gap

A diagnostic run of the existing `whisper-transcript-service/chunker.py` with
one-second transcript segments produced:

```text
300s  -> 2 chunks: [240, 60]
540s  -> 3 chunks: [240, 240, 60]
600s  -> 3 chunks: [240, 240, 120]
822s  -> 4 chunks: [240, 240, 240, 102]
1200s -> 5 chunks: [240, 240, 240, 240, 240]
```

The desired counts are `1, 1, 2, 3, 4`. Therefore Step 2 must replace the
current greedy four-minute behavior with the accepted balanced policy. This is
a discovered implementation gap, not a Step 1 failure.

## Deferred calibration, not Step 1 blockers

- Select and record the exact OpenAI model and per-campaign budget after a
  cost-first fixture evaluation.
- Calibrate signature-music loudness, ducking, loops, fades, and missing-file
  behavior with real media.
- Confirm Shopee API credentials during the later commerce milestone.

## Files

- `automation/acceptance-contract/fixtures.json`
- `automation/acceptance-contract/contract.py`
- `automation/acceptance-contract/test_contract.py`
- `automation/acceptance-contract/README.md`
- `features/pipeline_acceptance_contract/spec.md`
- `features/pipeline_acceptance_contract/todo.md`
