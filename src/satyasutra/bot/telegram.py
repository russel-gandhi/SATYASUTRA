"""Telegram front door: a forwarded text, voice note or photo goes through the same API the web page uses, and the reply is plain text.

The bot holds no logic of its own. It long-polls Telegram, sends what it received to the local API (/verify, /voice/transcribe,
/media/check, /act/plan) and formats the answer. It never answers a message with a verdict it did not get from the API, replies only to
the chat that wrote, and never files or forwards anything anywhere. Errors are reported by class so a token in a URL cannot leak.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from ..config import Config

log = logging.getLogger("satyasutra.bot")


def format_claims(body: dict, labels: dict[str, str], none_found: str) -> str:
    claims = body.get("claims") or []
    if not claims:
        return none_found
    parts = []
    for c in claims:
        lines = [f"{c['text']}", f"{labels.get(c['label'], c['label'])} ({c['confidence_band']} confidence)"]
        lines += [f"- {r}" for r in dict.fromkeys(c.get("reasons", []))][:3]
        seen = list(dict.fromkeys(f"{x.get('publisher') or 'source'}: {x['url']}" for x in c.get("citations", []) if x.get("url")))
        lines += seen[:2]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def format_media(body: dict, labels: dict[str, str]) -> str:
    lines = [labels.get(body["label"], body["label"])] + [f"- {r}" for r in body.get("reasons", [])][:4]
    lines += [f"Seen in a fact-check: {m['url']}" for m in body.get("matches", []) if m.get("url")][:2]
    return "\n".join(lines)


class TelegramBot:
    def __init__(self, cfg: Config, token: str | None = None):
        self.s = cfg.runtime.telegram
        self.cfg = cfg
        self.token = token or os.environ.get(self.s.token_env, "")
        if not self.token:
            raise ValueError(f"set {self.s.token_env} in .env")
        self.api = httpx.Client(base_url=self.s.api_base, timeout=self.s.poll_timeout_s + self.s.request_margin_s)
        self.local = httpx.Client(base_url=self.s.local_api, timeout=self.s.local_timeout_s)

    def _call(self, method: str, **params) -> dict:
        r = self.api.post(f"/bot{self.token}/{method}", json=params)
        return r.json()

    def _download(self, file_id: str) -> bytes:
        info = self._call("getFile", file_id=file_id)["result"]
        r = self.api.get(f"/file/bot{self.token}/{info['file_path']}")
        r.raise_for_status()
        return r.content

    def reply(self, chat: int, text: str) -> None:
        self._call("sendMessage", chat_id=chat, text=text[: self.s.max_reply_chars], disable_web_page_preview=True)

    def check_text(self, text: str) -> str:
        body = self.local.post("/verify", json={"text": text}).json()
        out = format_claims(body, self.s.labels, self.s.messages["none_found"])
        labels = [c["label"] for c in body.get("claims", [])]
        plan = self.local.post("/act/plan", json={"text": text, "labels": labels,
                                                  "ledger_seqs": [c["ledger_seq"] for c in body.get("claims", []) if c.get("ledger_seq")]}).json()
        if plan.get("intent") != "none":
            out += "\n\n" + plan["why"].capitalize() + ". Next steps:\n" + "\n".join(f"- {r['title']}" + (f" ({r['contact']})" if r.get("contact") else "") for r in plan["routes"])
            out += "\n" + plan["notice"]
        return out

    def handle(self, msg: dict) -> str:
        if msg.get("text") and not msg["text"].startswith("/"):
            return self.check_text(msg["text"])
        if msg.get("text"):
            return self.s.messages["start"]
        voice = msg.get("voice") or msg.get("audio")
        if voice:
            r = self.local.post("/voice/transcribe", content=self._download(voice["file_id"]))
            if r.status_code != httpx.codes.OK:
                return self.s.messages["voice_failed"].format(reason=r.json().get("detail", ""))
            t = r.json()
            head = self.s.messages["transcript"].format(language=t["language"], text=t["text"])
            if t["language_uncertain"]:
                head += "\n" + self.s.messages["uncertain"]
            return head + "\n\n" + (self.check_text(t["text"]) if t["text"].strip() else self.s.messages["none_found"])
        photo = msg.get("photo")
        if photo:
            data = self._download(photo[-1]["file_id"])                    # Telegram lists sizes small to large
            r = self.local.post("/media/check", content=data)
            if r.status_code != httpx.codes.OK:
                return self.s.messages["image_failed"]
            return format_media(r.json(), self.s.labels) + ("\n\n" + self.check_text(msg["caption"]) if msg.get("caption") else "")
        return self.s.messages["unsupported"]

    def run(self) -> None:
        offset = None
        log.info("bot started")
        while True:
            try:
                updates = self._call("getUpdates", offset=offset, timeout=self.s.poll_timeout_s, allowed_updates=["message"])
            except httpx.HTTPError as exc:
                log.warning("poll failed: %s", type(exc).__name__)
                time.sleep(self.s.retry_s)
                continue
            for u in updates.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message")
                if not msg:
                    continue
                try:
                    text = self.handle(msg)
                except Exception as exc:                                 # one bad message must not stop the bot; class only, never the text
                    log.warning("handling failed: %s", type(exc).__name__)
                    text = self.s.messages["error"]
                self.reply(msg["chat"]["id"], text)
