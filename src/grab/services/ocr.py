import httpx


class OcrClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url
        self._client = client or httpx.AsyncClient(base_url=base_url, trust_env=False)

    async def recognize(self, image_bytes: bytes) -> str:
        response = await self._client.post(
            "/ocr",
            files={"file": ("captcha.png", image_bytes, "image/png")},
        )
        response.raise_for_status()
        return response.json()["result"]
