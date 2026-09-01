from ozon_parser.manual_capture import normalize_cdp_cookies, select_current_ozon_url


def test_cdp_cookies_keep_required_metadata():
    cookies = normalize_cdp_cookies(
        [
            {
                "name": "session",
                "value": "private",
                "domain": ".ozon.ru",
                "path": "/",
                "expires": 1_900_000_000.75,
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
                "priority": "High",
            },
            {
                "name": "session-cookie",
                "value": "private",
                "domain": "data.ozon.ru",
                "path": "/analytics",
                "expires": -1,
                "secure": True,
                "httpOnly": False,
            },
        ]
    )

    assert cookies[0] == {
        "name": "session",
        "value": "private",
        "domain": ".ozon.ru",
        "path": "/",
        "expiry": 1_900_000_000,
        "secure": True,
        "httpOnly": True,
        "sameSite": "Lax",
    }
    assert "expiry" not in cookies[1]


def test_current_url_selects_only_an_ozon_page():
    targets = [
        {"type": "page", "url": "https://example.com/"},
        {"type": "service_worker", "url": "https://data.ozon.ru/worker.js"},
        {"type": "page", "url": "https://data.ozon.ru/analytics"},
    ]

    assert select_current_ozon_url(targets) == "https://data.ozon.ru/analytics"
