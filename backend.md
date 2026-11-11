# Backend: API, vận hành và tích hợp

Tài liệu này là nguồn tham khảo cho backend FastAPI: cấu trúc, API, phân quyền,
biến môi trường, migration, tích hợp Judge0/email và cách chạy/kiểm thử. Giao
diện cùng hành trình người dùng nằm trong [app.md](./app.md); schema và dữ
liệu lưu trữ nằm trong [database.md](./database.md).

## Kiến trúc và mã nguồn

- `backend/src/app.py`: tạo FastAPI app, cấu hình CORS và gắn các router.
- `backend/routers/`: API xác thực/tài khoản và API lớp học, bài tập, contest,
  lịch sử nộp.
- `backend/db/schemas.py`: request models, kiểm tra đầu vào và giới hạn dữ liệu.
- `backend/db/tables_phase1.py`, `tables_phase2.py`: SQLAlchemy models đang
  hoạt động.
- `backend/db/session.py`: engine và dependency cấp database session cho API.
- `backend/db/redis_client.py`, `redis_session.py`: kết nối Redis và quản lý
  session đăng nhập.
- `backend/db/initialize_tables.py`: tạo các bảng còn thiếu.
- `backend/db/migrate_judge_admin.py`: migration lặp lại an toàn cho schema
  judge; hiện cũng xóa vĩnh viễn bảng `solution` đã ngừng sử dụng.
- `backend/tests/`: kiểm thử API, quyền truy cập, phiên và migration.

Backend dùng FastAPI, SQLAlchemy, PostgreSQL, Redis và session cookie. Account
và dữ liệu nghiệp vụ vẫn ở PostgreSQL; session đăng nhập được lưu trong Redis
với thời hạn 24 giờ. Mã nguồn bài nộp được gửi tới Judge0; FastAPI không thực
thi mã nguồn trực tiếp. Danh sách ngôn ngữ được đồng bộ từ Judge0 vào bảng
`language`.

## API

Các endpoint cần đăng nhập nhận cookie `session_id`. Lỗi phổ biến: `401` khi
chưa đăng nhập/session hết hạn, `403` khi không đủ quyền, `404` khi không tìm
thấy tài nguyên, `409` khi xung đột dữ liệu, `422` khi request không hợp lệ.
Chi tiết request/response có thể xem tại OpenAPI `/docs` khi backend đang chạy.

### Xác thực và tài khoản

| Method | Endpoint | Quyền / mục đích |
|---|---|---|
| `POST` | `/auth/register` | Tạo tài khoản USER chưa kích hoạt |
| `POST` | `/auth/send-code`, `/auth/send-otp` | Gửi mã xác thực email |
| `POST` | `/auth/verify-code`, `/auth/verify-otp` | Xác thực email |
| `POST` | `/auth/login` | Đăng nhập và tạo cookie/session |
| `GET` | `/auth/google` | Bắt đầu OAuth Google, đặt cookie `state` và PKCE |
| `GET` | `/auth/google/callback` | Kiểm tra `state`/PKCE, xác minh ID token và email, tạo session |
| `POST` | `/auth/logout` | Xóa session hiện tại và cookie |
| `GET`, `PATCH` | `/auth/me` | Đọc hồ sơ hiện tại; cập nhật tên |
| `POST` | `/auth/forgot-password` | Gửi mã đặt lại mật khẩu |
| `GET` | `/auth/email-delivery/{request_id}` | Kiểm tra trạng thái yêu cầu gửi email đang chờ |
| `POST` | `/auth/reset-password` | Đặt mật khẩu mới bằng mã xác thực |
| `GET` | `/admin/accounts` | SUPER_ADMIN: liệt kê tài khoản |
| `PATCH` | `/admin/accounts/{account_id}/role` | SUPER_ADMIN: đổi vai trò |
| `DELETE` | `/admin/accounts/{account_id}` | Xóa tài khoản theo quy tắc bảo vệ super admin |

Vai trò là `USER`, `ADMIN`, `SUPER_ADMIN`. Backend luôn xác minh session,
trạng thái kích hoạt và quyền; ẩn nút trên frontend không phải biện pháp phân
quyền. Session/cookie có thời hạn một ngày. Thành viên đã tham gia lớp có thể
xem contest, bảng xếp hạng và lịch sử khi contest đang diễn ra hoặc đã kết thúc;
contest sắp diễn ra chỉ quản trị viên lớp mới xem được. Quản trị viên được phân
công quản lý lớp có thể xem mọi trạng thái; việc sửa contest thay danh sách/thứ
tự bài cũng không xóa các bài nộp cũ, nên lịch sử cũ có thể tham chiếu tới bài
không còn thuộc cấu hình contest hiện tại.

