from typing import Literal

BrowserChannel = Literal["chromium", "chrome", "msedge"]
CHANNELS = {"chromium", "chrome", "msedge"}


def validate_browser_channel(channel: str) -> str:
    if not isinstance(channel, str) or channel not in CHANNELS:
        raise ValueError("browser.channel must be chromium, chrome or msedge")
    return channel
