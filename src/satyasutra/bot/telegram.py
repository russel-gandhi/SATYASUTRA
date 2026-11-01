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

from datetime import date

from ..act.plan import load_actions
from ..config import Config
from ..raksha import load_flow
from ..raksha.flow import Session, advance, begin

log = logging.getLogger("satyasutra.bot")
NO_CLAIM_QUOTE_CHARS = 120      # how much of the user's own words is echoed back when nothing was checkable


def no_claim_reply(extraction: dict, messages: dict[str, str]) -> str:
    """Why nothing was checked, in the user's terms: how the message was read, or that it could not be read at all. Never a verdict."""
    if extraction.get("status") == "invalid_output":
        return messages["could_not_read"]
    items = extraction.get("non_claim_items") or []
    if items:
        first = items[0]
        kind = messages.get(f"kind_{first.get('kind', '')}", messages["kind_other"])
        return messages["none_found_read"].format(kind=kind, quote=" ".join(str(first.get("quote", "")).split())[:NO_CLAIM_QUOTE_CHARS])
    return messages["none_found"]


def format_claims(body: dict, labels: dict[str, str], messages: dict[str, str]) -> str:
    """One short paragraph per claim: the verdict in a sentence, who said so and a link. The jargon (bands, rule trail) stays on the web page."""
    claims = body.get("claims") or []
    if not claims:
        return no_claim_reply(body.get("extraction") or {}, messages)
    parts = []
    for c in claims:
        cites = [x for x in c.get("citations", []) if x.get("url")]
        first = cites[0] if cites else {}
        lines = [c["text"]]
        if "HUMAN_REVIEW" in c.get("flags", []):
            lines.append(messages["human_review"])
        elif "CONFLICT" in c.get("flags", []):
            lines.append(messages["conflict"])
        else:
            lines.append(labels.get(c["label"], c["label"]).format(publisher=first.get("publisher") or "A fact-checker", rating=first.get("rating") or ""))
        if first:
            lines.append(messages["source_line"].format(url=first["url"]))
            more = len({x["url"] for x in cites}) - 1
            if more:
                lines.append(messages["more_line"].format(n=more))
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def format_media(body: dict, labels: dict[str, str], messages: dict[str, str]) -> str:
    lines = [labels.get(body["label"], body["label"])]
    lines += [messages["media_match"].format(url=m["url"]) for m in body.get("matches", []) if m.get("url")][:1]
    return "\n".join(lines)


def format_news(news: dict, messages: dict[str, str]) -> str:
    if news["status"] == "reported":
        h = news["hits"][0]
        return messages["news_reported"].format(publisher=h["publisher"], title=h["title"], url=h["url"])
    if news["status"] == "not_found":
        return messages["news_not_found"].format(n=news["outlets_checked"], names=", ".join(news["outlet_names"]))
    return ""


