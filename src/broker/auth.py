"""Upstox OAuth token storage and Playwright login automation."""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

UPSTOX_AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


@dataclass
class TokenRecord:
    access_token: str
    obtained_at: datetime
    expires_at: datetime | None = None


class TokenStore:
    """Persist access token locally (dev) or read from env / Secrets Manager ARN."""

    def __init__(self, token_file: str | Path | None = None) -> None:
        self.token_file = Path(token_file) if token_file else Path("./data/live/upstox_token.json")
        self.token_file.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> TokenRecord | None:
        env = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
        if env:
            return TokenRecord(access_token=env, obtained_at=datetime.now(timezone.utc))
        if not self.token_file.exists():
            return None
        raw = json.loads(self.token_file.read_text(encoding="utf-8"))
        token = str(raw.get("access_token", "")).strip()
        if not token:
            return None
        obtained = raw.get("obtained_at")
        obtained_at = (
            datetime.fromisoformat(obtained)
            if obtained
            else datetime.now(timezone.utc)
        )
        expires = raw.get("expires_at")
        expires_at = datetime.fromisoformat(expires) if expires else None
        return TokenRecord(access_token=token, obtained_at=obtained_at, expires_at=expires_at)

    def save(self, access_token: str, *, expires_at: datetime | None = None) -> None:
        payload = {
            "access_token": access_token,
            "obtained_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
        }
        self.token_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def is_valid_for_today(self, record: TokenRecord | None) -> bool:
        if record is None or not record.access_token:
            return False
        if record.expires_at and datetime.now(timezone.utc) >= record.expires_at:
            return False
        return True


def exchange_auth_code(
    *,
    api_key: str,
    api_secret: str,
    redirect_uri: str,
    auth_code: str,
) -> str:
    resp = requests.post(
        UPSTOX_TOKEN_URL,
        data={
            "code": auth_code,
            "client_id": api_key,
            "client_secret": api_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"Token exchange failed: {payload}")
    return str(token)


class UpstoxLoginAutomator:
    """Browser automation for discretionary daily login (REQUIREMENTS §9 step 2)."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        redirect_uri: str,
        token_store: TokenStore,
        browser_profile_dir: str | Path | None = None,
        headless: bool = False,
        timeout_sec: int = 300,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.redirect_uri = redirect_uri
        self.token_store = token_store
        self.browser_profile_dir = Path(browser_profile_dir or "./data/.upstox_browser")
        self.headless = headless
        self.timeout_sec = timeout_sec

    def authorization_url(self) -> str:
        params = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.api_key,
                "redirect_uri": self.redirect_uri,
            }
        )
        return f"{UPSTOX_AUTH_URL}?{params}"

    def ensure_access_token(self, *, force_login: bool = False) -> str:
        record = self.token_store.load()
        if not force_login and self.token_store.is_valid_for_today(record):
            assert record is not None
            return record.access_token
        return self.login_via_browser()

    def login_via_browser(self) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required for UI login. "
                "Install with: pip install playwright && playwright install chromium"
            ) from exc

        auth_url = self.authorization_url()
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Opening Upstox login: %s", auth_url)

        captured_code: str | None = None

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self.browser_profile_dir),
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()

            def on_response(response) -> None:  # type: ignore[no-untyped-def]
                nonlocal captured_code
                url = response.url
                if not url.startswith(self.redirect_uri):
                    return
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                code_vals = qs.get("code")
                if code_vals:
                    captured_code = code_vals[0]

            page.on("response", on_response)
            page.goto(auth_url, wait_until="domcontentloaded", timeout=self.timeout_sec * 1000)

            totp_secret = os.environ.get("UPSTOX_TOTP_SECRET", "").strip()
            if totp_secret:
                self._try_fill_totp(page, totp_secret)

            deadline = datetime.now(timezone.utc).timestamp() + self.timeout_sec
            while captured_code is None and datetime.now(timezone.utc).timestamp() < deadline:
                page.wait_for_timeout(500)

            context.close()

        if not captured_code:
            raise RuntimeError(
                "Login timed out — complete Upstox login in the browser window "
                f"within {self.timeout_sec}s"
            )

        token = exchange_auth_code(
            api_key=self.api_key,
            api_secret=self.api_secret,
            redirect_uri=self.redirect_uri,
            auth_code=captured_code,
        )
        self.token_store.save(token)
        logger.info("Upstox access token saved to %s", self.token_store.token_file)
        return token

    def _try_fill_totp(self, page, totp_secret: str) -> None:  # type: ignore[no-untyped-def]
        try:
            import pyotp
        except ImportError:
            logger.warning("pyotp not installed — TOTP auto-fill skipped")
            return
        otp = pyotp.TOTP(totp_secret).now()
        for selector in (
            'input[name="otp"]',
            'input[type="tel"]',
            'input[placeholder*="OTP"]',
            'input[placeholder*="otp"]',
        ):
            loc = page.locator(selector)
            if loc.count() > 0:
                loc.first.fill(otp)
                logger.info("Filled TOTP via selector %s", selector)
                return
