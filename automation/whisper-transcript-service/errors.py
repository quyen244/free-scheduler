class InvalidVideoIdError(Exception):
    """The requested video id is not the shape a YouTube id has."""


class MediaNotFoundError(Exception):
    """No audio on the shared volume for this video id — nothing ingested it."""


class TranscriptionError(Exception):
    """Whisper failed to transcribe an already-downloaded audio file."""


class TranscriptNotFoundError(Exception):
    """Nothing has transcribed this video yet."""


class EmptyTranscriptError(Exception):
    """A transcript arrived with no segments to chunk."""


class ChunkBoundaryError(Exception):
    """Balanced chunks cannot be placed safely on transcript boundaries."""
