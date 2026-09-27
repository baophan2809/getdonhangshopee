# -*- coding: utf-8 -*-
"""
Chạy thử API tra cứu SPX / GHN ngay trên server (không cần bot):

    venv/bin/python test_apis.py <MÃ_SPX> <MÃ_GHN> ...

Chẩn đoán kỹ khi SPX báo lỗi 403:  venv/bin/python diag_spx.py <MÃ_SPX>
"""
import asyncio
import logging
import sys

from carriers import close_client, fetch_tracking

logging.basicConfig(format="%(levelname)s %(message)s", level=logging.INFO)


async def main():
    args = sys.argv[1:]
    if not args:
        print("Cách dùng: python test_apis.py <MÃ_VẬN_ĐƠN> [mã2] ...")
        return
    for code in args:
        code = code.strip().upper()
        print(f"\n===== {code} =====")
        info = await fetch_tracking(code)
        if not info or not info.events:
            print("❌ Không lấy được dữ liệu (xem dòng WARNING phía trên để biết lý do).")
            continue
        print(f"Hãng: {info.carrier} | Đã xong: {'✅' if info.done else '⏳ chưa'} | "
              f"{len(info.events)} trạng thái")
        for ev in info.events:
            print(f"  • {ev.time_str()} — {ev.description}")
    await close_client()


if __name__ == "__main__":
    asyncio.run(main())
