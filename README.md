# OpenSwarm Independent — Triển khai Docker Compose ARM64

Repository triển khai bốn Docker image độc lập: **SN, SL, User-TF và SWOP**.
Chỉ cần clone **repo này** và dùng các image đã phát hành; không cần thư mục
`openswarm-arm64-rebuild`, mã nguồn ứng dụng bên ngoài hoặc build image trên board.
Không chứa mã OpenSwarm/HPE thu hồi chưa được cấp phép.

## Chọn cách triển khai

| Cách chạy | Tài liệu | Trạng thái |
|---|---|---|
| Một board chạy toàn bộ hệ thống | [single-host.md](docs/single-host.md) | Operator xác nhận native ARM64 PASS riêng trên RPi 5 và Orange Pi 5 Pro |
| Hai board cùng tham gia một job | [multi-host.md](docs/multi-host.md) | Cấu hình/tooling đã kiểm thử cục bộ; runtime giữa hai board chưa được xác minh |
| Cài Docker Engine/Compose trên Ubuntu ARM64 | [install-docker.md](docs/install-docker.md) | Dành cho máy mới; máy đã chạy job không cài lại |
| Dừng/gỡ single-host hoặc multi-host | [remove-deployment.md](docs/remove-deployment.md) | Kiểm ownership; xóa từng resource được chọn |

Một lần chạy một board vẫn có hai SL và hai client. Hai lần chạy riêng trên hai
board **không chứng minh** đã thực hiện một job multi-host chung.
Monitoring/Grafana chưa thuộc bộ triển khai này và vẫn có blocker phát hành riêng.
Chính sách rotation cho container mới, cách đọc logs có timestamp và exit code
preflight được mô tả trong hai hướng dẫn triển khai. Cập nhật repo không thay
logging của containers hiện tại; không recreate SN đang giữ state trong RAM.

## Bắt đầu với một board

Yêu cầu: Linux ARM64, RAM khoảng 8 GiB, Docker Engine local, Compose v2.24+ tương
thích, Git và Bash. Nên dự trù ít nhất 10 GiB đĩa trống; đây không phải số đo thực
tế trên thiết bị. Không cần cài TensorFlow, SWOP hoặc Python host cho single-host.

```bash
git clone https://github.com/TheAnh-05-UIT/openswarm.git
cd openswarm
cp -n .env.example .env
# Điền RUN_ID mới và EXCLUSIVE_OWNER_ACK sau khi đọc hướng dẫn single-host.
nano .env
bash scripts/check.sh
# Chỉ thực hiện khi đã xác nhận đúng cấu hình và cho phép chạy job mới:
bash scripts/start.sh
```

`start.sh` pull image và khởi chạy job mới. `check.sh` chỉ kiểm tra; nó không
chứng minh training PASS. Không dùng lại RUN_ID, restart để resume hoặc xóa volume
cũ để vượt qua lỗi. Các bước xác minh kết quả, dừng và xử lý sự cố nằm trong
[hướng dẫn một host](docs/single-host.md).

## Cấu trúc repo

```text
openswarm/
  README.md                       # Điểm bắt đầu
  compose.yaml                    # Triển khai một host
  .env.example                    # Cấu hình mẫu một host, không có secret
  config/
    initialize.py                 # Khởi tạo bundle/volume mới trong image SWOP
    verify.py                     # Kiểm tra journal, receipts và checkpoint
    verify_fedavg.py              # Replay số học độc lập sau khi clients hoàn tất
    profile.template.json         # Profile mẫu một host
    image-manifest.json           # Nhận dạng image theo lần kiểm tra đã lưu
    assets/initial.iutfw           # Bootstrap số học, không phải mã thực thi
  scripts/                        # check, start, logs, verify, stop một host
  multihost/
    README.md                     # Contract và cách dùng công cụ nhiều host
    compose.host-a.yaml            # SN + SL-A + Client-A + một controller
    compose.host-b.yaml            # SL-B + Client-B
    env.host-a.example            # Mẫu biến môi trường host A
    env.host-b.example            # Mẫu biến môi trường host B
    profile.template.json         # Một profile chung cho hai host
    tooling.py                    # validate/prepare/verify/import-host/verify-local
    operations.py                 # CLI thao tác đúng host/project/job
    preflight.py                  # Kiểm tra chỉ đọc, có timeout
    runtime_contract.py           # Đối chiếu bundle/import/env bất biến
    scripts/                      # Wrapper cho CLI nhiều host
    tests/                        # Kiểm thử bundle, cấu hình và isolation
  docs/                           # Hướng dẫn tiếng Việt theo chức năng
  tests/                          # Tests, kiểm tra repo/Compose và công cụ audit
  secrets/.gitkeep                # Chỗ dự phòng, hiện không dùng static secret
  LICENSE                         # Giấy phép gốc, giữ nguyên văn
  NOTICE.md                       # Nguồn gốc và phạm vi phân phối
```

Tên file dùng tiếng Anh, không dấu để dễ gọi trên Linux. Chú thích/hướng dẫn dùng
tiếng Việt; tên API, CLI flags, service, biến môi trường và khóa JSON giữ nguyên
để không làm thay đổi contract. `compose.yaml` dùng JSON hợp lệ trong YAML nên
không chèn comment trực tiếp; xem tài liệu và chú thích trong scripts/config.

## Image, dữ liệu và giới hạn

Các image mặc định đều có namespace `24520115`, tag `v0.1.0-arm64`.
[image-manifest.json](config/image-manifest.json) giữ riêng config ID, platform
manifest digest và tag/index digest từ bằng chứng đã lưu. Tag có thể thay đổi;
đây không phải kết quả truy vấn Registry mới hoặc cam kết image luôn tồn tại.

- SN/SL giữ coordination state trong RAM; checkpoint không phục hồi được epoch.
- API MVP chưa có production authentication/TLS; không expose ra Internet.
- User-TF có known issue TensorFlow I/O GCS duplicate `gs`; không tuyên bố remote
  GCS hoạt động. Workload dùng dữ liệu synthetic và checkpoint cục bộ.
- Hostnames/IP không được tự suy từ Docker DNS xuyên hai Docker Engines.
- Log, dữ liệu job, `.env`, khóa, cache và kết quả tests không đưa lên GitHub.
  Tests sẽ tạo thư mục `evidence/` bị ignore khi cần, không cần có log cũ để chạy.

## Phát triển và kiểm chứng

Các bước kiểm thử offline và lưu bằng chứng đã gộp vào
[single-host.md](docs/single-host.md) và [multi-host.md](docs/multi-host.md).
Thư mục docs chỉ giữ bốn hướng dẫn trong bảng đầu trang.
Test cục bộ/Compose config PASS không được coi là native runtime hoặc
multi-host training PASS. Repo không tự cài dependency, chỉnh Docker/OS, SSH,
deploy từ xa, cleanup toàn cục hay đẩy image.
