# Tính năng, giao diện và luồng người dùng

Tài liệu này tập trung vào tính năng người dùng nhìn thấy, cấu trúc giao diện
và luồng thao tác trên frontend HTML/CSS/JavaScript. API và vận hành backend
được mô tả trong [backend.md](./backend.md); schema/lưu trữ nằm trong
[database.md](./database.md); cách khởi chạy nhanh ở [README.md](./README.md).

## Các trang chính

| Trang | Đường dẫn | Mục đích |
|---|---|---|
| Đăng nhập | `frontend/modules/login.html` | Đăng nhập và tạo phiên làm việc |
| Đăng ký | `frontend/modules/register.html` | Tạo tài khoản người dùng |
| Tài khoản | `frontend/modules/index.html` | Xem hồ sơ, sửa tên, xem feed bài nộp toàn server và mở các khu vực khác |
| Kho bài tập | `frontend/modules/problems.html` | Tìm bài public; admin còn có danh sách bài do mình tạo |
| Làm bài | `frontend/modules/problem_submit.html` | Đọc đề và nộp lời giải trong bố cục hai cột |
| Lịch sử bài nộp | `frontend/modules/submissions.html` | Xem lịch sử toàn server và lịch sử lọc theo bài/người |
| Lớp học | `frontend/modules/classroom.html` | Khám phá/tham gia lớp, xem bài và contest được giao, nộp bài trong lớp |
| Đề bài lớp học | `frontend/modules/problem_statement.html` | Mở riêng đề được giao trong lớp |
| Tạo/chỉnh sửa bài | `frontend/modules/create_problem.html` | Quản lý đề, giới hạn chấm và testcase của admin |
| Quản trị judge | `frontend/modules/judge_admin.html` | Quản lý lớp, contest, bài tập và quyền liên quan |
| Quản lý tài khoản | `frontend/modules/manage_accounts.html` | Quản lý tài khoản dành cho super admin |

Mọi trang cần đăng nhập sẽ chuyển người dùng chưa xác thực về trang đăng nhập.
Các thao tác quản trị được hiển thị theo vai trò và vẫn được backend kiểm tra
quyền; ẩn thành phần frontend không thay thế kiểm tra phân quyền phía server.

## Tính năng theo vai trò

- **USER:** đăng ký, xác thực email, đăng nhập/đặt lại mật khẩu, luyện bài
  public, xin vào lớp, làm bài được giao và xem lịch sử.
- **ADMIN:** tạo/quản lý bài, lớp và contest; quản lý nội dung lớp mình được
  phân quyền; mời thành viên hoặc quản trị viên theo quyền chủ lớp.
- **SUPER_ADMIN:** có quyền quản trị toàn cục, quản lý tài khoản và thực hiện
  các thao tác xóa được giới hạn cho vai trò này.
- **Chấm bài:** gửi mã nguồn tới Judge0 và nhận verdict, điểm, tiến độ testcase,
  runtime/bộ nhớ; mã nguồn chỉ hiện theo quyền truy cập lịch sử.

Các màn hình quản trị có thể ẩn nút theo vai trò để giao diện gọn hơn; mọi
quyền vẫn được xác minh ở API.

## Bố cục giao diện

- Các trang dùng thanh điều hướng trên cùng thống nhất cho tài khoản, lớp học,
  kho bài tập, lịch sử và đăng xuất.
- Trang quản trị chi tiết lớp chia nội dung thành tab **Bài tập**,
  **Cuộc thi**, **Thành viên**. Học viên có thêm tab **Lịch sử nộp**. Tab
  hỗ trợ bàn phím trái/phải, Home và End.
- Thành viên lớp xem được danh sách thành viên với tên và vai trò; quản trị
  lớp có thể xem thêm thông tin liên hệ và các công cụ quản lý theo quyền.
- Trang nộp bài và trang lịch sử dùng bố cục nhiều cột ở màn hình rộng, tự xếp
  dọc trên màn hình nhỏ.
- Các form phản hồi trạng thái/lỗi ngay tại khu vực thao tác; danh sách rỗng
  có thông báo hướng dẫn.

## Tài khoản và xác thực

```text
Đăng ký -> gửi mã email -> xác thực -> đăng nhập -> dashboard
                                   └-> quên mật khẩu -> mã email -> đặt lại
```

Sau đăng nhập, session cookie được backend kiểm tra trên các trang cần bảo vệ.
Người dùng có thể xem/cập nhật tên hiển thị và đăng xuất; SUPER_ADMIN mở khu
vực quản lý tài khoản để thay đổi vai trò hoặc xóa tài khoản theo quy tắc bảo vệ.

## Luyện tập bài public

```text
Kho bài tập
  ├─ tìm theo tên bài
  └─ chọn tên bài
       └─ problem_submit.html?problem_id=<id>&source=public
            ├─ đề bài ở nửa trái
            ├─ ngôn ngữ, mã nguồn và nút nộp ở nửa phải
            └─ liên kết lịch sử của bài
                 └─ submissions.html?problem_id=<id>
```

