# Cài đặt và cấu hình

Hướng dẫn chuẩn bị một máy mới để chạy Online Judge. Tài liệu này chỉ mô tả
tên và mục đích cấu hình; **không chứa giá trị từ `.env`, mật khẩu, token hay
OAuth credentials**. Không gửi hoặc commit các tệp bí mật lên Git.

## 1. Yêu cầu

- Git.
- Python 3.11.
- PostgreSQL đang chạy và có quyền tạo database/user.
- Một dịch vụ Judge0 có thể truy cập từ máy chạy backend nếu cần chấm bài.
- Tài khoản Google OAuth/Gmail nếu cần gửi email xác thực và đặt lại mật khẩu.

Frontend là HTML/CSS/JavaScript tĩnh; không cần Node.js hoặc bước build.

## 2. Lấy mã nguồn và cài backend

Clone repository, sau đó từ thư mục gốc:

```bash
cd backend
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Trên Windows PowerShell, có thể tạo virtual environment bằng
`py -3.11 -m venv .venv`, sau đó dùng `.venv\Scripts\python.exe` thay cho
`.venv/bin/python` trong các lệnh và `.venv\Scripts\pytest.exe` để chạy test.

## 3. Tạo database PostgreSQL

Tạo một database và một PostgreSQL role riêng cho ứng dụng. Cấp role đó quyền
kết nối và tạo/cập nhật bảng trong database. Dùng thông tin kết nối này để
cấu hình `DATABASE_URL` ở bước tiếp theo.

URL kết nối dùng driver psycopg của SQLAlchemy, theo cấu trúc:

```text
postgresql+psycopg://<user>:<password>@<host>:<port>/<database>
```

Nếu username hoặc password có ký tự đặc biệt, URL-encode các thành phần đó.
Không ghi URL thực vào tài liệu, issue, log hoặc lệnh được chia sẻ.

## 4. Cấu hình riêng trên máy

Tạo `backend/.env` cục bộ; không lấy `.env` từ máy khác. Backend đọc tệp này
theo vị trí của project. Chỉ tự nhập các giá trị phù hợp với máy mới:

| Tên cấu hình | Bắt buộc | Mục đích |
|---|---:|---|
| `DATABASE_URL` | Có | Kết nối PostgreSQL của bước 3 |
| `JUDGE0_URL` | Khi cần chấm bài | URL gốc của API Judge0 mà backend truy cập được; mặc định là `http://localhost:2358` |
| `CORS_ORIGINS` | Tùy chọn | Các origin frontend bổ sung, phân cách bằng dấu phẩy; chỉ nhập origin, không thêm path |
| `SESSION_COOKIE_SECURE` | Tùy chọn | Bật `true` khi frontend/backend dùng HTTPS; để `false` khi phát triển qua HTTP |
| `GOOGLE_CREDENTIALS_PATH` | Khi dùng email | Đường dẫn tới tệp OAuth client credentials của Google |
| `GOOGLE_TOKEN_PATH` | Tùy chọn | Nơi lưu token OAuth được tạo khi cấp quyền; mặc định là `backend/token.json` |
| `GOOGLE_CLIENT_ID` | Khi dùng đăng nhập Google | OAuth 2.0 Client ID loại Web |
| `GOOGLE_CLIENT_SECRET` | Khi dùng đăng nhập Google | Client secret tương ứng, chỉ lưu phía backend |
| `GOOGLE_CLIENT_SECRETS_FILE` | Khi dùng đăng nhập Google | Đường dẫn tới tệp JSON OAuth client loại Web; thay thế được hai biến ID/secret |
| `GOOGLE_REDIRECT_URI` | Khi dùng đăng nhập Google | Callback URI đã đăng ký; mặc định `http://localhost:8000/auth/google/callback` |
| `FRONTEND_BASE_URL` | Tùy chọn | Origin frontend sau đăng nhập; mặc định `http://localhost:5500` |

Các origin localhost thường dùng đã được cấu hình sẵn. Nếu dùng địa chỉ hoặc
cổng frontend khác, thêm origin chính xác vào `CORS_ORIGINS`, ví dụ dạng
`http://<host>:<port>`.

Giữ `.env`, tệp OAuth credentials và token OAuth trên máy chủ, giới hạn quyền
đọc tệp phù hợp và không commit/chia sẻ chúng. `GOOGLE_TOKEN_PATH` nên trỏ tới
nơi riêng tư có thể ghi được; token sẽ được tạo sau lần xác thực OAuth đầu
tiên. Nếu không cấu hình Gmail, backend vẫn có thể chạy nhưng đăng ký/xác
thực email và đặt lại mật khẩu sẽ không gửi được email.

Để bật đăng nhập Google, tạo OAuth Client loại Web trong Google Cloud và thêm
chính xác giá trị `GOOGLE_REDIRECT_URI` vào Authorized redirect URIs. Dùng
Client ID và secret riêng cho backend hoặc trỏ
`GOOGLE_CLIENT_SECRETS_FILE` tới JSON của client Web; không dùng tệp
credentials/token của Gmail. Tệp JSON và `.env` chứa thông tin nhạy cảm, phải
giữ ngoài Git và không chia sẻ công khai.

