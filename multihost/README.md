# Triển khai một job trên hai host

Thư mục này chứa cấu hình và công cụ cho hai Docker Engines Linux ARM64 độc lập.
Đọc [multi-host.md](../docs/multi-host.md) trước khi chạy; kiểm tra host/mạng theo
[phần preflight trong cùng hướng dẫn](../docs/multi-host.md#6-preflight-chỉ-đọc-và-kiểm-tra-mạng).
Các lệnh không cần thư mục ứng dụng ngoài repo.
Chưa có bằng chứng một job training chung chạy trên hai board vật lý.

## Vai trò và nhận dạng

| Contract host ID | Vai trò | Hostname có thể khác ID |
|---|---|---|
| `rpi5` | SN, SL-A, Client-A, duy nhất một controller | Ví dụ `rpi5` |
| `opi5-pro` | SL-B, Client-B | Ví dụ lab dùng `oran5` |

Mọi host dùng cùng RUN_ID, bundle ID, job/model/bootstrap và cùng SN epoch sống.
RUN_ID có dạng `YYYYMMDDtHHMMSSz-xxxxxxxx`; `job_id=mh-<RUN_ID>`.
Profile/env mẫu cố ý để trống các thông số thực, tránh chạy nhầm job.
Hai địa chỉ phải là IPv4 RFC1918 khác nhau; `address` khớp `app_bind_ip`.
Không chấp nhận public IP, loopback, địa chỉ documentation, DNS hoặc CGNAT trong
contract hiện tại. Không thay địa chỉ giả để bỏ qua validator của SWOP.

Port mặc định: A:18000→8000 cho SN, A:19001→9000 cho SL-A,
B:19002→9000 cho SL-B, A:19200→9200 cho callback controller.
Các dịch vụ dùng endpoint private đã cấu hình, không dùng Docker DNS xuyên host.
Không cần Swarm, overlay, host networking, shared volume hoặc VPN bắt buộc.

## Các công cụ

| File | Chức năng |
|---|---|
| `tooling.py` | validate profile; prepare/verify một bundle; import-host; verify-local |
| `runtime_contract.py` | Đọc import đã ghim ID, kiểm tra file bất biến, host/env/path |
| `preflight.py` | Chỉ đọc host/Engine, route/interface/port/ownership và GET endpoint tùy chọn |
| `operations.py` | configure, check, preflight, plan, start, status, logs, stop |
| `scripts/*.sh` | Gọi đúng subcommand, yêu cầu chọn host tường minh |
| `tests/run_offline.py` | Kiểm thử trên bản sao, ghi output cục bộ bị Git bỏ qua |

`tooling.py` cần SWOP có sẵn khi dùng schema/generator; có thể chạy ngay trong
image SWOP đã phát hành. Công cụ vận hành trên host chỉ cần Python 3.10+ thư viện
chuẩn và Bash; không cần cài SWOP/NumPy/TensorFlow lên board.

## Bundle, storage và cách ly

Chỉ nhận bundle dạng thư mục, không giải nén ZIP/TAR. Bundle ID là SHA-256 của
manifest JSON chuẩn hóa, **không phải image digest hay chữ ký**. ID mong đợi phải
được chuyển qua kênh tin cậy độc lập với bundle. Manifest kiểm tra đầy đủ file,
hash, kích thước, host placement và output từ generator SWOP thực tế.

Import chỉ tạo đích mới; từ chối đích đã tồn tại dù rỗng, link, hardlink và file
đặc biệt. Phần output dở dang được giữ lại để điều tra, không tự xóa hoặc ghi đè.
Config/runtime/profile/bootstrap mount read-only. Checkpoint riêng mỗi host;
journal chỉ trên A. UID:GID runtime là65532:65532. `--defer-ownership` chỉ dành
staging offline, không được coi là import sẵn sàng chạy trên Linux.

`verify-local` chỉ dành cho import mới chưa có dữ liệu runtime. Sau training,
dùng status/logs và quy trình thu bằng chứng; không xóa checkpoint để ép verify
lại. Project mới có tên `osm009-<RUN_ID>-<host>`, network `<project>_lab`, labels
riêng; không nhắm vào project một host `openswarm-<RUN_ID>`.

## Các giới hạn phải đọc

- CLI preflight trả 0 khi các gate bắt buộc PASS, 2 khi có gate FAIL, 3 nếu
  thiếu gate hoặc UNKNOWN/NOT TESTED. Đọc `readiness` và từng observation;
  exit0 không cấp phép deploy. Không nối `preflight && start` thay phê duyệt.
- Logs thêm timestamps và nhận `--since` UTC tuyệt đối, có metadata byte budget/
  truncation; xem [multi-host.md](../docs/multi-host.md). Policy rotation chỉ dành
  container mới, không được recreate SN đang giữ epoch để áp dụng ngay.
- Preflight cần import hợp lệ; board chưa import dùng checklist read-only trước.
- GET từ host không chứng minh đường mạng container/hairpin hoặc transfer model.
- Controller mở listener rồi có thể mở round ngay; không có listener-only mode.
  Chạy controller để thử callback cũng cần phê duyệt training.
- Cờ acknowledgement chỉ là xác nhận operator, không phải đo đạc hay distributed
  lock. Không có API remote-client-ready; phải đọc log cả hai client.
- State SN/SL chỉ trong RAM; mất epoch không tự resume. Không retry mutation
  không rõ thành công, giảm số peer hoặc dùng checkpoint để giả lập commit.