### Bài tập và chấm bài

| Method | Endpoint | Quyền / mục đích |
|---|---|---|
| `GET` | `/admin/judge/problems?q=...` | ADMIN: danh sách bài được phép quản lý; `q` tìm tên |
| `GET` | `/admin/judge/problems/{problem_id}` | ADMIN chủ bài hoặc SUPER_ADMIN: xem bài và testcase |
| `POST` | `/admin/judge/problems` | ADMIN: tạo bài, có thể kèm testcase |
| `PATCH` | `/admin/judge/problems/{problem_id}` | Chủ bài hoặc SUPER_ADMIN: cập nhật bài/testcase |
| `DELETE` | `/admin/judge/problems/{problem_id}` | SUPER_ADMIN: xóa bài và dữ liệu phụ thuộc |
| `GET` | `/student/problems?q=...` | Người dùng đăng nhập: tìm bài public theo tên |
| `GET` | `/student/problems/{problem_id}` | Người dùng đăng nhập: đọc bài public |
| `POST` | `/student/problems/{problem_id}/run` | Người dùng đăng nhập: chạy thử bài public với stdin tự nhập; không tạo bài nộp |
| `POST` | `/student/problems/{problem_id}/submissions` | Nộp bài public và chấm qua Judge0 |
| `GET` | `/student/languages` | Lấy ngôn ngữ đã đồng bộ |

Giới hạn/đầu vào bài tập được định nghĩa bởi `ProblemCreateIn`,
`ProblemUpdateIn`, `ProblemTestcaseIn` trong `backend/db/schemas.py`.

### Chạy thử code trong mini IDE

Hai endpoint `/run` chuyển source code và input tự nhập tới Judge0, dùng
time limit và memory limit của bài. Request không nhận testcase ID hoặc
expected output, không đọc hay trả test case ẩn và không tạo bản ghi
`submission`; endpoint này chỉ trả kết quả để hiển thị trong mini IDE. Với
bài public, yêu cầu chỉ chạy được nếu người dùng đã đăng nhập. Với bài trong
lớp, người dùng phải là thành viên lớp và bài phải được gán vào lớp.

Contest có thể dùng endpoint chạy thử trong lớp bằng cách truyền thêm
`contest_id`. Khi đó bài phải thuộc contest được gán cho lớp, contest phải
đang diễn ra và người dùng phải có quyền xem bài trong contest.
Mỗi lượt chạy có thời hạn chờ Judge0 tối đa 15 giây; Judge0 còn áp dụng giới
hạn CPU và bộ nhớ của bài.

Request JSON:

```json
{
  "language_id": 71,
  "source_code": "print(input())",
  "stdin": "hello\n",
  "contest_id": 12
}
```

`contest_id` là tùy chọn và chỉ dùng cho bài chạy thử trong lớp/contest.
`source_code` bắt buộc, không chỉ có khoảng trắng và tối đa 65.536 ký tự;
`stdin` mặc định là chuỗi rỗng và tối đa 16.384 ký tự. `language_id` phải
tồn tại trong danh sách lấy từ `/student/languages`. Request model tương ứng
là `StudentRunCodeIn` trong `backend/db/schemas.py`.

Response thành công gồm trạng thái Judge0, stdout, stderr, output biên dịch và
các metric có thể có:

```json
{
  "status": "Accepted",
  "status_id": 3,
  "stdout": "hello\n",
  "stderr": "",
  "compile_output": "",
  "runtime_ms": 25,
  "memory_kb": 1024
}
```

`status_id` là mã trạng thái Judge0; chỉ ID `3` là chạy thành công. Nếu
chương trình không in stdout, giá trị là chuỗi rỗng. `runtime_ms` và
`memory_kb` có thể là `null` nếu Judge0 không cung cấp metric. Lỗi kết nối
Judge0 trả `503`, hết hạn chạy trả `504`, phản hồi Judge0 không hợp lệ trả
`502`; input sai trả `422`, còn lỗi đăng nhập/quyền/tài nguyên dùng các mã
`401`, `403`, `404` hoặc `409` tương ứng.

### Lớp học và phân quyền lớp

