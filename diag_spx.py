# -*- coding: utf-8 -*-
"""
Chẩn đoán vì sao SPX trả 403 cho server:
    venv/bin/pip install -q curl_cffi
    venv/bin/python diag_spx.py <MÃ_SPX>

Thử 3 cách gọi khác nhau:
  A. httpx (thư viện bot đang dùng) + header giống Chrome
  B. lệnh curl của hệ thống
  C. curl_cffi giả lập "vân tay" TLS của Chrome thật
Nếu C được mà A không → SPX chặn theo vân tay trình duyệt → sửa được bằng code.
Nếu cả 3 đều 403 → SPX chặn cả IP máy chủ → cần đường vòng khác.
"""
import subprocess
import sys

import httpx

if len(sys.argv) < 2:
    sys.exit("Cách dùng: venv/bin/python diag_spx.py <MÃ_SPX>")
code = sys.argv[1].upper()
URL = f"https://spx.vn/shipment/order/open/order/get_order_info?spx_tn={code}&language_code=vi"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
     "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8", "Referer": "https://spx.vn/track"}


def show(name, status, body, server=""):
    body = (body or "").replace("\n", " ")[:160]
    print(f"\n[{name}] HTTP {status}  server={server!r}\n    {body}", flush=True)


# A. httpx
try:
    with httpx.Client(timeout=20, follow_redirects=True) as c:
        c.get("https://spx.vn/track", headers={**H, "Accept": "text/html"})
        r = c.get(URL, headers=H)
        show("A httpx", r.status_code, r.text, r.headers.get("server", ""))
except Exception as e:
    print("\n[A httpx] lỗi:", repr(e), flush=True)

# B. curl hệ thống
try:
    out = subprocess.run(
        ["curl", "-s", "-A", UA, "-H", "Referer: https://spx.vn/track",
         "-w", "\n__STATUS__%{http_code}", URL],
        capture_output=True, text=True, timeout=30).stdout
    body, _, st = out.rpartition("__STATUS__")
    show("B curl", st.strip(), body)
except Exception as e:
    print("\n[B curl] lỗi:", repr(e), flush=True)

# C. curl_cffi giả lập Chrome
try:
    from curl_cffi import requests as creq
    s = creq.Session(impersonate="chrome")
    s.get("https://spx.vn/track", timeout=20)
    r = s.get(URL, headers={"Referer": "https://spx.vn/track"}, timeout=20)
    show("C curl_cffi chrome", r.status_code, r.text, r.headers.get("server", ""))
except ImportError:
    print("\n[C curl_cffi] chưa cài → chạy: venv/bin/pip install -q curl_cffi", flush=True)
except Exception as e:
    print("\n[C curl_cffi] lỗi:", repr(e), flush=True)

# IP công khai của server (để biết đang bị chặn IP nào)
try:
    print("\nIP server:", httpx.get("https://api.ipify.org", timeout=10).text, flush=True)
except Exception:
    pass
