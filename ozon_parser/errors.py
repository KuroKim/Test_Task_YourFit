"""Предметные исключения с понятными сообщениями для пользователя."""


class OzonParserError(Exception):
    """Базовое исключение для ожидаемых ошибок приложения."""


class CookieFileError(OzonParserError):
    """Файл cookies отсутствует, небезопасен или имеет неверную структуру."""


class PageRequestError(OzonParserError):
    """HTTP-ответ не позволяет использовать карточку товара."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


class PageExtractionError(OzonParserError):
    """Встроенные данные товара отсутствуют или повреждены."""
