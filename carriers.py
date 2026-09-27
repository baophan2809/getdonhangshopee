# -*- coding: utf-8 -*-
"""
Gọi API tra cứu CÔNG KHAI của SPX (Shopee Express VN) và GHN (Giao Hàng Nhanh).

- SPX : GET https://spx.vn/shipment/order/open/order/get_order_info?spx_tn=MÃ&language_code=vi
        (chính là API mà trang spx.vn dùng). SPX chặn (HTTP 403) các thư viện HTTP
        thông thường gọi từ máy chủ, nên phần SPX dùng curl_cffi giả lập Chrome thật.
- GHN : GET https://fe-online-gateway.ghn.vn/order-tracking/public-api/client/tracking-logs?order_code=MÃ
        (chính là API mà trang donhang.ghn.vn dùng — đã test OK bằng đơn thật, có dự phòng POST)

Nếu một ngày hãng đổi API thì chỉ cần sửa file này.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import httpx

try:  # giả lập "vân tay" TLS của Chrome — cần để SPX không trả 403
    from curl_cffi.requests import AsyncSession as CffiSession
except ImportError:  # chưa cài -> tạm dùng httpx (dễ bị SPX chặn)
    CffiSession = None

log = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))

# Giờ GHN trả về là UTC (đuôi Z) — đã kiểm chứng bằng đơn thật.
# Nếu sau này thấy giờ của đơn GHN bị lệch đúng 7 tiếng thì đổi thành False.
GHN_TIME_IS_UTC = True

SPX_API = "https://spx.vn/shipment/order/open/order/get_order_info"
GHN_API = "https://fe-online-gateway.ghn.vn/order-tracking/public-api/client/tracking-logs"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9",
}

# Dịch mã trạng thái GHN -> tiếng Việt (dự phòng khi API không trả status_name)
GHN_STATUS_VI = {
    "ready_to_pick": "Đơn hàng vừa được tạo, chờ lấy hàng",
    "picking": "Shipper đang đến lấy hàng",
    "money_collect_picking": "Shipper đang làm việc với người gửi",
    "picked": "Lấy hàng thành công",
    "storing": "Hàng đã nhập kho",
    "transporting": "Đang trung chuyển",
    "sorting": "Đang phân loại tại kho",
    "delivering": "Shipper đang giao hàng",
    "money_collect_delivering": "Shipper đang làm việc với người nhận",
    "delivered": "Giao hàng thành công",
    "delivery_fail": "Giao hàng không thành công",
    "waiting_to_return": "Chờ xác nhận giao lại",
    "return": "Chuyển hoàn",
    "return_transporting": "Đang trung chuyển hàng hoàn",
    "return_sorting": "Đang phân loại hàng hoàn",
    "returning": "Shipper đang hoàn hàng",
    "return_fail": "Hoàn hàng không thành công",
    "returned": "Hoàn hàng thành công",
    "cancel": "Đơn đã huỷ",
    "exception": "Đơn hàng ngoại lệ",
    "damage": "Hàng hư hỏng",
    "lost": "Hàng thất lạc",
}

# Trạng thái GHN coi như "xong" -> khỏi cần check ngầm nữa
GHN_DONE_STATUSES = {"delivered", "returned", "cancel", "lost", "damage"}

# Từ khoá trong mô tả SPX coi như "xong"
SPX_DONE_KEYWORDS = (
    "giao hàng thành công", "giao kiện hàng thành công",
    "đã hủy", "đã huỷ", "hủy đơn", "huỷ đơn",
    "trả hàng thành công", "hoàn hàng thành công", "trả kiện hàng thành công",
)


@dataclass
class TrackingEvent:
    """Một dòng trạng thái trong hành trình đơn hàng."""
    description: str
    ts: int | None  # epoch giây (UTC), None nếu không rõ giờ

    def time_str(self) -> str:
        if not self.ts:
            return "—"
        return datetime.fromtimestamp(self.ts, VN_TZ).strftime("%H:%M %d/%m/%Y")


@dataclass
class TrackingInfo:
    code: str
    carrier: str                                   # "SPX" | "GHN"
    events: list = field(default_factory=list)     # TrackingEvent, MỚI NHẤT đứng đầu
    done: bool = False                             # đã giao / hoàn / huỷ

    @property
    def latest(self):
        return self.events[0] if self.events else None


def detect_carrier(code: str) -> str:
    return "SPX" if code.upper().startswith("SPX") else "GHN"


def _epoch(v) -> int | None:
    """SPX trả epoch giây (đôi khi mili-giây) -> chuẩn hoá về giây."""
    try:
        ts = int(v)
    except (TypeError, ValueError):
        return None
    if ts > 10**12:  # mili-giây
        ts //= 1000
    return ts or None


def _parse_iso(s) -> int | None:
    """'2026-08-01T05:37:32.164Z' / '+07:00' / không múi giờ -> epoch giây."""
    if not s:
        return None
    try:
        s = str(s).strip()
        if not GHN_TIME_IS_UTC and s.endswith("Z"):
            s = s[:-1]  # bỏ Z, coi như giờ VN
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VN_TZ)
        return int(dt.timestamp())
    except Exception:
        return None


# ---------------------------------------------------------------- SPX ----

def parse_spx_payload(code: str, payload: dict) -> TrackingInfo | None:
    """Đọc JSON của spx.vn. Cấu trúc đã kiểm chứng:
    {retcode:0, data:{sls_tracking_info:{records:[
        {description, buyer_description, actual_time, milestone_name, ...}]}}}
    """
    if not isinstance(payload, dict) or payload.get("retcode") != 0:
        return None
    tracking = (payload.get("data") or {}).get("sls_tracking_info") or {}
    records = tracking.get("records") or []
    events, milestones = [], []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        desc = (rec.get("buyer_description") or rec.get("description")
                or rec.get("tracking_name") or "").strip()
        if not desc:
            continue
        events.append(TrackingEvent(desc, _epoch(rec.get("actual_time"))))
        milestones.append((rec.get("milestone_name") or "").strip().lower())
    if not events:
        return None
    # sắp xếp mới nhất lên đầu (sort ổn định — giữ thứ tự API khi trùng giờ)
    order = sorted(range(len(events)), key=lambda i: events[i].ts or 0, reverse=True)
    events = [events[i] for i in order]
    newest_desc = events[0].description.lower()
    newest_mile = milestones[order[0]] if milestones else ""
    done = (any(k in newest_desc for k in SPX_DONE_KEYWORDS)
            or newest_mile in {"delivered", "completed", "cancelled", "returned"})
    return TrackingInfo(code=code, carrier="SPX", events=events, done=done)


class FetchError(Exception):
    """Hãng trả về lỗi (HTTP != 200, không phải JSON, retcode != 0...)."""


_spx_session = None
_spx_warmed = False


def _get_spx_session():
    """Phiên làm việc dùng chung cho SPX (giữ cookie như 1 trình duyệt)."""
    global _spx_session
    if _spx_session is None and CffiSession is not None:
        _spx_session = CffiSession(impersonate="chrome", timeout=20)
    return _spx_session


async def _spx_get(client: httpx.AsyncClient, url: str, **kw):
    """GET tới spx.vn: ưu tiên curl_cffi (giả lập Chrome), không có thì dùng httpx."""
    sess = _get_spx_session()
    try:
        if sess is not None:
            return await sess.get(url, **kw)
        return await client.get(url, headers={**HEADERS, **kw.pop("headers", {})}, **kw)
    except Exception as e:
        raise FetchError(f"lỗi mạng: {e!r}")


async def _spx_warmup(client: httpx.AsyncClient):
    """Ghé trang spx.vn/track như người thật để lấy cookie, rồi mới gọi API."""
    global _spx_warmed
    try:
        await _spx_get(client, "https://spx.vn/track")
        _spx_warmed = True
    except FetchError:
        pass


async def _fetch_spx(client: httpx.AsyncClient, code: str) -> TrackingInfo | None:
    if not _spx_warmed:
        await _spx_warmup(client)
    r = await _spx_get(client, SPX_API,
                       params={"spx_tn": code, "language_code": "vi"},
                       headers={"Referer": "https://spx.vn/track"})
    if r.status_code != 200:
        raise FetchError(f"HTTP {r.status_code}: {r.text[:120]!r}")
    try:
        payload = r.json()
    except ValueError:
        raise FetchError(f"phản hồi không phải JSON: {r.text[:80]!r}")
    if not isinstance(payload, dict) or payload.get("retcode") != 0:
        rc = payload.get("retcode") if isinstance(payload, dict) else "?"
        msg = payload.get("message") if isinstance(payload, dict) else ""
        raise FetchError(f"retcode={rc} message={msg!r}")
    info = parse_spx_payload(code, payload)
    if info is None:
        raise FetchError("retcode=0 nhưng chưa có dòng trạng thái nào")
    return info


# ---------------------------------------------------------------- GHN ----

def _ghn_event_desc(item: dict) -> str:
    status = (item.get("status") or item.get("status_code") or "").strip()
    name = (item.get("status_name") or "").strip() or GHN_STATUS_VI.get(status, status)
    if not name:
        return ""
    loc = item.get("location") or {}
    addr = (loc.get("address") or "").strip() if isinstance(loc, dict) else ""
    # chỉ nối địa chỉ khi nó ngắn gọn và không phải câu mô tả lặp lại
    if addr and len(addr) <= 70 and not addr.lower().startswith("đơn hàng"):
        name = f"{name} — {addr}"
    return name


def parse_ghn_payload(code: str, payload: dict) -> TrackingInfo | None:
    """Đọc JSON của donhang.ghn.vn. Cấu trúc đã kiểm chứng:
    {code:200, data:{order_info:{status, status_name, ...},
                     tracking_logs:[{status, status_name, action_at, location:{address}, ...}]}}
    """
    if not isinstance(payload, dict):
        return None
    if str(payload.get("code")) not in ("200", "0") and not payload.get("data"):
        return None
    data = payload.get("data") or {}
    logs = data.get("tracking_logs") or data.get("logs") or []
    order = data.get("order_info") or data.get("order") or {}
    events = []
    for item in logs:
        if not isinstance(item, dict):
            continue
        desc = _ghn_event_desc(item)
        if not desc:
            continue
        raw_t = (item.get("action_at") or item.get("updated_date")
                 or item.get("created_date") or item.get("time"))
        events.append(TrackingEvent(desc, _parse_iso(raw_t)))
    if not events:
        # đơn tồn tại nhưng chưa có log -> dùng trạng thái tổng
        st = (order.get("status") or "").strip()
        if not st:
            return None
        name = (order.get("status_name") or "").strip() or GHN_STATUS_VI.get(st, st)
        events = [TrackingEvent(name, _parse_iso(order.get("updated_date")))]
    order_idx = sorted(range(len(events)), key=lambda i: events[i].ts or 0, reverse=True)
    events = [events[i] for i in order_idx]
    cur = (order.get("status") or "").strip()
    done = cur in GHN_DONE_STATUSES
    return TrackingInfo(code=code, carrier="GHN", events=events, done=done)


async def _fetch_ghn(client: httpx.AsyncClient, code: str) -> TrackingInfo | None:
    headers = {**HEADERS, "Origin": "https://donhang.ghn.vn",
               "Referer": "https://donhang.ghn.vn/"}
    last_err = None
    # GET là cách trang donhang.ghn.vn đang dùng; POST để dự phòng
    for method in ("get", "post"):
        try:
            if method == "get":
                r = await client.get(GHN_API, params={"order_code": code}, headers=headers)
            else:
                r = await client.post(GHN_API, json={"order_code": code}, headers=headers)
            if r.status_code == 200:
                info = parse_ghn_payload(code, r.json())
                if info:
                    return info
            else:
                last_err = f"HTTP {r.status_code}"
        except (httpx.HTTPError, ValueError) as e:
            last_err = repr(e)
            continue
    if last_err:
        raise FetchError(last_err)
    return None


# ---------------------------------------------------------------- API ----

# Client HTTP dùng chung cho cả bot: giữ cookie + tái sử dụng kết nối,
# giống một trình duyệt thật hơn -> ít bị hệ thống chống bot của hãng chặn.
_client: httpx.AsyncClient | None = None

# Bộ nhớ đệm: lần gần nhất tra cứu THÀNH CÔNG của từng mã (đầy đủ hành trình).
# Dùng khi hãng tạm thời không phản hồi, để màn Chi tiết không bị cụt còn 1 dòng.
_last_good: dict[str, tuple[TrackingInfo, float]] = {}
_CACHE_MAX = 500


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=20, follow_redirects=True)
    return _client


async def close_client():
    global _client, _spx_session, _spx_warmed
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
    if _spx_session is not None:
        try:
            await _spx_session.close()
        except Exception:
            pass
    _spx_session, _spx_warmed = None, False


if CffiSession is None:
    log.warning("Chưa cài curl_cffi — SPX có thể trả 403. Chạy: venv/bin/pip install curl_cffi")


def cached_tracking(code: str) -> tuple[TrackingInfo, float] | None:
    """(TrackingInfo, thời điểm lấy được) của lần tra cứu thành công gần nhất, hoặc None."""
    return _last_good.get(code.strip().upper())


def _remember(code: str, info: TrackingInfo):
    _last_good[code] = (info, time.time())
    if len(_last_good) > _CACHE_MAX:  # giữ gọn bộ nhớ
        oldest = min(_last_good, key=lambda k: _last_good[k][1])
        _last_good.pop(oldest, None)


async def fetch_tracking(code: str, client: httpx.AsyncClient | None = None,
                         attempts: int = 3) -> TrackingInfo | None:
    """Trả về TrackingInfo, hoặc None nếu sau `attempts` lần vẫn không lấy được.

    Lỗi gì cũng được bắt lại (không bao giờ làm sập vòng check ngầm) và ghi log
    lý do cụ thể: xem bằng  journalctl -u npa-tracking | grep "Không lấy được"
    """
    code = code.strip().upper()
    client = client or get_client()
    is_spx = detect_carrier(code) == "SPX"
    last_err = "không rõ"
    for i in range(attempts):
        try:
            info = await (_fetch_spx(client, code) if is_spx else _fetch_ghn(client, code))
            if info and info.events:
                if i > 0:
                    log.info("✅ %s lấy được ở lần thử thứ %s", code, i + 1)
                _remember(code, info)
                return info
            last_err = "không có dữ liệu"
        except FetchError as e:
            last_err = str(e)
        except httpx.HTTPError as e:
            last_err = f"lỗi mạng: {e!r}"
        except Exception as e:  # phòng mọi bất ngờ
            last_err = f"lỗi lạ: {e!r}"
        if i < attempts - 1:
            if is_spx:
                await _spx_warmup(client)
            await asyncio.sleep(1.5 * (i + 1))
    log.warning("Không lấy được %s sau %s lần: %s", code, attempts, last_err)
    return None
