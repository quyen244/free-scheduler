class MetadataError(Exception):
    """A typed failure safe to persist without source text or credentials."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        response_id: str | None = None,
        response_model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.response_id = response_id
        self.response_model = response_model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens
