"""Thin Mattermost REST v4 client for Family HQ.

Posts as a bot account when a token and channel are configured, otherwise through an
incoming webhook. Reading the channel (step 2) needs the bot token.
"""
from __future__ import annotations

import os

import requests


MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_MIME_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}


class MattermostError(Exception):
    """Raised when Mattermost rejects a request or is unreachable."""


class MattermostClient:
    def __init__(self, base_url: str, token: str | None = None, channel_id: str | None = None,
                 webhook_url: str | None = None, timeout: int = 10):
        self.base_url = base_url.rstrip('/')
        self.token = token or None
        self.channel_id = channel_id or None
        self.webhook_url = webhook_url or None
        self.timeout = timeout
        self._me = None

    @property
    def can_read(self) -> bool:
        return bool(self.token and self.channel_id)

    @property
    def can_post(self) -> bool:
        return self.can_read or bool(self.webhook_url)

    def _headers(self) -> dict:
        return {'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'}

    def _request(self, method: str, url: str, **kwargs):
        try:
            response = requests.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise MattermostError(f'Mattermost unreachable: {exc}') from exc
        if response.status_code >= 400:
            raise MattermostError(f'Mattermost {response.status_code} for {method} {url}: {response.text[:200]}')
        return response

    def ping(self) -> bool:
        try:
            self._request('GET', f'{self.base_url}/api/v4/system/ping')
            return True
        except MattermostError:
            return False

    def post(self, message: str) -> str | None:
        """Post `message` to the channel. Returns the post id (bot) or None (webhook)."""
        if self.can_read:
            response = self._request(
                'POST', f'{self.base_url}/api/v4/posts', headers=self._headers(),
                json={'channel_id': self.channel_id, 'message': message},
            )
            return response.json().get('id')
        if self.webhook_url:
            self._request('POST', self.webhook_url, json={'text': message})
            return None
        raise MattermostError('No Mattermost bot token or webhook configured')


    # ── reading (bot token only) ──────────────────────────────────────────
    def _require_bot(self):
        if not self.can_read:
            raise MattermostError('This call needs a bot token and channel id')

    def me(self) -> dict:
        if self._me is None:
            self._require_bot()
            self._me = self._request('GET', f'{self.base_url}/api/v4/users/me', headers=self._headers()).json()
        return self._me

    def posts_since(self, since_ms: int) -> list[dict]:
        """Posts in the channel created or edited after `since_ms`, oldest first."""
        self._require_bot()
        data = self._request('GET', f'{self.base_url}/api/v4/channels/{self.channel_id}/posts',
                             headers=self._headers(), params={'since': int(since_ms)}).json()
        posts = [data['posts'][pid] for pid in data.get('order', []) if pid in data.get('posts', {})]
        keep = ('id', 'user_id', 'message', 'create_at', 'file_ids', 'root_id', 'type')
        return sorted(({k: p.get(k) for k in keep} for p in posts), key=lambda p: p['create_at'] or 0)

    def download_file(self, file_id: str) -> tuple[bytes, str, str]:
        """Download an image attachment. Returns (content, mime_type, name)."""
        self._require_bot()
        info = self._request('GET', f'{self.base_url}/api/v4/files/{file_id}/info', headers=self._headers()).json()
        mime = (info.get('mime_type') or '').lower()
        if mime not in IMAGE_MIME_TYPES:
            raise MattermostError(f'Not an image: {mime or "unknown type"}')
        if int(info.get('size') or 0) > MAX_IMAGE_BYTES:
            raise MattermostError('Image larger than 10 MB')
        content = self._request('GET', f'{self.base_url}/api/v4/files/{file_id}', headers=self._headers()).content
        return content, mime, info.get('name') or file_id

    def add_reaction(self, post_id: str, emoji_name: str = 'white_check_mark') -> None:
        self._require_bot()
        self._request('POST', f'{self.base_url}/api/v4/reactions', headers=self._headers(),
                      json={'user_id': self.me()['id'], 'post_id': post_id, 'emoji_name': emoji_name})

    def users_by_ids(self, ids) -> dict[str, str]:
        self._require_bot()
        ids = list(ids)
        if not ids:
            return {}
        rows = self._request('POST', f'{self.base_url}/api/v4/users/ids', headers=self._headers(), json=ids).json()
        return {u['id']: u['username'] for u in rows}


def client_from_env(environ=os.environ) -> MattermostClient | None:
    """Build a client from MATTERMOST_* variables, or None when sending is not configured."""
    url = (environ.get('MATTERMOST_URL') or '').strip()
    token = (environ.get('MATTERMOST_BOT_TOKEN') or '').strip()
    channel_id = (environ.get('MATTERMOST_CHANNEL_ID') or '').strip()
    webhook = (environ.get('MATTERMOST_WEBHOOK_URL') or '').strip()
    if not url:
        return None
    client = MattermostClient(url, token=token, channel_id=channel_id, webhook_url=webhook)
    return client if client.can_post else None
