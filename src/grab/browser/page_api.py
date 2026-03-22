class BrowserPageApi:
    def __init__(self, page, base_url: str = "https://www.91160.com"):
        self.page = page
        self.base_url = base_url

    async def get_json(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict:
        return await self.page.evaluate(
            """async ({path, params}) => {
                const url = new URL(path, "https://www.91160.com");
                Object.entries(params ?? {}).forEach(([key, value]) => {
                    url.searchParams.set(key, value);
                });
                const response = await fetch(url.toString(), {
                    credentials: "include",
                });
                return await response.json();
            }""",
            {"path": path, "params": params or {}},
        )
