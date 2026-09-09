class InvalidVideoIdError(Exception):
    """The requested video id is not the shape a YouTube id has."""


class TranscriptNotFoundError(Exception):
    """Nothing has transcribed this video yet."""


class MisalignedTranslationError(Exception):
    """The model returned something that no longer lines up with the input.

    Raised rather than repaired. A segment list that comes back one short
    shifts every subtitle after it, and that surfaces three stages later as a
    voice track out of sync with the picture — miserable to trace back to here.
    """


class TranslationError(Exception):
    """The model failed to produce a translation at all."""


class TruncatedTranslationError(Exception):
    """Generation stopped at the token cap, so the text is cut off mid-sentence.

    Only the budgeted second pass raises this. A truncated subtitle is worse
    than a rushed one, so the candidate is discarded and the first pass stands.
    """
