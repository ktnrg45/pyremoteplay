"""OAuth methods for getting PSN credentials."""
import asyncio
import base64
import logging
from urllib.parse import parse_qs, urlparse

import aiohttp
from Cryptodome.Hash import SHA256

CLIENT_ID = "ba495a24-818c-472b-b12d-ff231c1b5745"
CLIENT_SECRET = "mvaiZkRsAsI1IBkY"

REDIRECT_URL = "https://remoteplay.dl.playstation.net/remoteplay/redirect"
LOGIN_URL = (
    'https://auth.api.sonyentertainmentnetwork.com/'
    '2.0/oauth/authorize'
    '?service_entity=urn:service-entity:psn'
    f'&response_type=code&client_id={CLIENT_ID}'
    f'&redirect_uri={REDIRECT_URL}'
    '&scope=psn:clientapp'
    '&request_locale=en_US&ui=pr'
    '&service_logo=ps'
    '&layout_type=popup'
    '&smcid=remoteplay'
    '&prompt=always'
    '&PlatformPrivacyWs1=minimal'
    '&no_captcha=true&'
)

TOKEN_URL = "https://auth.api.sonyentertainmentnetwork.com/2.0/oauth/token"
TOKEN_BODY = (
    'grant_type=authorization_code'
    '&code={}'
    f'&redirect_uri={REDIRECT_URL}&'
)
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded"
}

logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger(__name__)


def get_user_account(redirect_url: str, loop=None):
    """Return Account. Use async_get_user_account if from loop."""
    if loop is None:
        loop = asyncio.get_event_loop()
        if not loop.is_running():
            task = asyncio.ensure_future(async_get_user_account(redirect_url))
            account = loop.run_until_complete(task)
    else:
        account = loop.create_task(async_get_user_account(redirect_url))
    return account


async def async_get_user_account(redirect_url: str) -> dict:
    """Asyncio coroutine to get user account."""
    code = _parse_redirect_url(redirect_url)
    if code is None:
        return None
    token = await _get_token(code)
    if token is None:
        return None
    account = await _fetch_account_info(token)
    return account


async def _get_token(code):
    _LOGGER.debug("Sending POST request")
    auth = aiohttp.BasicAuth(CLIENT_ID, password=CLIENT_SECRET)
    body = TOKEN_BODY.format(code).encode('ascii')
    async with aiohttp.ClientSession() as session:
        async with session.post(
                url=TOKEN_URL, auth=auth,
                headers=HEADERS, data=body, timeout=3) as resp:
            if resp.status == 200:
                content = await resp.json()
                token = content.get('access_token')
                return token
            _LOGGER.error(
                "Error getting token. Got response: %s", resp.status)
            await resp.release()
            return None


async def _fetch_account_info(token):
    auth = aiohttp.BasicAuth(CLIENT_ID, password=CLIENT_SECRET)
    async with aiohttp.ClientSession() as session:
        async with session.get(
                url='{}/{}'.format(TOKEN_URL, token),
                auth=auth, timeout=3) as resp:
            if resp.status == 200:
                account_info = await resp.json()
                user_id = account_info.get('user_id')
                user_b64 = _format_user_id(user_id, 'base64')
                user_creds = _format_user_id(user_id, 'sha256')
                account_info['user_rpid'] = user_b64
                account_info['credentials'] = user_creds
                return account_info
            _LOGGER.error(
                "Error getting account. Got response: %s", resp.status)
            await resp.release()
            return None


def _parse_redirect_url(redirect_url):
    if not redirect_url.startswith(REDIRECT_URL):
        _LOGGER.error("Redirect URL does not start with %s", REDIRECT_URL)
        return None
    code_url = urlparse(redirect_url)
    query = parse_qs(code_url.query)
    code = query.get('code')
    if code is None:
        _LOGGER.error("Code not in query")
        return None
    code = code[0]
    if len(code) <= 1:
        _LOGGER.error("Code is too short")
        return None
    _LOGGER.debug("Got Auth Code: %s", code)
    return code


def _format_user_id(user_id: str, encoding='base64'):
    """Format user id into useable encoding."""
    valid_encodings = {'base64', 'sha256'}
    if encoding not in valid_encodings:
        raise TypeError("{} encoding is not valid. Use {}".format(
            encoding, valid_encodings))

    if user_id is not None:
        if encoding == 'sha256':
            user_id = SHA256.new(user_id.encode())
            user_id = user_id.digest().hex()
        elif encoding == 'base64':
            user_id = base64.b64encode(
                int(user_id).to_bytes(8, "little")).decode()
    return user_id


def prompt():
    msg = (
        "\r\n\r\nGo to the url below in a web browser, "
        "log into your PSN Account, "
        "then copy and paste the URL of the page that shows 'redirect'."
        "\r\n\r\n{} \r\n\r\nEnter Redirect URL >".format(LOGIN_URL)
    )

    redirect_url = input(msg)
    if redirect_url is not None:
        account_info = get_user_account(redirect_url)
        return account_info
    return None
