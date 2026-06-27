"""Upstox OAuth token storage and Playwright login automation."""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
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
        try:
            from zoneinfo import ZoneInfo

            ist = ZoneInfo("Asia/Kolkata")
        except ImportError:
            ist = timezone.utc
        obtained_ist = record.obtained_at.astimezone(ist).date()
        today_ist = datetime.now(ist).date()
        return obtained_ist >= today_ist

    def load_from_file(self) -> TokenRecord | None:
        """Load persisted token only (ignore UPSTOX_ACCESS_TOKEN env)."""
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
        record = self.token_store.load_from_file()
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
        callback_server, callback_holder = self._start_callback_server()
        chromium_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "").strip()
        launch_kwargs: dict = {
            "user_data_dir": str(self.browser_profile_dir),
            "headless": self.headless,
            "viewport": {"width": 1280, "height": 900},
        }
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
            launch_kwargs["args"] = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]

        try:
            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(**launch_kwargs)
                page = context.pages[0] if context.pages else context.new_page()

                def on_response(response) -> None:  # type: ignore[no-untyped-def]
                    nonlocal captured_code
                    captured_code = self._extract_auth_code_from_url(response.url) or captured_code

                page.on("response", on_response)

                def on_request(request) -> None:  # type: ignore[no-untyped-def]
                    nonlocal captured_code
                    captured_code = self._extract_auth_code_from_url(request.url) or captured_code

                page.on("request", on_request)

                def on_frame_navigated(frame) -> None:  # type: ignore[no-untyped-def]
                    nonlocal captured_code
                    if frame != page.main_frame:
                        return
                    captured_code = self._extract_auth_code_from_url(frame.url) or captured_code

                page.on("framenavigated", on_frame_navigated)
                page.goto(auth_url, wait_until="domcontentloaded", timeout=self.timeout_sec * 1000)

                totp_secret = os.environ.get("UPSTOX_TOTP_SECRET", "").strip()
                login_mobile = os.environ.get("UPSTOX_LOGIN_MOBILE", "").strip()
                login_pin = os.environ.get("UPSTOX_LOGIN_PIN", "").strip()
                sms_otp = os.environ.get("UPSTOX_SMS_OTP", "").strip()

                page.wait_for_timeout(1000)
                if login_pin:
                    self._try_fill_pin(page, login_pin)
                    self._try_click_continue(page)
                    page.wait_for_timeout(1500)

                if login_mobile:
                    self._try_fill_field(
                        page,
                        login_mobile,
                        selectors=(
                            'input[name="mobileNumber"]',
                            'input[maxlength="10"]',
                            'input[placeholder*="Mobile"]',
                        ),
                        label="mobile",
                    )
                    page.wait_for_timeout(500)
                    self._try_click_get_otp(page)
                    page.wait_for_timeout(1500)

                if totp_secret and not sms_otp:
                    self._try_click_totp_link(page)
                    page.wait_for_timeout(1000)

                deadline = datetime.now(timezone.utc).timestamp() + self.timeout_sec
                while captured_code is None and datetime.now(timezone.utc).timestamp() < deadline:
                    captured_code = callback_holder.get("code") or captured_code
                    captured_code = self._extract_auth_code_from_url(page.url) or captured_code
                    if captured_code:
                        break
                    if login_pin:
                        self._try_fill_pin(page, login_pin)
                    if sms_otp:
                        self._try_fill_otp_code(page, sms_otp)
                    elif totp_secret:
                        self._wait_and_fill_totp(page, totp_secret, timeout_sec=5)
                    self._try_click_verify(page)
                    self._try_click_continue(page)
                    page.wait_for_timeout(800)

                if not captured_code:
                    shot = self.browser_profile_dir / "login_timeout.png"
                    try:
                        page.screenshot(path=str(shot))
                        logger.error("Login timeout screenshot: %s", shot)
                    except Exception:  # noqa: BLE001
                        pass

                context.close()
        finally:
            callback_server.shutdown()

        if not captured_code:
            raise RuntimeError(
                "Login timed out — set UPSTOX_LOGIN_MOBILE + UPSTOX_LOGIN_PIN for first "
                f"login, or complete manually within {self.timeout_sec}s"
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

    def _start_callback_server(self) -> tuple[HTTPServer, dict[str, str | None]]:
        parsed = urllib.parse.urlparse(self.redirect_uri)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        callback_path = parsed.path or "/"
        holder: dict[str, str | None] = {"code": None}

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                req = urllib.parse.urlparse(self.path)
                if req.path == callback_path:
                    code_vals = urllib.parse.parse_qs(req.query).get("code")
                    if code_vals:
                        holder["code"] = code_vals[0]
                        logger.info("Callback server received auth code")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body>Login OK.</body></html>")

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

        server = HTTPServer((host, port), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info("OAuth callback server listening on %s:%s%s", host, port, callback_path)
        return server, holder

    def _extract_auth_code_from_url(self, url: str) -> str | None:
        if not url.startswith(self.redirect_uri):
            return None
        code_vals = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("code")
        if code_vals:
            logger.info("Captured auth code from redirect URL")
            return code_vals[0]
        return None

    def _try_fill_field(
        self,
        page,
        value: str,
        *,
        selectors: tuple[str, ...],
        label: str,
    ) -> None:  # type: ignore[no-untyped-def]
        import re

        if label == "mobile":
            for pattern in (r"enter mobile", r"mobile number", r"mobile"):
                loc = page.get_by_placeholder(re.compile(pattern, re.I))
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    loc.first.fill("")
                    loc.first.press_sequentially(value, delay=30)
                    logger.info("Filled mobile via placeholder %s", pattern)
                    return
            for sel in ('input[maxlength="10"]', 'input[name="mobileNumber"]', "#mobileNumber"):
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    loc.first.fill(value)
                    logger.info("Filled mobile via %s", sel)
                    return
        for selector in selectors:
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                current = loc.first.input_value()
                if current != value:
                    loc.first.fill(value)
                    logger.info("Filled %s via %s", label, selector)
                return

    def _try_click_get_otp(self, page) -> None:  # type: ignore[no-untyped-def]
        import re

        btn = page.get_by_role("button", name=re.compile(r"get otp", re.I))
        if btn.count() > 0 and btn.first.is_enabled():
            btn.first.click()
            logger.info("Clicked Get OTP")
            return
        otp_btn = page.locator("#getOtp")
        if otp_btn.count() > 0 and otp_btn.first.is_enabled():
            otp_btn.first.click()
            logger.info("Clicked #getOtp")

    def _try_click_totp_link(self, page) -> None:  # type: ignore[no-untyped-def]
        for sel in (
            'text="Try TOTP?"',
            'a:has-text("Try TOTP")',
            'button:has-text("Try TOTP")',
        ):
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                logger.info("Clicked Try TOTP link")
                return

    def _try_fill_pin(self, page, pin: str) -> None:  # type: ignore[no-untyped-def]
        import re

        if len(pin) != 6 or not pin.isdigit():
            return
        pin_heading = page.get_by_text(re.compile(r"6-digit pin|enter.*pin", re.I))
        if pin_heading.count() == 0:
            return

        boxes = page.locator('input[maxlength="1"]')
        if boxes.count() >= 6:
            for i, digit in enumerate(pin):
                box = boxes.nth(i)
                if box.is_visible():
                    box.click()
                    box.fill(digit)
            logger.info("Filled PIN via %s digit boxes", boxes.count())
            return

        for sel in (
            'input[type="password"]',
            'input[maxlength="6"]',
            'input[name="pin"]',
            "#pin",
        ):
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible() and loc.first.is_enabled():
                loc.first.click()
                loc.first.fill("")
                loc.first.press_sequentially(pin, delay=80)
                logger.info("Filled PIN via %s", sel)
                return

    def _try_fill_otp_code(self, page, code: str) -> None:  # type: ignore[no-untyped-def]
        for selector in ("#otpNum", 'input[autocomplete="one-time-code"]', 'input[name="otp"]'):
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible() and loc.first.is_enabled():
                loc.first.fill(code)
                logger.info("Filled OTP via %s", selector)
                return

    def _try_click_verify(self, page) -> None:  # type: ignore[no-untyped-def]
        for sel in ("#verifyOtp", "#continueBtn", "#continue"):
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible() and btn.first.is_enabled():
                try:
                    btn.first.click(timeout=3000)
                    logger.info("Clicked %s", sel)
                except Exception:  # noqa: BLE001
                    pass
                return

    def _try_click_continue(self, page) -> None:  # type: ignore[no-untyped-def]
        import re

        for pattern in (
            r"continue",
            r"verify",
            r"submit",
            r"log in",
            r"login",
        ):
            btn = page.get_by_role("button", name=re.compile(pattern, re.I))
            if btn.count() > 0 and btn.first.is_visible() and btn.first.is_enabled():
                btn.first.click()
                logger.info("Clicked button matching /%s/i", pattern)
                return
        otp_btn = page.locator("#getOtp")
        if otp_btn.count() > 0 and otp_btn.first.is_enabled():
            otp_btn.first.click()
            logger.info("Clicked #getOtp")
            return
        for btn_sel in (
            'button:has-text("Continue")',
            'button:has-text("Verify")',
            'button:has-text("Submit")',
            'button:has-text("Log in")',
            'button:has-text("Login")',
            'button[type="submit"]',
        ):
            btn = page.locator(btn_sel)
            if btn.count() > 0 and btn.first.is_visible() and btn.first.is_enabled():
                btn.first.click()
                return

    def _wait_and_fill_totp(self, page, totp_secret: str, *, timeout_sec: int = 90) -> None:  # type: ignore[no-untyped-def]
        try:
            import pyotp
        except ImportError:
            logger.warning("pyotp not installed — TOTP auto-fill skipped")
            return

        deadline = datetime.now(timezone.utc).timestamp() + timeout_sec
        selectors = (
            "#otpNum",
            'input[name="otp"]',
            'input[name="totp"]',
            'input[autocomplete="one-time-code"]',
            'input[placeholder*="OTP"]',
            'input[placeholder*="otp"]',
            'input[placeholder*="TOTP"]',
        )
        while datetime.now(timezone.utc).timestamp() < deadline:
            otp = pyotp.TOTP(totp_secret).now()
            for selector in selectors:
                loc = page.locator(selector)
                if loc.count() > 0 and loc.first.is_visible() and loc.first.is_enabled():
                    loc.first.click()
                    loc.first.fill("")
                    loc.first.press_sequentially(otp, delay=80)
                    logger.info("Filled TOTP via selector %s", selector)
                    for btn_sel in (
                        "#continueBtn",
                        "#verifyOtp",
                        'button:has-text("Continue")',
                        'button:has-text("Verify")',
                        'button:has-text("Submit")',
                        'button[type="submit"]',
                    ):
                        btn = page.locator(btn_sel)
                        if btn.count() > 0 and btn.first.is_enabled():
                            btn.first.click()
                            break
                    return
            page.wait_for_timeout(500)
