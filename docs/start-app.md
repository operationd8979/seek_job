# Hướng dẫn khởi động app Seek Job

Tài liệu dành cho Windows PowerShell. App chạy trên máy cá nhân và mở giao diện
trong trình duyệt tại **http://127.0.0.1:8765**.

## 1. Chuẩn bị lần đầu

Cần Python **3.11 trở lên** và mã nguồn project. Mở PowerShell rồi kiểm tra:

```powershell
python --version
Set-Location 'C:\Users\remotechip\Documents\SYNC\Github\seek_job'
```

Nếu lưu project ở vị trí khác, thay đường dẫn trên bằng thư mục chứa `README.md`
và `requirements.txt` của Seek Job.

Tạo môi trường Python riêng và cài thư viện:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m seek_job validate
```

Chạy từng lệnh; nếu có lỗi, xử lý lỗi trước khi sang bước tiếp theo. Nếu đã có
`.venv`, bỏ qua lệnh tạo môi trường. Bước cài thư viện có thể cần Internet.
Kết quả kiểm tra cấu hình thành công có `"valid": true`; đọc thêm `notes` để biết
thông tin về workspace CV. Lệnh `validate` không tìm việc hoặc tạo CV.

Các lệnh bên dưới gọi trực tiếp Python trong `.venv`, nên không cần activate
môi trường hoặc đổi PowerShell execution policy. Giao diện không cần npm build.

## 2. Khởi động app mỗi lần sử dụng

Mở PowerShell và chạy:

```powershell
Set-Location 'C:\Users\remotechip\Documents\SYNC\Github\seek_job'
.\.venv\Scripts\python.exe -m seek_job ui --port 8765
```

Khi terminal hiện dòng sau, mở địa chỉ đó bằng Edge, Chrome hoặc trình duyệt khác:

```text
Seek Job UI: http://127.0.0.1:8765
```

Giữ cửa sổ PowerShell này mở trong lúc dùng app. Terminal đang chờ và chưa trả
về dấu nhắc là bình thường: server đang phục vụ giao diện.

**Lưu ý tên lệnh:** `ui` mở app; `start` tạo một run tìm việc, không mở giao diện.
Mở app không tự bắt đầu tìm việc hay tạo CV.

## 3. Sử dụng sau khi mở

- Xem các run đã lưu trong **History**, hoặc đọc và lọc job trong **Review**.
- Khi muốn tìm việc, chọn preset phù hợp rồi bấm **Run job search**. Xem mô tả
  preset trong [storage/README.md](../storage/README.md).
- Tạo CV là bước riêng: duyệt job, chọn profile và template, xác nhận ứng viên
  rồi bấm **Tạo CV**.

Chức năng tìm việc qua agent và lập kế hoạch CV cần **Codex CLI trong PATH và đã
đăng nhập**, theo phần [Runner và kiểm tra](../README.md#runner-và-kiểm-tra).
Đây là điều kiện cho các thao tác đó, không phải điều kiện để mở giao diện.
Tạo PDF còn cần workspace `latex_cv`, profile đầy đủ và công cụ build của
workspace đó.

Các file cấu hình liên quan:

| File | Mục đích |
| --- | --- |
| `config/search-config.yaml` | Tiêu chí tìm việc hiện tại và đường dẫn workspace CV |
| `storage/search-config-*.yaml` | Preset để chọn khi tạo run |
| `config/agent-config.yaml` | Model và reasoning cho operation tiếp theo |

## 4. Tắt và khởi động lại

Nhấn **Ctrl + C** tại cửa sổ PowerShell đang chạy app để tắt server. Nếu đang có
operation, dùng nút **Dừng** trong UI trước khi tắt. Đóng tab trình duyệt không
tắt server.

Để khởi động lại, chạy lại lệnh ở bước 2 rồi tải lại trang trình duyệt.

## 5. Lỗi thường gặp

| Hiện tượng | Cách xử lý |
| --- | --- |
| Không nhận lệnh `python` | Kiểm tra Python 3.11+ đã được cài và có trong PATH; mở lại PowerShell sau khi cài. Nếu máy dùng Python Launcher, thử `py -3 --version`, rồi dùng `py -3 -m venv .venv` để tạo môi trường. |
| Không tìm thấy `.venv\Scripts\python.exe` | Chuyển đến đúng thư mục project và thực hiện bước tạo `.venv`. |
| `No module named seek_job` | Chạy `Set-Location` về thư mục project trước khi khởi động. |
| Thiếu module `yaml` hoặc `jsonschema` | Chạy lại `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`. |
| `validate` báo lỗi cấu hình | Đọc đường dẫn và trường được báo lỗi, sửa cấu hình tương ứng rồi chạy lại `validate`. |
| Cổng đang được sử dụng / `WinError 10048` | Kiểm tra app có đang mở ở terminal khác; dùng phiên đang chạy hoặc đổi cổng như ví dụ dưới. |
| Trình duyệt không kết nối được | Kiểm tra terminal còn chạy, không có traceback, và URL dùng đúng cổng đã chọn. |
| Giao diện mở được nhưng thao tác agent lỗi | Kiểm tra Codex CLI trong PATH và trạng thái đăng nhập; xem log operation trong UI. Sau khi thay PATH, mở PowerShell mới và khởi động lại app. |

Ví dụ dùng cổng khác:

```powershell
.\.venv\Scripts\python.exe -m seek_job ui --port 8768
```

Sau đó mở **http://127.0.0.1:8768**. Server chỉ phục vụ trên máy đang chạy app
qua `127.0.0.1`.

Xem [README](../README.md) để biết thêm về review, nhập JD, resume và CV handoff.
