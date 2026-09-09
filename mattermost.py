"""Thin Mattermost REST v4 client for Family HQ.

Posts as a bot account when a token and channel are configured, otherwise through an
incoming webhook. Reading the channel (step 2) needs the bot token.
"""
from __future__ import annotations

import os

import requests


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