class TelegramBot:
    def __init__(self, cfg: Config, token: str | None = None):
        self.s = cfg.runtime.telegram
        self.cfg = cfg
        self.token = token or os.environ.get(self.s.token_env, "")
        if not self.token:
            raise ValueError(f"set {self.s.token_env} in .env")
        self.api = httpx.Client(base_url=self.s.api_base, timeout=self.s.poll_timeout_s + self.s.request_margin_s)
        self.local = httpx.Client(base_url=self.s.local_api, timeout=self.s.local_timeout_s, headers={self.s.private_header: "1"})
        self.help_until: dict[int, float] = {}                     # chat id -> deadline; memory only, gone when the bot stops
        self.flow, self.actions = load_flow(), load_actions()
        self.raksha: dict[int, Session] = {}                       # the guided flow's state per chat; memory only

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

    def in_help(self, chat: int | None) -> bool:
        until = self.help_until.get(chat, 0.0) if chat is not None else 0.0
        if until and until < time.monotonic():
            self.help_until.pop(chat, None)
        return bool(until) and until >= time.monotonic()

    def enter_help(self, chat: int | None) -> None:
        if chat is not None:
            self.help_until[chat] = time.monotonic() + self.s.help_mode_s

    def help_reply(self, plan: dict) -> str:
        """Guidance only: the routes the plan lists, then a request not to send images. Nothing is checked, stored or filed."""
        lines = [f"- {r['title']}" + (f" ({r['contact']})" if r.get("contact") else "") for r in plan.get("routes", [])]
        return "\n\n".join([self.s.messages["help_intro"], "\n".join(lines), self.s.messages["act_notice"], self.s.messages["help_no_images"]])

    def private_plan(self, text: str) -> dict | None:
        """The private-images topic is found before anything else runs: such a message is never fact-checked (a verdict would go into a ledger that cannot be edited)."""
        try:
            plan = self.local.post("/act/plan", json={"text": text, "labels": [], "ledger_seqs": []}).json()
        except (httpx.HTTPError, ValueError):
            return None
        return plan if plan.get("intent") == "intimate_images" else None

    def check_text(self, text: str, chat: int | None = None) -> str:
        plan0 = self.private_plan(text)
        if plan0:
            self.enter_help(chat)
            return self.help_reply(plan0) + "\n\n" + self.s.messages["guided_offer"]
        body = self.local.post("/verify", json={"text": text}).json()
        out = format_claims(body, self.s.labels, self.s.messages)
        for c in body.get("claims", []):
            if c["label"] == "UNVERIFIABLE_YET":                       # no fact-checker has covered it: say whether the news has
                try:
                    line = format_news(self.local.get("/news/check", params={"text": c["text"]}).json(), self.s.messages)
                except (httpx.HTTPError, ValueError, KeyError):
                    line = ""
                out += ("\n\n" + line) if line else ""
        labels = [c["label"] for c in body.get("claims", [])]
        plan = self.local.post("/act/plan", json={"text": text, "labels": labels,
                                                  "ledger_seqs": [c["ledger_seq"] for c in body.get("claims", []) if c.get("ledger_seq")]}).json()
        if plan.get("intent") != "none":
            out += "\n\n" + self.s.messages["next_steps"] + "\n" + "\n".join(f"- {r['title']}" + (f" ({r['contact']})" if r.get("contact") else "") for r in plan["routes"])
            out += "\n" + self.s.messages["act_notice"]
        return out

    def guide(self, text: str) -> str:
        """The grounded guide through the local API: /ladder, /ask, /draft. A question goes only to /raksha/ask, never to the fact-check path (no ledger row)."""
        cmd, _, rest = text.partition(" ")
        cmd, rest = cmd.split("@")[0], rest.strip()
        try:
            if cmd == "/ladder":
                v = self.local.get("/raksha/ladder", params={"age": rest or "unsure", "lang": "en"}).json()
                lines = [self.s.messages["ladder_intro"]]
                for i, r in enumerate(v["rungs"], start=1):
                    lines.append(f"{i}. {r['title']}\n{r['how']}\n{r['wait']}")
                    lines += [f"   “{q['quote']}” ({q['url']})" + ("" if q["confirmed"] else " [source not confirmed]") for q in r["sources"] if q.get("url")]
                return "\n\n".join([*lines, v["not_covered"]])
            if cmd == "/ask":
                if not rest:
                    return self.s.messages["ask_usage"]
                a = self.local.post("/raksha/ask", json={"text": rest, "lang": "en"}).json()
                if a.get("danger"):
                    return a["message"]
                if a.get("refused"):
                    return a["refusal"]
                parts = [f"{s['text']}\n“{s['quote']}”\n{s['source']['publisher']}, fetched {s['source']['retrieved_on']}: {s['source']['url']}" for s in a["sentences"]]
                return "\n\n".join([*parts, a["note"]])
            kind, _, tail = rest.partition(" ")
            if kind not in ("complaint", "followup", "escalation"):
                return self.s.messages["draft_usage"]
            fields = {k.strip(): v.strip() for k, _, v in (p.partition("=") for p in tail.split(";")) if k.strip() and v.strip()}
            r = self.local.post("/raksha/draft", json={"kind": kind, "lang": "en", "age": "unsure", "what": "shared", "fields": fields})
            if r.status_code == httpx.codes.OK:
                return r.json()["text"]
            return f"{r.json().get('detail', '')}\n\n{self.s.messages['draft_usage']}"
        except (httpx.HTTPError, ValueError, KeyError):
            return self.s.messages["raksha_busy"]

    def handle(self, msg: dict) -> str:
        chat = (msg.get("chat") or {}).get("id")
        text = msg.get("text") or ""
        if text.startswith("/forget"):
            self.help_until.pop(chat, None)
            self.raksha.pop(chat, None)
            return self.s.messages["forgot"]
        if text.startswith(("/ladder", "/ask", "/draft")):
            return self.guide(text)
        if text.startswith(("/help_me", "/madad")):
            self.enter_help(chat)
            self.raksha[chat], first = begin(self.flow, "hi" if text.startswith("/madad") else "en")
            return first
        session = self.raksha.get(chat)
        if session and session.expires < time.monotonic():
            self.raksha.pop(chat, None)
            session = None
        if session and text and not text.startswith("/"):               # inside the flow nothing is fact-checked or sent to the local API
            reply = advance(self.flow, session, text, self.actions, date.today())
            if session.done:
                self.raksha.pop(chat, None)
            return reply
        if text and not text.startswith("/"):
            return self.check_text(text, chat)
        if text:
            return self.s.messages["start"]
        if self.in_help(chat) and any(k in msg for k in ("photo", "video", "video_note", "document", "animation", "sticker")):
            return self.s.messages["help_images_refused"]              # refused before any download: the file is never fetched
        caption = msg.get("caption")
        if caption and (plan0 := self.private_plan(caption)):
            self.enter_help(chat)
            return self.help_reply(plan0)
        voice = msg.get("voice") or msg.get("audio")
        if voice:
            r = self.local.post("/voice/transcribe", content=self._download(voice["file_id"]))
            if r.status_code != httpx.codes.OK:
                return self.s.messages["voice_failed"].format(reason=r.json().get("detail", ""))
            t = r.json()
            head = self.s.messages["transcript"].format(language=t["language"], text=t["text"])
            if t["language_uncertain"]:
                head += "\n" + self.s.messages["uncertain"]
            return head + "\n\n" + (self.check_text(t["text"], chat) if t["text"].strip() else self.s.messages["none_found"])
        photo = msg.get("photo")
        if photo:
            data = self._download(photo[-1]["file_id"])                    # Telegram lists sizes small to large
            r = self.local.post("/media/check", content=data)
            if r.status_code != httpx.codes.OK:
                return self.s.messages["image_failed"]
            return format_media(r.json(), self.s.labels, self.s.messages) + ("\n\n" + self.check_text(msg["caption"], chat) if msg.get("caption") else "")
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
