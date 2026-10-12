# Dola Render Gateway — website dùng chung 24/7

Mã nguồn trong nhánh này chuẩn bị bản triển khai trên VPS Linux x86_64. Website chỉ live sau khi có máy chủ,
domain, DNS và đăng nhập Dola thành công. GitHub lưu mã nguồn; VPS chạy trình duyệt và xử lý các tác vụ.

## Giao diện và quyền truy cập

| Đường dẫn | Người dùng | Chức năng |
|---|---|---|
| `/studio` | Nhân viên có API Key riêng | Gửi prompt và link ảnh tham chiếu, theo dõi tác vụ, tải video |
| `/` | Admin có mật khẩu quản trị | Quản lý tài khoản Dola, tác vụ, API Key và hạn mức |
| `/ready` | Kiểm tra máy chủ | Kiểm tra tiến trình/API/SQLite; không xác nhận tài khoản Dola hoạt động |
| `/health` | Admin | Kiểm tra chi tiết tài khoản và hàng đợi |

Key riêng dùng logic phân quyền tác vụ sẵn có trong repo. Đây chưa phải hệ thống tài khoản nhân viên có vai trò/SSO.
Admin nhìn thấy tất cả tác vụ; nhân viên chỉ lấy được thông tin tác vụ thuộc key của mình. Link tải video có chữ ký,
hết hạn sau một giờ; ai có link hợp lệ có thể xem video trong thời gian đó. Để lấy link mới, kiểm tra lại tác vụ.

## 1. Chuẩn bị máy chủ

- VPS Linux x86_64 với Docker Engine và Docker Compose v2 đã cài đặt.
- Điểm bắt đầu để thử nghiệm: 4 vCPU, 8 GB RAM, 40 GB đĩa, chạy một tác vụ tại một thời điểm.
  Đây là cấu hình khởi đầu cần đo tải thực tế, chưa phải kết quả benchmark. Đĩa phải tăng theo số lượng video lưu.
- Chọn subdomain, ví dụ `video.ten-cong-ty.com`, trỏ bản ghi A về IPv4 của VPS. Chỉ tạo AAAA khi IPv6 hoạt động.
- Mở cổng 80/443 cho website và cổng SSH theo cấu hình máy chủ.
- Kết nối Dola phải hoạt động từ máy chủ và phù hợp với quyền truy cập tài khoản. Nếu mạng cần proxy,
  cấu hình proxy mà container truy cập được. `127.0.0.1:7890` trong Docker là bên trong container, không phải máy VPS.
  Ảnh tham chiếu được tải trực tiếp, với kiểm tra IP ở lúc kết nối; không chuyển hostname của nhân viên qua proxy.

Không thay cấu hình các ứng dụng khác trên VPS. Bộ Compose này dùng cổng 80/443; nếu VPS đã có web server,
quản trị viên phải gắn ứng dụng vào reverse proxy đang dùng hoặc triển khai trên VPS riêng.

## 2. Lấy mã nguồn và cấu hình

Sau khi đăng nhập SSH vào VPS:

```bash
git clone https://github.com/cuongni1989-coder/dola-render-gateway-tram-share.git
cd dola-render-gateway-tram-share
# Trước khi PR được merge, dùng nhánh chứa bản triển khai:
git switch codex/team-vps-20261012
python3 deploy/configure.py video.ten-cong-ty.com
```

Thay domain mẫu bằng domain của bạn. Nếu cần proxy, dùng `--proxy 'http://proxy-host:port'`.
Script tạo `.env.production` với quyền chỉ chủ sở hữu đọc/ghi, tự tạo ba secret và từ chối ghi đè file có sẵn.
Đọc riêng `DOLA_ADMIN_KEY` (mật khẩu dashboard) và `DOLA_VNC_PASSWORD` (mật khẩu màn hình trình duyệt) trên VPS.
Không đưa `.env.production`, phiên đăng nhập, database hoặc video riêng lên GitHub hoặc chat.

Mặc định chỉ một tác vụ đồng thời, hàng đợi tối đa 20, extension 30 giây tắt. Trang Studio hiển thị thời lượng
10/15 giây. Model và khả năng thực tế vẫn phụ thuộc giao diện/quyền sử dụng của Dola; cần xác nhận bằng tác vụ thật.

## 3. Build và chạy

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=100 app caddy
```

Caddy tự cấp/gia hạn HTTPS khi DNS và cổng 80/443 đúng. Caddy đợi API đạt readiness trước khi khởi động.
Chính sách `unless-stopped` khởi động lại container khi tiến trình lỗi hoặc máy chủ khởi động lại, khi Docker được
cấu hình tự chạy. Nếu bạn chủ động stop, container giữ trạng thái stop. Healthcheck đánh dấu sức khỏe; nó không tự
khởi động lại một tiến trình vẫn sống nhưng bị treo. Lỗi riêng của tác vụ cần admin xử lý trên dashboard.

Mở `https://DOMAIN/` và đăng nhập bằng mật khẩu quản trị. Chưa có tài khoản Dola thì tạo video sẽ trả lỗi
`no account in pool`; không phải lỗi website.

