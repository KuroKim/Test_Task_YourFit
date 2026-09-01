import pytest
import requests

from ozon_parser.errors import PageRequestError
from ozon_parser.http import validate_response


def _response(status=200, text="<html>product</html>", url="https://www.ozon.ru/product/123/"):
    response = requests.Response()
    response.status_code = status
    response._content = text.encode("utf-8")
    response.encoding = "utf-8"
    response.url = url
    return response


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(403, "http_403"), (404, "http_404"), (429, "http_429"), (503, "http_5xx")],
)
def test_known_http_errors_are_classified(status, error_type):
    with pytest.raises(PageRequestError) as error:
        validate_response(_response(status=status))
    assert error.value.error_type == error_type


def test_antibot_page_is_classified():
    with pytest.raises(PageRequestError) as error:
        validate_response(_response(text="<html><body>CAPTCHA</body></html>"))
    assert error.value.error_type == "antibot"


def test_authorization_redirect_is_classified():
    with pytest.raises(PageRequestError) as error:
        validate_response(_response(url="https://id.ozon.ru/login"))
    assert error.value.error_type == "authorization_redirect"


def test_empty_page_is_classified():
    with pytest.raises(PageRequestError) as error:
        validate_response(_response(text="   "))
    assert error.value.error_type == "empty_page"


def test_regular_page_passes_validation():
    validate_response(_response())

