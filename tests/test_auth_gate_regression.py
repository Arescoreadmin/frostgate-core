import httpx

BASE = "http://127.0.0.1:8000"

def test_ui_token_is_public_and_sets_cookie():
    r = httpx.get(f"{BASE}/ui/token", params={"api_key": "supersecret"})
    assert r.status_code == 200
    assert "set-cookie" in r.headers
    assert "fg_api_key=" in r.headers["set-cookie"]

def test_ui_feed_without_cookie_is_401_not_500():
    r = httpx.get(f"{BASE}/ui/feed")
    assert r.status_code == 401
    assert r.headers.get("content-type","").startswith("application/json")

def test_ui_feed_with_cookie_is_200_html():
    cj = httpx.Cookies()
    r = httpx.get(f"{BASE}/ui/token", params={"api_key": "supersecret"})
    cj.extract_cookies(r)
    r2 = httpx.get(f"{BASE}/ui/feed", cookies=cj)
    assert r2.status_code == 200
    assert "text/html" in r2.headers.get("content-type","")