## 4. Đăng nhập Dola lần đầu

Website production không nhận mật khẩu Google hoặc secret 2FA. Admin đăng nhập trực tiếp trong cửa sổ Chromium
riêng, qua kết nối SSH. Dola vẫn có thể yêu cầu đăng nhập lại/xác minh; admin phải tự hoàn thành yêu cầu đó.

Trên máy của admin, mở terminal và giữ kết nối này:

```bash
ssh -N -L 6080:127.0.0.1:6080 USER@IP_VPS
```

Mở `http://127.0.0.1:6080/vnc.html`, Connect và nhập mật khẩu màn hình trình duyệt. Đây là localhost được chuyển
tiếp qua SSH, không phải địa chỉ website công khai. Cổng 6080 trên VPS chỉ bind localhost.

Ở terminal SSH khác trên VPS:

```bash
docker compose exec app python login_account.py team01
```

Trong màn hình trình duyệt, tự đăng nhập Dola và hoàn tất 2FA/xác minh. Lệnh không nhận mật khẩu hay TOTP secret.
Sau thông báo lưu phiên thành công, trở về dashboard → Accounts → Refresh → Verify. Với tài khoản đã có,
tắt Scheduling và chờ tác vụ hiện tại kết thúc trước khi đăng nhập lại. Đăng nhập tài khoản mới dùng thư mục tạm
ẩn; scheduler chỉ thấy tài khoản sau khi đăng nhập thành công. Nếu thao tác gặp lỗi, đọc log để sửa; không tạo
nhiều lần đăng nhập cho cùng một tài khoản đồng thời.

Màn hình này cũng dùng để hoàn tất xác minh trong lúc tạo video. Tác vụ chờ tối đa 180 giây mặc định;
hết thời gian thì báo lỗi cần thao tác thủ công. Không có bước tự giải/vượt xác minh trong worker của nhánh này.

## 5. Cấp quyền cho nhân viên và kiểm tra tác vụ thật

1. Admin vào API Keys → Create API Key, đặt tên nhân viên, hạn mức/ngày và số tác vụ đồng thời.
2. Chuyển key riêng cho đúng nhân viên qua kênh riêng của công ty. Không chia sẻ mật khẩu admin cho cả đội.
3. Nhân viên mở `https://DOMAIN/studio`, nhập key, prompt, tỉ lệ, thời lượng và link ảnh nếu cần.
4. Tạo một tác vụ: kiểm tra trạng thái queued → processing → completed, phát video và tải file.
5. Thử key khác: không được đọc tác vụ của người đầu tiên. Kiểm tra đạt hạn mức sẽ báo 429.
6. Khởi động lại app: dashboard, tài khoản và video phải còn; kiểm tra tác vụ có conversation ID tiếp tục theo dõi.

Studio chỉ giữ key/mã tác vụ trong session của tab. Đây là giao diện cơ bản cho một tác vụ đang theo dõi;
chưa có thư viện nhân viên, upload ảnh từ máy, lịch sử nhiều tác vụ hay đăng nhập theo email.

## 6. Dữ liệu, backup và cập nhật

Compose có volume riêng cho `accounts`, `downloads`, `data`, và chứng chỉ HTTPS của Caddy.
Database tác vụ và metadata tài khoản đều ở volume `data`. Rebuild image hoặc recreate container không xóa các volume.
Chỉ chạy một replica app và một Uvicorn worker: khóa tài khoản/hàng đợi nằm trong bộ nhớ của tiến trình.

Trước khi backup, tạm dừng nhận tác vụ, chờ tác vụ hoàn tất rồi stop app. Tạo bản sao ba volume ứng dụng
và `.env.production` qua quy trình backup của VPS, lưu ở nơi riêng có kiểm soát quyền; sau đó start app lại.
Test restore trên một máy riêng. Không dùng `docker compose down -v` vì lệnh đó xóa volume.
Chưa có tự động dọn video hay backup lên nơi khác; admin cần theo dõi dung lượng và thiết lập backup của VPS.

Sau khi PR được merge:

```bash
git switch main
git pull --ff-only
docker compose up -d --build
docker compose ps
```

Trước mỗi cập nhật, kiểm tra diff và backup; kiểm tra `/ready`, đăng nhập admin, một tác vụ thật và video sau cập nhật.
Không thay `.env.production` khi cập nhật code. Không chạy lại configure để xoay secret ngoài kế hoạch.

## Kiểm tra đã thực hiện và phần cần xác nhận trên VPS

Test trong repo kiểm tra xác thực production, cách ly tác vụ theo key, hạn mức, link video có chữ ký,
khả năng mở lại dữ liệu SQLite và cấu hình từ chối thiếu secret. Luồng trình duyệt/API được kiểm tra với worker
giả lập, không dùng tài khoản hay tạo video Dola thật. Workflow CI kiểm tra build và khởi động Docker.

Chỉ coi triển khai hoàn tất sau khi Docker/HTTPS, SSH private browser và ít nhất một video Dola thật PASS trên VPS.
Không suy từ `/ready` thành kết luận tạo video thật đã hoạt động hoặc dịch vụ sẽ luôn sẵn sàng.
