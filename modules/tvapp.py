import re
import httpx
import logging
import asyncio

from time import time
from html import unescape
from typing import Optional
from modules.extractor import Extractor
from modules.domain_data import DOMAIN_IPS, DOMAIN_ROUTES

class TheTvApp:
    DOMAIN = "thetvapp.to"
    IP = "195.128.248.251"
    BASE = f"https://{DOMAIN}"
    SERVER_IPS = DOMAIN_IPS["server"]

    # TheTvApp arbitrarily blocks access to important data,
    # this will ensure that we always have access to the existing channels...
    CHANNELS = DOMAIN_ROUTES

    def __init__(self, **kwargs) -> None:
        self.extractor = Extractor()
        self.logger = logging.getLogger("TvApp")
        self.session = httpx.AsyncClient(
            timeout=httpx.Timeout(999)
        )

        self.keys = {
            0: None,
            1: None,
            "app_script": None,
            "last_updated": 0
        }
        self.csrf = ""
        self.token = ""

    @staticmethod
    def format_domain(url: str) -> str:
        domain = url.split("/")[2]
        data = TheTvApp.SERVER_IPS[domain]
        return url.replace(domain, data["ip"])

    async def update_keys(self, url) -> None:
        if url == self.keys["app_script"]:
            return None

        req = await self.session.get(url)
        if not req.is_success:
            self.logger.warning("Failed to get app.js url, retrying in 5 seconds...")
            await asyncio.sleep(5)
            return await self.update_keys(url)

        keys = self.extractor.get_keys(req.content)
        self.logger.info(f"Keys - {keys}")

        now = int(time())
        diff = abs(self.keys['last_updated'] - now)
        self.logger.info(f"Updating keys, last updated '{diff}'...")
        self.keys[0] = keys[0]
        self.keys[1] = keys[1]
        self.keys["app_script"] = url
        self.keys["last_updated"] = now

    async def fetch_app_url(self, url: str) -> Optional[str]:
        self.logger.debug("Attempting to retrieve client data...")
        req = await self.session.get(url, follow_redirects=True)
        if not req.is_success:
            self.logger.warning(f"Failed to get '{req.url}'!")
            return None
        csrf = re.search(r"\<meta name=\"csrf-token\"\scontent=\"(\w+)\"\>", req.text)
        if csrf and csrf.group(1) != self.csrf:
            self.logger.debug(f"Current CSRF: {self.csrf}, New CSRF: {csrf.group(1)}")
            self.csrf = csrf.group(1)
        elif not csrf:
            self.logger.warning(f"No csrf found in {req.url}...")
        module_scripts = re.findall(r"\<script\s?type=\"module\"\s?src=\"([^\"]+)\"\>", req.text)
        if module_scripts:
            script = [script for script in module_scripts if re.search(r"app-\w+\.js", script)]
            if len(script) != 1:
                self.logger.debug(f"Target scripts: {module_scripts}")
                raise ValueError("Too many/No app script(s)!")
            self.logger.debug(f"Script URL: {script[0]}")
            if self.keys["app_script"] != script[0]: 
                await self.update_keys(script[0])
        return req.text
            
    async def get_stream(self, name: str, route: str, token_route: str, _base: str) -> tuple[str, Optional[str]]:
        # assert self.csrf, "Cannot call get_stream before csrf has been set!"
        if not self.csrf:
            await self.fetch_app_url(TheTvApp.BASE + route)

        if token_route == "/token/":
            self.logger.warn(f"'{name}' not ready yet!")
            return name, None

        if self.token: # Reuse token if already set, take advantage of their loadbalancer
            stream_url = f"https://load.thetvapp.to{token_route.replace('token', 'hls')}/index.m3u8?token={self.token}"
            return name, f"{_base}?url={stream_url}&referer={TheTvApp.BASE}{route}"

        req = await self.session.post(f"{TheTvApp.BASE}{token_route}", json={self.keys[1]: self.keys[0]}, headers={
            "Origin": TheTvApp.BASE,
            "Referer": f"{TheTvApp.BASE}{route}",
            "X-CSRF-TOKEN": self.csrf,
        })

        if not req.is_success:
            self.logger.warning(f"Failed to POST '{req.url}', keys possibly updated...")
            return name, None

        self.logger.debug(req.text)
        m3u8_url = req.json()
        self.logger.debug(f"'{name}' recieved '{m3u8_url}' as response...")
        self.token = m3u8_url.partition("?token=")[2]
        final_url = f"{_base}?url={m3u8_url}&referer={TheTvApp.BASE}{route}"
        return name, final_url
    
    async def scrape_channel(self, name: str, route: Optional[str] = None, _base: str = "/cors") -> tuple:
        self.logger.info(f"Retrieving '{name}' channel data...")
        channel = TheTvApp.CHANNELS.get(name)
        if channel:
            self.logger.debug(f"'{name}' channel info: {channel}")
            if route:
                return name, self.get_stream(name, route, channel["token_route"], _base)
            else:
                return name, self.get_stream(name, channel["route"], channel["token_route"], _base)
        if not route:
            self.logger.warning(f"'{name}' has not been saved and no route has been passed...")
            return name, None
        response = await self.fetch_app_url(TheTvApp.BASE + route)
        if not response:
            self.logger.warning(f"'{name}' failed to get response...")
            return name, None
        token_route_match = re.search(r"\"get-m3u8-link\"\sdata=\"([^\"]+)\"", response)
        if not token_route_match:
            self.logger.warning(f"No token route for '{name}'!")
            return name, None
        token_route = token_route_match.group(1)
        return name, self.get_stream(name, route, token_route, _base)