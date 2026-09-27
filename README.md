# 🚀 NPA Tracking Bot

Bot Telegram tự động theo dõi vận đơn **Shopee Express (SPX)** và **Giao Hàng Nhanh (GHN)**.

- 🔔 Check ngầm định kỳ (mặc định 3 phút), báo ngay khi đơn đổi trạng thái, kèm nút 📋 Xem chi tiết
- 📥 Thêm mã bằng cách nhắn tin: mỗi dòng 1 mã, sau mã cách ra là note; mã trùng tự bỏ qua
- 📋 `/list` xem danh sách và toàn bộ hành trình, 🗑 `/delete` xoá mã
- 🧹 Đơn giao/hoàn/huỷ xong: gửi toàn bộ lịch trình lần cuối rồi tự xoá khỏi danh sách
- 🔒 Có thể khoá bot chỉ cho một số Chat ID dùng

## Cấu trúc

| File | Vai trò |
|---|---|
| `bot.py` | Bot Telegram: lệnh, nút bấm, vòng check ngầm |
| `carriers.py` | Gọi API tra cứu SPX / GHN, chuẩn hoá dữ liệu |
| `storage.py` | Lưu danh sách mã vào `data.json` (ghi atomic) |
| `test_apis.py` | Chạy thử tra cứu không cần bot |
| `diag_spx.py` | Chẩn đoán khi SPX trả lỗi 403 |
| `deploy.sh` | Cài thành service systemd chạy 24/7 |
| `.env.example` | Mẫu cấu hình |

## Cài đặt trên server (Ubuntu / Oracle Linux)

1. Tạo bot mới với **@BotFather** (`/newbot`) và lấy token.
2. Đưa code lên server (qua `git clone` hoặc `scp`), rồi:

```bash
cd npa-tracking-bot
cp .env.example .env
nano .env            # điền BOT_TOKEN
bash deploy.sh
```

`deploy.sh` tự cài Python, tạo `venv`, cài thư viện, tạo service `npa-tracking` và bật tự khởi động khi reboot. Bot dùng long-polling nên không cần mở port, chạy chung máy với bot khác được, miễn là mỗi bot có token riêng.

## Cấu hình `.env`

| Biến | Ý nghĩa | Mặc định |
|---|---|---|
| `BOT_TOKEN` | Token từ @BotFather | *(bắt buộc)* |
| `CHECK_INTERVAL_MINUTES` | Mấy phút check ngầm 1 lần (nên ≥ 2) | 3 |
| `ALLOWED_CHAT_IDS` | Chỉ các Chat ID này được dùng (cách nhau dấu phẩy); trống = ai cũng dùng được | *(trống)* |
| `DATA_FILE` | File lưu danh sách mã | `data.json` |

Lấy Chat ID: nhắn `/id` cho bot. Sửa `.env` xong thì `sudo systemctl restart npa-tracking`.

## Cách dùng

```
SPXVN000000000001
SPXVN000000000002 áo khoác
GABC1234 đơn GHN
```

| Lệnh | Tác dụng |
|---|---|
| `/list` | Danh sách mã, bấm vào để xem chi tiết + nút xoá |
| `/delete` | Bấm mã để xoá |
| `/id` | Xem Chat ID |
| `/start` | Hướng dẫn |

## Quản lý

```bash
journalctl -u npa-tracking -f          # xem log trực tiếp
sudo systemctl restart npa-tracking    # khởi động lại sau khi sửa code/.env
sudo systemctl stop npa-tracking       # dừng
venv/bin/python test_apis.py <MÃ>      # thử tra cứu 1 mã
```

## Cập nhật code

```bash
git pull
venv/bin/pip install -r requirements.txt
sudo systemctl restart npa-tracking
```

## Xử lý sự cố

- **SPX trả 403 / màn chi tiết báo "Hãng tạm chưa phản hồi"**: SPX chặn các thư viện HTTP thông thường gọi từ máy chủ. Bot dùng `curl_cffi` giả lập Chrome để vượt qua; nếu chưa cài: `venv/bin/pip install curl_cffi`. Chẩn đoán: `venv/bin/python diag_spx.py <MÃ_SPX>`. Xem lý do lỗi: `journalctl -u npa-tracking --since "1 day ago" | grep "Không lấy được"`.
- **Log báo `Conflict: terminated by other getUpdates request`**: có 2 tiến trình cùng chạy 1 token, tắt bớt một cái.
- **`deploy.sh` lỗi vì ký tự `\r`**: file bị đổi sang xuống dòng Windows, chạy `sed -i 's/\r$//' deploy.sh`.
- **Dùng trong group**: @BotFather → `/setprivacy` → Disable.

## Lưu ý

- Bot dùng API tra cứu công khai của spx.vn và donhang.ghn.vn, không phải API đối tác chính thức. Hãng đổi API thì chỉ cần sửa `carriers.py`.
- `.env` (token) và `data.json` (danh sách mã) đã nằm trong `.gitignore`, không được đưa lên GitHub. Lộ token thì vào @BotFather → `/revoke` để cấp token mới.
