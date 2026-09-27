# -*- coding: utf-8 -*-
"""Lưu danh sách vận đơn bằng file JSON (ghi kiểu atomic để không bao giờ hỏng file)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time


class Store:
    """Cấu trúc dữ liệu:
    {
      "chats": {
        "<chat_id>": {
          "orders": {
            "<MÃ VẬN ĐƠN>": {
              "note": "", "carrier": "SPX|GHN",
              "last_desc": "", "last_ts": null,
              "done": false, "added_at": 123456789
            }
          }
        }
      }
    }
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.data = {"chats": {}}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                # file hỏng -> giữ bản backup rồi làm mới
                try:
                    os.replace(self.path, self.path + ".bak")
                except OSError:
                    pass
                self.data = {"chats": {}}
        if not isinstance(self.data, dict):
            self.data = {"chats": {}}
        self.data.setdefault("chats", {})

    def save(self):
        with self._lock:
            d = os.path.dirname(os.path.abspath(self.path)) or "."
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".data_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

    # ---------------- helpers ----------------
    def orders(self, chat_id) -> dict:
        return self.data["chats"].setdefault(str(chat_id), {}).setdefault("orders", {})

    def all_chats(self) -> list:
        return list(self.data["chats"].keys())

    def add(self, chat_id, code: str, note: str, carrier: str):
        self.orders(chat_id)[code] = {
            "note": (note or "").strip(),
            "carrier": carrier,
            "last_desc": "",
            "last_ts": None,
            "done": False,
            "added_at": int(time.time()),
        }

    def get(self, chat_id, code: str):
        return self.orders(chat_id).get(code)

    def remove(self, chat_id, code: str) -> bool:
        return self.orders(chat_id).pop(code, None) is not None
