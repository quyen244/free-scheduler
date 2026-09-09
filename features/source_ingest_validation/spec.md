# Source ingest and validation

Status: proposed
Priority: P0
Depends on: foundation and rights
Consumed by: transcription, chunking, campaign review

## Outcome

Accept a YouTube URL exactly once, establish a stable source identity, and stop
invalid media before expensive transcription or rendering. Local-file import is
deferred until URL ingestion is reliable.

## Processing flow

```mermaid
flowchart TD
    I[YouTube URL] --> N[Normalize submission]
    N --> D{Existing source?}
    D -->|Active campaign| O[Open existing campaign]
    D -->|Failed/published| R[Offer resume or new campaign]
    D -->|New| G[Download YouTube source]
    G --> P[ffprobe + SHA-256]
    P --> V{Valid media, 5-20 min,<br/>at least 720p, rights known?}
    V -->|No| B[needs_action with reason]
    V -->|Yes| T[Ready for transcription]
```

## State lifecycle

```mermaid
stateDiagram-v2
    [*] --> submitted
    submitted --> validating
    validating --> ready_for_transcription
    validating --> needs_action: invalid or missing information
    validating --> duplicate_active: active source found
    needs_action --> validating: corrected and resumed
    duplicate_active --> [*]
    ready_for_transcription --> [*]
```

## Data relationships

```mermaid
erDiagram
    SOURCE_VIDEO ||--o{ SOURCE_SUBMISSION : identified_by
    SOURCE_VIDEO ||--o{ CAMPAIGN : reused_by
    CAMPAIGN ||--o{ PROCESSING_ATTEMPT : executes
    SOURCE_VIDEO ||--o{ SOURCE_ARTIFACT : stores
```

`SOURCE_VIDEO` holds normalized YouTube ID and downloaded content hash. A
submission records the URL used. A failed `PROCESSING_ATTEMPT` never makes the
source permanently unusable.

## Edge cases and recovery

| Case | Behavior | User recovery |
|---|---|---|
| URL belongs to running work | Do not duplicate it | Open the existing campaign |
| Previous campaign failed | Keep history and reusable artifacts | Retry failed stage or restart as new campaign |
| Previous campaign published | Warn about intentional duplicate publishing | Explicitly create a new campaign |
| URL changes but bytes match | Detect downloaded SHA-256 match | Choose existing source or intentional new campaign |
| Source was deleted remotely | Preserve history and mark download failure | Replace URL or stop |
| Corrupt, out-of-range, low-resolution, or unknown-rights input | Stop before transcription | Correct the input/rights and resume validation |

## Done when

Fixtures prove YouTube URL identity, validation boundaries, duplicate-active
protection, failed-stage resume, and intentional reprocessing without
permanently blacklisting a URL.
