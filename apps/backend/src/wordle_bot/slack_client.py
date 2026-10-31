"""Slack API adapter with explicit, worker-controlled retries."""

from typing import Protocol

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


class SlackGateway(Protocol):
    def react(self, channel: str, timestamp: str, reaction: str) -> None: ...
    def post(self, channel: str, text: str) -> None: ...


class SlackClient:
    def __init__(self, token: str) -> None:
        self.client = WebClient(token=token, timeout=10, retry_handlers=[])

    def react(self, channel: str, timestamp: str, reaction: str) -> None:
        try:
            self.client.reactions_add(channel=channel, timestamp=timestamp, name=reaction)
        except SlackApiError as error:
            if error.response.get("error") != "already_reacted":
                raise

    def post(self, channel: str, text: str) -> None:
        self.client.chat_postMessage(
            channel=channel,
            text=text,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
            unfurl_links=False,
            unfurl_media=False,
        )
