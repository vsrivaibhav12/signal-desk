"""Sends alerts to your phone and reads the few words you can send back."""
import requests


class Telegram:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.chat_id = None
        self.offset = None
        self.username = None

    def _call(self, method, **params):
        try:
            r = requests.post(self.base + method, json=params, timeout=params.get("timeout", 0) + 15)
            j = r.json()
            return j.get("result") if j.get("ok") else None
        except Exception as e:  # network trouble must never stop the engine
            print("Telegram problem:", type(e).__name__)
            return None

    def me(self):
        if self.username is None:
            res = self._call("getMe") or {}
            self.username = res.get("username")
        return self.username

    def send(self, text):
        print("[alert]", text.replace("\n", " | ")[:300])
        if self.chat_id:
            self._call("sendMessage", chat_id=self.chat_id, text=text, parse_mode="HTML",
                       disable_web_page_preview=True)

    def poll(self, wait=0):
        """Returns new messages as a list of (chat_id, text, sent_time)."""
        params = {"timeout": int(wait), "allowed_updates": ["message"]}
        if self.offset is not None:
            params["offset"] = self.offset
        out = []
        for u in self._call("getUpdates", **params) or []:
            self.offset = u["update_id"] + 1
            m = u.get("message") or {}
            if m.get("text"):
                out.append((m["chat"]["id"], m["text"].strip(), m.get("date", 0)))
        return out
