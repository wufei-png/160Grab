from grab.services.ocr import OcrClient


def test_ocr_client_disables_environment_proxy_inheritance(monkeypatch):
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1080")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1080")

    client = OcrClient("http://127.0.0.1:8000")

    assert client._client._trust_env is False
