"""Domain-specific exceptions shown as concise messages by the entrypoints."""


class OzonParserError(Exception):
    """Base exception for expected application errors."""


class CookieFileError(OzonParserError):
    """The cookie file is missing, unsafe, or has an unexpected structure."""


class PageRequestError(OzonParserError):
    """A product page cannot be used because of its HTTP response."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


class PageExtractionError(OzonParserError):
    """Embedded product data is absent or malformed."""