Trang kho bài tập giữ danh sách gọn, có ô tìm kiếm nhỏ ở góc trên của danh
sách. Khi nhập, frontend debounce request tới API; backend lọc trực tiếp trong
cơ sở dữ liệu theo tên bài, không tìm trong mô tả hay nội dung đề.

Trang làm bài chia ngang thành hai cột trên màn hình rộng: đề bên trái và form
nộp bên phải. Trên màn hình hẹp, hai cột xếp thành một cột.

Form lấy danh sách ngôn ngữ từ backend, gửi mã nguồn lên API chấm Judge0 và
hiển thị verdict, điểm, số testcase qua/tổng, runtime và bộ nhớ. Trang không
hiển thị feed bài nộp toàn server; dùng trang lịch sử riêng khi cần xem lịch sử.

## Bài private và bài của admin

- Bài public được mở từ kho bài tập với `source=public`.
- Admin có thể mở bài mình quản lý với `source=owner`. Chỉ chủ bài hoặc super
  admin có quyền đọc/chỉnh sửa testcase qua các API quản trị.
- Trang làm bài không cho nộp trực tiếp bài private. Bài private được nộp từ
  form trong ngữ cảnh lớp học được giao.
- Trong lớp, liên kết xem đề mở
  `problem_statement.html?problem_id=<id>&source=class&class_id=<id>`.
  Quyền truy cập lớp và việc bài có được giao trong lớp được backend xác minh.

## Lịch sử bài nộp

`frontend/modules/submissions.html` là trang lịch sử dùng lại được. Query
parameters có thể truyền trực tiếp trong URL:

| Tham số | Ý nghĩa |
|---|---|
| `problem_name` | Tìm theo một phần tên bài tập, không phân biệt hoa thường |
| `account_name` | Tìm theo một phần tên người nộp, không phân biệt hoa thường |
| `problem_id` | Bộ lọc ID tương thích cho các liên kết cũ |
| `account_id` | Bộ lọc ID tương thích cho các liên kết cũ |

Ví dụ:

```text
modules/submissions.html
modules/submissions.html?problem_name=Two%20Sum
modules/submissions.html?account_name=Nguyen
```

Bộ lọc nằm ở cột bên cạnh danh sách bài nộp; trên màn hình rộng danh sách chiếm
khoảng hai phần ba chiều ngang, còn trên màn hình hẹp hai phần xếp dọc. Khi có
bộ lọc, danh sách hiển thị kết quả khớp tên; nếu không, nó hiển thị bài nộp mới
nhất toàn server. Các kết quả được phân trang và có nút tải các lượt cũ hơn.

Feed toàn server hiển thị trạng thái của bài public và private cho người dùng
đã đăng nhập. Metadata gồm người nộp, bài, trạng thái và các chỉ số chấm; API
không trả mã nguồn trong danh sách. Nút xem mã chỉ có ở bài nộp của chính
người dùng, và endpoint lấy mã nguồn cũng xác minh quyền sở hữu.

## Dashboard và lớp học

- Dashboard có feed bài nộp toàn server dạng phân trang.
- Học viên khám phá lớp, gửi yêu cầu tham gia, chờ chủ lớp duyệt rồi mở nội dung.
- Trang chi tiết lớp chia bài tập, cuộc thi và thành viên thành các tab riêng.
  Học viên có tab lịch sử nộp trong lớp; admin có công cụ gán bài, tạo contest,
  duyệt yêu cầu và mời thành viên/quản trị viên theo quyền được cấp.
- Thành viên đã tham gia lớp xem được contest đang diễn ra và contest đã kết
  thúc, gồm đề, bảng xếp hạng và lịch sử bài nộp; contest chưa bắt đầu chỉ quản
  trị viên lớp mới xem được. Chỉ thành viên lớp mới nộp được trong thời gian thi.
- Mã nguồn bài nộp chỉ người nộp và quản trị viên lớp có quyền xem trong contest
  tương ứng đọc được; mã nguồn không nằm trong dữ liệu lịch sử dạng danh sách.
- Lượt nộp public từ kho bài tập không gắn với lớp. Các lượt cũ không có thông
  tin lớp đáng tin cậy sẽ không được gán vào feed lớp một cách suy đoán.
- Quản trị viên được phân công quản lý lớp có thể xem contest ở mọi trạng thái
  và sửa tên, mô tả, thời gian, danh sách/thứ tự bài. Việc thay danh sách bài
  không xóa lịch sử nộp đã có.
- Trang contest có đề bài, vùng nộp (chỉ khi có quyền và đang trong thời gian
  thi), bảng xếp hạng và lịch sử contest phân trang.

## Điều hướng liên quan

Các trang tài khoản, kho bài tập và lớp học có liên kết đến trang lịch sử.
Trang làm bài có liên kết đến lịch sử riêng của bài đang mở; không đặt thêm
nút feed toàn server trong form nộp bài. Trang lịch sử luôn có feed toàn
server của riêng nó.