Callback phải dùng địa chỉ trình duyệt truy cập được, không nhất thiết là cổng
backend nội bộ. Ví dụ backend nghe ở `localhost:8000` nhưng được port-forward
thành `localhost:8001`, cấu hình và đăng ký
`http://localhost:8001/auth/google/callback` trong cả `.env`, JSON client Web
và Google Cloud. Giữ nguyên hostname ở frontend, callback và luồng đăng nhập;
không trộn `localhost` với `127.0.0.1`. Sau khi sửa cấu hình OAuth phải khởi
động lại backend và bắt đầu một lượt đăng nhập mới.

## 5. Tạo schema

Từ thư mục `backend`, chạy:

```bash
PYTHONPATH=. .venv/bin/python db/initialize_tables.py
PYTHONPATH=. .venv/bin/python db/migrate_judge_admin.py
```

Lệnh đầu tạo các bảng còn thiếu theo model hiện tại. Lệnh migration tiếp theo
bổ sung schema judge cần thiết và xóa bảng cũ `solution` nếu còn tồn tại.
Migration này có thể xóa dữ liệu còn sót trong bảng đó; xem chi tiết tại
[database.md](./database.md) và [backend.md](./backend.md).

Trong Windows PowerShell, đứng trong `backend` và đặt `PYTHONPATH` như sau
trước mỗi lệnh Python:

```powershell
$env:PYTHONPATH = "."
```

Trong các lệnh tiếp theo trên Windows, thay
`.venv/bin/python` bằng `.venv\Scripts\python.exe`.

Nếu cần bật chấm bài, trước tiên bảo đảm Judge0 đang hoạt động và backend
truy cập được URL đã cấu hình. Sau đó đồng bộ các language ID mà Judge0 hỗ trợ:

```bash
PYTHONPATH=. .venv/bin/python db/initialize_languages.py
```

Chạy lại lệnh đồng bộ khi thay đổi cấu hình hoặc phiên bản Judge0. Nếu bỏ qua,
API và giao diện có thể khởi động nhưng danh sách ngôn ngữ/nộp bài sẽ chưa
sẵn sàng.

## 6. Chạy ứng dụng

Mở hai terminal từ thư mục gốc của repository.

Terminal backend:

```bash
cd backend
PYTHONPATH=. .venv/bin/python -m src.serve
```

API lắng nghe cổng `8000`; mở `http://localhost:8000/docs` để kiểm tra API.

Terminal frontend:

```bash
python3 -m http.server 5500 --bind 127.0.0.1 --directory frontend
```

Mở `http://localhost:5500`. Nếu cần truy cập frontend từ thiết bị khác trong
mạng LAN, bind frontend vào `0.0.0.0`, cấu hình origin phù hợp trong
`CORS_ORIGINS` nếu cần, và chỉ mở các cổng cần thiết trên firewall. CORS không
thay thế xác thực hay bảo vệ API; không đưa API lên Internet bằng cấu hình
phát triển này.

## 7. Gmail OAuth (tùy chọn)

Để gửi email xác thực tài khoản và đặt lại mật khẩu:

1. Tạo OAuth client cho ứng dụng desktop trong Google Cloud Console và bật
   Gmail API cho project đó.
2. Cấu hình consent screen/test users theo chính sách Google hiện hành.
3. Lưu tệp OAuth client credentials riêng trên máy, đặt đường dẫn vào
   `GOOGLE_CREDENTIALS_PATH`.
4. Khởi động backend và thực hiện thao tác cần gửi email. Lần đầu, hoàn tất
   luồng cấp quyền Google trên máy; token được ghi tại `GOOGLE_TOKEN_PATH`.

Không đưa tệp credentials hoặc token vào repository. Mã xác thực và giới hạn
gửi email hiện lưu trong bộ nhớ tiến trình; khi dùng các tính năng này, chỉ
chạy một worker và lưu ý dữ liệu đó mất khi backend khởi động lại.

## 8. Tạo tài khoản quản trị đầu tiên (tùy chọn)

Đăng ký tài khoản qua giao diện và xác thực email trước. Sau đó, người có quyền
quản trị PostgreSQL có thể nâng đúng tài khoản đó lên `SUPER_ADMIN` bằng một
thao tác có kiểm soát. Thay email ví dụ bên dưới bằng email tài khoản vừa xác
thực:

```sql
BEGIN;

UPDATE account
SET role = 'SUPER_ADMIN'
WHERE account_email = 'admin@example.invalid'
  AND is_active = TRUE;

INSERT INTO admin (account_id, is_active)
SELECT account_id, TRUE
FROM account
WHERE account_email = 'admin@example.invalid'
  AND role = 'SUPER_ADMIN'
ON CONFLICT (account_id) DO UPDATE
SET is_active = TRUE;

COMMIT;
```

Kiểm tra kết quả trước khi đăng xuất khỏi phiên database. Không chạy
`backend/db/initialize_users.py` như hiện tại: script đó chứa thông tin đăng
nhập mặc định được viết cứng trong mã nguồn.

## 9. Kiểm tra cài đặt

Chạy bộ test backend từ thư mục `backend`:

```bash
PYTHONPATH=. .venv/bin/pytest -q
```

Các tài liệu liên quan: [README.md](./README.md) để xem tổng quan,
[app.md](./app.md) về tính năng/giao diện/luồng thao tác,
[backend.md](./backend.md) về API và vận hành, và
[database.md](./database.md) về schema.
