class InvalidVideoIdError(Exception):
    """The requested video id is not the shape a YouTube id has."""


class TranscriptNotFoundError(Exception):
    """Nothing has transcribed this video yet."""


class EmptyTranscriptError(Exception):
    """There is a transcript, but nothing in it to speak."""


class UnknownVoiceError(Exception):
    """The requested voice is not one ZeroTTS ships.

    An enum, not a free string: ZeroTTS 0.1.2 still cannot build a voice from a
    reference clip, so anything outside the shipped set is a typo that would
    otherwise fail deep inside synthesis.
    """


class SynthesisError(Exception):
    """The model failed to produce audio for a segment."""


class VoiceTrackNotFoundError(Exception):
    """Nothing has voiced this video yet — render has nothing to lay under it."""


class NoChunksError(Exception):
    """The video has no chunk rows, so there is nothing to render."""


class PresetNotFoundError(Exception):
    """The named preset file is not on the shared volume."""


class RenderError(Exception):
    """ffmpeg refused a clip."""