| Method | Endpoint | Quyền / mục đích |
|---|---|---|
| `GET`, `POST` | `/admin/judge/academic-classes` | ADMIN: liệt kê/tạo lớp |
| `GET` | `/admin/judge/academic-classes/{class_id}` | Quản trị lớp: xem nội dung/roster |
| `DELETE` | `/admin/judge/academic-classes/{class_id}` | SUPER_ADMIN: xóa lớp và liên kết |
| `POST` | `/admin/judge/academic-classes/{class_id}/claim` | ADMIN: nhận quản lý lớp chưa có chủ |
| `GET` | `/admin/judge/academic-classes/{class_id}/join-requests` | Chủ lớp: xem yêu cầu đang chờ |
| `PATCH` | `/admin/judge/academic-classes/{class_id}/join-requests/{request_id}` | Chủ lớp: duyệt hoặc từ chối |
| `GET`, `POST` | `/admin/judge/academic-classes/{class_id}/managers` | Chủ lớp: xem/mời quản trị viên |
| `GET` | `/admin/judge/academic-classes/{class_id}/available-managers?search=...` | Tìm quản trị viên để mời |
| `DELETE` | `/admin/judge/academic-classes/{class_id}/managers/{account_id}` | Chủ lớp: thu hồi quyền quản lý |
| `GET` | `/admin/judge/academic-classes/{class_id}/available-students?search=...` | Quản trị lớp: tìm thành viên để mời |
| `POST`, `DELETE` | `/admin/judge/academic-classes/{class_id}/students[/{account_id}]` | Quản trị lớp: thêm/xóa thành viên |
| `POST` | `/admin/judge/academic-classes/{class_id}/problems` | Gán bài có sẵn vào lớp |
| `POST` | `/admin/judge/academic-classes/{class_id}/problems/create` | Tạo bài và gán vào lớp |
| `GET` | `/student/classes` | Liệt kê lớp đã tham gia |
| `GET` | `/student/available-classes` | Khám phá lớp và trạng thái yêu cầu |
| `POST` | `/student/classes/{class_id}/join-requests` | Gửi yêu cầu vào lớp |
| `GET` | `/student/classes/{class_id}` | Thành viên: xem bài, danh sách thành viên và contest của lớp |
| `GET` | `/student/classes/{class_id}/submissions` | Lịch sử bài nộp trong lớp |
| `POST` | `/student/classes/{class_id}/problems/{problem_id}/run` | Thành viên: chạy thử bằng stdin tự nhập; tùy chọn `contest_id` trong contest đang diễn ra |
| `POST` | `/student/classes/{class_id}/problems/{problem_id}/submissions` | Nộp bài trong lớp |

Quản trị lớp được xác định qua quan hệ `manage_class`; chủ lớp là quản trị
viên sở hữu lớp. Các endpoint kiểm tra riêng quyền quản trị, chủ lớp và thành
viên lớp theo từng thao tác.

API lớp dành cho thành viên chỉ trả tên và vai trò trong danh sách thành viên;
endpoint quản trị lớp trả thêm email và thông tin roster phục vụ công cụ quản lý.

### Contest và bài nộp

| Method | Endpoint | Quyền / mục đích |
|---|---|---|
| `GET`, `POST` | `/admin/judge/contests` | ADMIN: liệt kê/tạo contest |
| `GET` | `/admin/judge/contests/{contest_id}` | ADMIN: xem contest và bài |
| `DELETE` | `/admin/judge/contests/{contest_id}` | SUPER_ADMIN: xóa contest/liên kết/bài nộp |
| `POST` | `/admin/judge/contests/{contest_id}/problems` | Thêm bài vào contest |
| `POST` | `/admin/judge/academic-classes/{class_id}/contests` | Gán contest có sẵn vào lớp |
| `POST` | `/admin/judge/academic-classes/{class_id}/contests/create` | Tạo contest và gán bài lớp |
| `PATCH` | `/admin/judge/academic-classes/{class_id}/contests/{contest_id}` | Quản trị lớp: sửa contest và thay danh sách/thứ tự bài |
| `GET` | `/admin/judge/contests/{contest_id}/standings` | Xem bảng xếp hạng |
| `GET` | `/student/classes/{class_id}/contests/{contest_id}` | Thành viên lớp xem contest đang diễn ra/đã kết thúc; quản trị lớp xem mọi trạng thái |
| `GET` | `/student/classes/{class_id}/contests/{contest_id}/standings` | Thành viên lớp xem khi contest đang diễn ra/đã kết thúc; quản trị lớp xem mọi trạng thái |
| `GET` | `/student/classes/{class_id}/submissions?contest_id={contest_id}` | Thành viên lớp xem lịch sử contest đang diễn ra/đã kết thúc; quản trị lớp xem mọi trạng thái |
| `GET` | `/admin/judge/submissions` | SUPER_ADMIN: quản lý bài nộp |
| `DELETE` | `/admin/judge/submissions/{submission_id}` | SUPER_ADMIN: xóa bài nộp |
| `GET` | `/student/submissions` | Lịch sử toàn server, có lọc và phân trang |
| `GET` | `/student/submissions/{submission_id}/source` | Chủ bài nộp: xem mã nguồn |
| `GET` | `/student/classes/{class_id}/contests/{contest_id}/submissions/{submission_id}/source` | Quản trị lớp: xem mã nộp trong contest |
| `GET` | `/admin/judge/health` | Kiểm tra API |

