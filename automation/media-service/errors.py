class InvalidURLError(Exception):
    """The request URL doesn't look like a YouTube video URL."""


class DownloadError(Exception):
    """yt-dlp failed to fetch the media for an otherwise valid URL."""


class AudioExtractionError(Exception):
    """ffmpeg failed to derive audio from an already-downloaded video."""


class SourcePolicyError(Exception):
    """The source is readable enough to classify but violates the MVP policy."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
