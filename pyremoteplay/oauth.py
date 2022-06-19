"""OAuth methods for getting PSN credentials."""
from __future__ import annotations
import base64
import logging
from urllib.parse import parse_qs, urlparse
from typing import TYPE_CHECKING

import requests
import aiohttp
from Cryptodome.Hash import SHA256

if TYPE_CHECKING:
    from .profile import UserProfile

__CLIENT_ID = "ba495a24-818c-472b-b12d-ff231c1b5745"
__CLIENT_SECRET = "bXZhaVprUnNBc0kxSUJrWQ=="

__REDIRECT_URL = "https://remoteplay.dl.playstation.net/remoteplay/redirect"
__LOGIN_URL = (
    "https://auth.api.sonyentertainmentnetwork.com/"
    "2.0/oauth/authorize"
    "?service_entity=urn:service-entity:psn"
    f"&response_type=code&client_id={__CLIENT_ID}"
    f"&redirect_uri={__REDIRECT_URL}"
    "&scope=psn:clientapp"
    "&request_locale=en_US&ui=pr"
    "&service_logo=ps"
    "&layout_type=popup"
    "&smcid=remoteplay"
    "&prompt=always"
    "&PlatformPrivacyWs1=minimal"
    "&no_captcha=true&"
)

__TOKEN_URL = "https://auth.api.sonyentertainmentnetwork.com/2.0/oauth/token"
__TOKEN_BODY = (
    "grant_type=authorization_code" "&code={}" f"&redirect_uri={__REDIRECT_URL}&"
)
__HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_LOGGER = logging.getLogger(__name__)


def get_login_url() -> str:
    """Return Login Url."""
    return __LOGIN_URL


def get_user_account(redirect_url: str) -> dict:
    """Return user account.

    Account should be formatted with \
        :meth:`format_user_account() <pyremoteplay.profile.format_user_account>` before use.

    :param redirect_url: Redirect url found after logging in
    """
    code = _parse_redirect_url(redirect_url)
    if code is None:
        return None
    token = _get_token(code)
    if token is None:
        return None
    account = _fetch_account_info(token)
    return account


async def async_get_user_account(redirect_url: str) -> dict:
    """Return user account. Async.

    Account should be formatted with \
        :meth:`format_user_account() <pyremoteplay.profile.format_user_account>` before use.

    :param redirect_url: Redirect url found after logging in
    """
    code = _parse_redirect_url(redirect_url)
    if code is None:
        return None
    token = await _async_get_token(code)
    if token is None:
        return None
    account = await _async_fetch_account_info(token)
    return account


def _get_token(code: str) -> str:
    _LOGGER.debug("Sending POST request")
    body = __TOKEN_BODY.format(code).encode("ascii")
    resp = requests.post(
        __TOKEN_URL,
        headers=__HEADERS,
        data=body,
        auth=(__CLIENT_ID, base64.b64decode(__CLIENT_SECRET.encode()).decode()),
        timeout=3,
    )
    if resp.status_code == 200:
        content = resp.json()
        token = content.get("access_token")
        return token
    _LOGGER.error("Error getting token. Got response: %s", resp.status_code)
    return None


async def _async_get_token(code: str) -> str:
    _LOGGER.debug("Sending POST request")
    auth = aiohttp.BasicAuth(
        __CLIENT_ID, password=base64.b64decode(__CLIENT_SECRET.encode()).decode()
    )
    body = __TOKEN_BODY.format(code).encode("ascii")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url=__TOKEN_URL, auth=auth, headers=__HEADERS, data=body, timeout=3
        ) as resp:
            if resp.status == 200:
                content = await resp.json()
                token = content.get("access_token")
                return token
            _LOGGER.error("Error getting token. Got response: %s", resp.status)
            await resp.release()
            return None


def _fetch_account_info(token: str) -> dict:
    resp = requests.get(
        f"{__TOKEN_URL}/{token}",
        headers=__HEADERS,
        auth=(__CLIENT_ID, base64.b64decode(__CLIENT_SECRET.encode()).decode()),
        timeout=3,
    )
    if resp.status_code == 200:
        account_info = resp.json()
        account_info = _format_account_info(account_info)
        return account_info
    _LOGGER.error("Error getting account. Got response: %s", resp.status_code)
    return None


async def _async_fetch_account_info(token: str) -> dict:
    auth = aiohttp.BasicAuth(
        __CLIENT_ID, password=base64.b64decode(__CLIENT_SECRET.encode()).decode()
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url=f"{__TOKEN_URL}/{token}", auth=auth, timeout=3
        ) as resp:
            if resp.status == 200:
                account_info = await resp.json()
                account_info = _format_account_info(account_info)
                return account_info
            _LOGGER.error("Error getting account. Got response: %s", resp.status)
            await resp.release()
            return None


def _format_account_info(account_info: dict) -> dict:
    user_id = account_info["user_id"]
    user_b64 = _format_user_id(user_id, "base64")
    user_creds = _format_user_id(user_id, "sha256")
    account_info["user_rpid"] = user_b64
    account_info["credentials"] = user_creds
    return account_info


def _parse_redirect_url(redirect_url):
    if not redirect_url.startswith(__REDIRECT_URL):
        _LOGGER.error("Redirect URL does not start with %s", __REDIRECT_URL)
        return None
    code_url = urlparse(redirect_url)
    query = parse_qs(code_url.query)
    code = query.get("code")
    if code is None:
        _LOGGER.error("Code not in query")
        return None
    code = code[0]
    if len(code) <= 1:
        _LOGGER.error("Code is too short")
        return None
    _LOGGER.debug("Got Auth Code: %s", code)
    return code


def _format_user_id(user_id: str, encoding="base64"):
    """Format user id into useable encoding."""
    valid_encodings = {"base64", "sha256"}
    if encoding not in valid_encodings:
        raise TypeError(f"{encoding} encoding is not valid. Use {valid_encodings}")

    if user_id is not None:
        if encoding == "sha256":
            user_id = SHA256.new(user_id.encode())
            user_id = user_id.digest().hex()
        elif encoding == "base64":
            user_id = base64.b64encode(int(user_id).to_bytes(8, "little")).decode()
    return user_id


def prompt() -> dict:
    """Prompt for input and return account info."""
    msg = (
        "\r\n\r\nGo to the url below in a web browser, "
        "log into your PSN Account, "
        "then copy and paste the URL of the page that shows 'redirect'."
        f"\r\n\r\n{__LOGIN_URL} \r\n\r\nEnter Redirect URL >"
    )

    redirect_url = input(msg)
    if redirect_url is not None:
        account_info = get_user_account(redirect_url)
        return account_info
    return None