Lịch sử danh sách không trả mã nguồn. Endpoint xem mã xác minh quyền sở hữu
hoặc quyền quản lý contest theo phạm vi. Schema và chính sách lưu source/metrics
được mô tả ở [database.md](./database.md).

## Cấu hình môi trường

Đặt các giá trị trong `backend/.env`; không commit thông tin xác thực.

| Biến | Ý nghĩa |
|---|---|
| `DATABASE_URL` | URL PostgreSQL, ví dụ định dạng `postgresql+psycopg://...` |
| `REDIS_URL` | URL Redis, ví dụ `redis://127.0.0.1:6379/0`; bắt buộc |
| `JUDGE0_URL` | URL Judge0; mặc định `http://localhost:2358` |
| `SESSION_COOKIE_SECURE` | Đặt `true` khi dùng HTTPS; mặc định `false` |
| `CORS_ORIGINS` | Danh sách origin frontend, phân tách bằng dấu phẩy |
| `GOOGLE_CREDENTIALS_PATH` | Đường dẫn OAuth credentials cho Gmail |
| `GOOGLE_TOKEN_PATH` | Đường dẫn token OAuth; mặc định `backend/token.json` |
| `GOOGLE_CLIENT_ID` | OAuth 2.0 Client ID loại Web cho đăng nhập Google |
| `GOOGLE_CLIENT_SECRET` | Client secret tương ứng; chỉ lưu ở backend |
| `GOOGLE_CLIENT_SECRETS_FILE` | Đường dẫn tệp JSON OAuth client loại Web; có thể dùng thay cho ID/secret riêng |
| `GOOGLE_REDIRECT_URI` | Callback đã đăng ký trong Google Cloud; mặc định `http://localhost:8000/auth/google/callback` |
| `FRONTEND_BASE_URL` | Origin frontend sau đăng nhập; mặc định `http://localhost:5500` |

Đăng nhập Google cần cấu hình OAuth Client loại Web trong Google Cloud, bật
OpenID Connect và đăng ký chính xác `GOOGLE_REDIRECT_URI` làm Authorized
redirect URI. Có thể cấu hình `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` hoặc
dùng `GOOGLE_CLIENT_SECRETS_FILE` trỏ tới tệp JSON OAuth client loại Web.
Không dùng Gmail credentials hoặc `token.json` cho luồng đăng nhập. Luồng dùng
authorization code, scope OpenID/email/profile của Google, state và PKCE;
cookie state/verifier là `HttpOnly`, `SameSite=Lax` và tự hết hạn sau 10 phút.
Tài khoản mới chỉ được tạo khi ID token hợp lệ và Google xác nhận email;
email trùng với tài khoản hiện có sẽ đăng nhập vào tài khoản đó, kích hoạt
tài khoản chưa active và giữ nguyên tên/vai trò hiện có.

Với port forwarding, `GOOGLE_REDIRECT_URI` phải là địa chỉ mà trình duyệt truy
cập được và phải khớp chính xác một URI trong tệp client Web lẫn cấu hình
Google Cloud. Ví dụ nếu cổng backend `8000` được forward thành `localhost:8001`,
hãy đăng ký và cấu hình
`http://localhost:8001/auth/google/callback`; backend nội bộ vẫn lắng nghe
cổng `8000`. Dùng cùng hostname (`localhost` hoặc `127.0.0.1`) xuyên suốt.
Sau khi đổi `.env` hoặc tệp OAuth, khởi động lại backend.

### Redis session

Redis là dependency bắt buộc để backend khởi động. Trên máy Debian 12 chạy
Redis cục bộ, cài và bật dịch vụ bằng:

```bash
sudo apt update
sudo apt install redis-server
sudo systemctl enable --now redis-server
sudo systemctl status redis-server
redis-cli ping
```

`redis-cli ping` cần trả về `PONG`. Chỉ bind Redis vào loopback nếu Redis và
backend cùng máy; không mở cổng `6379` ra Internet. Đặt
`REDIS_URL=redis://127.0.0.1:6379/0` trong `backend/.env`, giữ tệp riêng tư,
rồi khởi động lại backend. Nếu Redis nằm ở máy khác, dùng địa chỉ riêng được
bảo vệ bằng firewall và cấu hình xác thực/TLS theo hạ tầng đó.

Khi đăng nhập thường hoặc Google OAuth thành công, Redis lưu
`session:<session_id> → account_id` với TTL 24 giờ. Redis cũng lưu tập
`account_sessions:<account_id>` để thu hồi mọi session khi tài khoản bị xóa.
Đăng xuất thu hồi session hiện tại. Account và hồ sơ vẫn đọc từ PostgreSQL;
session SQL cũ không còn được dùng, vì vậy session hiện có sẽ yêu cầu đăng
nhập lại sau khi triển khai. Backend không tự chuyển tiếp các session cũ từ
PostgreSQL sang Redis.

Gửi email xác thực/đặt lại mật khẩu cần cấu hình OAuth Gmail. Mã xác thực và
giới hạn gửi hiện ở bộ nhớ tiến trình: mã hết hạn sau 5 phút, gửi lại cùng
địa chỉ cách nhau 1 phút, mỗi lần thử gửi cách nhau ít nhất 30 giây và tối đa
5 lần thử/phút. Vì dữ liệu không chia sẻ giữa các worker và mất khi restart,
chỉ chạy một worker nếu dùng các giới hạn này.
Endpoint gửi trả `delivery_id`; frontend dùng mã ngẫu nhiên này để kiểm tra
trạng thái `queued`, `sending`, `sent` hoặc `failed` trong tối đa 15 phút.
`sent` nghĩa là Gmail API đã chấp nhận email, không đảm bảo thư vào Inbox;
người dùng nên kiểm tra Spam/Junk. Lỗi gửi được ghi ở log backend nhưng không
ghi mã OTP hoặc nội dung email.

## Chạy local và migration

```bash
cd backend
PYTHONPATH=. .venv/bin/python db/initialize_tables.py
PYTHONPATH=. .venv/bin/python db/migrate_judge_admin.py
PYTHONPATH=. .venv/bin/python -m src.serve
```

`initialize_tables.py` tạo bảng còn thiếu nhưng không tự thêm cột vào bảng đã
tồn tại; migration xử lý các cột cần bổ sung. Migration xóa bảng cũ `solution`
và mọi dữ liệu trong đó. Chạy trước khi khởi động backend sau khi nâng cấp
source code.

Frontend tĩnh:

```bash
cd ..
python3 -m http.server 5500 --bind 0.0.0.0 --directory frontend
```

API mặc định tại `http://localhost:8000`; OpenAPI tại `/docs`. Từ frontend
localhost, API helper thử port `8000`, rồi dò các port forwarded `8001–8010`
nếu cần.

## Triển khai và truy cập mạng

Backend mặc định chấp nhận localhost/LAN trong cấu hình CORS; thêm chính xác
origin frontend vào `CORS_ORIGINS` khi cần. Dùng `127.0.0.1` để chỉ bind
local; dùng `0.0.0.0` để thiết bị khác trong mạng truy cập được, đồng thời
cấu hình firewall phù hợp.

Trình duyệt của người dùng vẫn cần truy cập được backend trên máy chủ. CORS
không làm backend local tự khả dụng từ Internet; không public API nếu chưa
có cấu hình bảo vệ phù hợp. Hướng dẫn UI và luồng thao tác ở [app.md](./app.md).

## Kiểm thử

Từ thư mục `backend`:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONPATH=. .venv/bin/python -m pytest
```

Các kiểm thử chính bao gồm API quản trị/lớp học, đăng nhập/session Redis, xóa
tài khoản và migration schema; luồng chạy thử kiểm tra input tự nhập, quyền
truy cập bài/lớp và bảo đảm không tạo submission. Test session dùng
`fakeredis`; không cần Redis service thật để chạy test.
