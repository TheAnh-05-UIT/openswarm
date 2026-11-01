# OpenSwarm Independent — ARM64 Docker Deployment

Triển khai **OpenSwarm Independent** trên **Raspberry Pi 5** hoặc **Orange Pi 5 Pro** bằng Docker Compose, sử dụng các Docker image đã được build sẵn trên Docker Hub.

Repository này dành cho người muốn **clone, cấu hình và chạy thử một phiên huấn luyện FedAvg gồm 2 rounds** mà không cần tự build image hoặc cài Python/TensorFlow trên máy host.

> **Phạm vi hiện tại:** triển khai **single-host** — SN, hai SL, hai training clients và round controller cùng chạy trên **một** Raspberry Pi hoặc Orange Pi. Chạy repository trên hai thiết bị khác nhau sẽ tạo **hai phiên độc lập**, **không** tự động trở thành multi-host Swarm Learning. Đây là bản triển khai độc lập, không phải bản phân phối chính thức của HPE Swarm Learning.

## 1. Thành phần hệ thống

| Service | Vai trò | Trạng thái mong đợi |
| --- | --- | --- |
| `bundle-init` | Tạo deployment bundle và chuẩn bị dữ liệu ban đầu | `Exited (0)` |
| `sn` | Điều phối phiên huấn luyện và trạng thái các rounds | `Up (healthy)` |
| `sl-a`, `sl-b` | Hai learning nodes, trao đổi và tổng hợp tham số mô hình | `Up (healthy)` |
| `client-a`, `client-b` | Huấn luyện TensorFlow trên dữ liệu mẫu cục bộ | `Exited (0)` khi hoàn thành |
| `round-controller` | Tự động điều phối 2 rounds, nhận completion receipts | `Exited (0)` khi hoàn thành |

Cấu hình mặc định: **2 rounds**, Client-A **16 samples**, Client-B **32 samples**, mỗi client huấn luyện **2 epochs/round**. Bộ dữ liệu được tạo tổng hợp (synthetic), không cần tải dataset từ bên ngoài.

### Docker images

| Image trên Docker Hub | Tag | Platform |
| --- | --- | --- |
| [`24520115/independent-sn`](https://hub.docker.com/r/24520115/independent-sn) | `v0.1.0-arm64` | `linux/arm64` |
| [`24520115/independent-sl`](https://hub.docker.com/r/24520115/independent-sl) | `v0.1.0-arm64` | `linux/arm64` |
| [`24520115/independent-user-tf`](https://hub.docker.com/r/24520115/independent-user-tf) | `v0.1.0-arm64` | `linux/arm64` |
| [`24520115/independent-swop`](https://hub.docker.com/r/24520115/independent-swop) | `v0.1.0-arm64` | `linux/arm64` |

Đây là các tag đã được kiểm thử trên Raspberry Pi 5 và Orange Pi 5 Pro. Thông tin nhận dạng image tại thời điểm kiểm tra được lưu trong [`tests/evidence/registry-status.json`](tests/evidence/registry-status.json). Để tái lập chính xác bằng chứng cũ, đối chiếu image digest; không coi tag là bất biến.

## 2. Yêu cầu trước khi triển khai

- **Thiết bị:** Raspberry Pi 5 hoặc Orange Pi 5 Pro; hệ điều hành Linux ARM64 (đã kiểm thử với Ubuntu Server ARM64).
- **Docker:** Docker Engine dùng local Linux ARM64, Docker Compose plugin tương thích (khuyến nghị Compose **v2.24+** hoặc mới hơn có đầy đủ các tính năng mà Compose file sử dụng).
- **Công cụ:** Git, Bash, `sha256sum`, `date`, `od`, `awk` và các tiện ích Linux thông dụng.
- **Tài nguyên:** khuyến nghị RAM từ **8 GiB**, đủ dung lượng lưu trữ trống (nên dự trù **ít nhất 10 GiB trước khi tải images**), nguồn điện và tản nhiệt ổn định.
- **Kết nối Internet:** để clone GitHub và pull Docker images.

Nếu chưa cài Docker, làm theo [hướng dẫn cài Docker Engine trên Ubuntu](https://docs.docker.com/engine/install/ubuntu/) và [Docker Compose plugin](https://docs.docker.com/compose/install/linux/). Không cần cài TensorFlow hay Python lên host.

Kiểm tra trực tiếp trên Pi:

```bash
uname -m
# Mong đợi: aarch64

docker context show
docker info --format '{{.OSType}}/{{.Architecture}}'
# Mong đợi: linux/aarch64 hoặc linux/arm64

docker compose version
git --version
free -h
df -h /
```

> **Lưu ý:** Docker phải truy cập được bằng tài khoản hiện tại. Không sử dụng Docker context trỏ tới Windows hoặc máy khác để chứng minh chạy native ARM64 trên Pi.

## 3. Quick Start — Clone và chạy OpenSwarm

Các lệnh dưới đây chạy trên **terminal của Raspberry Pi 5 hoặc Orange Pi 5 Pro**,

### Bước 1 — Clone repository

```bash
git clone https://github.com/TheAnh-05-UIT/openswarm.git
cd openswarm
```

Nếu đã clone từ trước, vào thư mục `~/openswarm`, kiểm tra thay đổi cục bộ bằng `git status`, sau đó mới cập nhật bằng `git pull --ff-only origin main` nếu working tree phù hợp.

### Bước 2 — Tạo file cấu hình `.env`

```bash
cp .env.example .env
```

Tạo một `RUN_ID` mới và tự điền vào `.env`:

```bash
RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-$(od -An -N4 -tx1 /dev/urandom | tr -d '[:space:]')"
sed -i "s/^RUN_ID=.*/RUN_ID=$RUN_ID/" .env
printf 'RUN_ID=%s\n' "$RUN_ID"
```

Chỉ khi chắc chắn **có đúng một controller/round writer** quản lý job này, xác nhận quyền điều phối:

```bash
sed -i 's/^EXCLUSIVE_OWNER_ACK=.*/EXCLUSIVE_OWNER_ACK=true/' .env
```

Kiểm tra các giá trị chính:

```bash
grep -E '^(IMAGE_NAMESPACE|SN_VERSION|SL_VERSION|USER_TF_VERSION|SWOP_VERSION|RUN_ID|EXCLUSIVE_OWNER_ACK|ROUNDS)=' .env
```

Giữ nguyên các cấu hình mặc định khác khi chạy lần đầu. Ví dụ:

```dotenv
IMAGE_NAMESPACE=24520115
SN_VERSION=v0.1.0-arm64
SL_VERSION=v0.1.0-arm64
USER_TF_VERSION=v0.1.0-arm64
SWOP_VERSION=v0.1.0-arm64
ROUNDS=2
SAMPLES_A=16
SAMPLES_B=32
EPOCHS=2
BATCH_SIZE=16
LEARNING_RATE=0.05
```

**Không dùng lại `RUN_ID` đã chạy.** Mỗi RUN_ID tạo một Docker Compose project và các volumes riêng. File `.env` chỉ dùng các giá trị literal `KEY=VALUE`; không thêm `export`, dấu nháy hoặc inline comments. Không commit `.env` lên GitHub.

### Bước 3 — Kiểm tra cấu hình trước khi chạy

```bash
bash scripts/check.sh
```

Kết quả thành công có dạng:

```text
Static configuration OK: openswarm-<RUN_ID>. No containers started; native runtime is NOT VERIFIED.
```

Lệnh này chỉ kiểm tra cấu hình và Docker Engine; chưa khởi chạy workload.

### Bước 4 — Pull images và khởi chạy

```bash
bash scripts/start.sh
```

Script sẽ **tự pull 4 images**, tạo network/volumes và khởi động các services. **Không cần chạy thêm `docker compose pull` hay `docker compose up -d`** sau lệnh này.

Kiểm tra trạng thái:

```bash
docker compose ps -a
```

Ban đầu các clients và controller có thể đang `Up`; sau khi hoàn thành, chúng sẽ chuyển thành `Exited (0)`.

### Bước 5 — Theo dõi training

```bash
docker compose logs --tail=100 round-controller
```

Hoặc xem logs các services trong thời gian thực:

```bash
bash scripts/logs.sh
```

Nhấn `Ctrl+C` để thoát chế độ theo dõi logs; thao tác này không dừng containers.

Các tín hiệu thành công cần tìm trong logs gồm:

- `BUNDLE_READY`: tạo deployment bundle thành công.
- `CLIENT_READY`: client kết nối và sẵn sàng huấn luyện.
- `ROUND_COMPLETED`: mỗi client hoàn thành Round 1 và Round 2.
- `CLIENT_COMPLETION_REPORTED`: client gửi completion receipt.
- Controller báo `"status": "PASS"` và liệt kê đủ hai rounds.

### Bước 6 — Xác minh kết quả

Sau khi `client-a`, `client-b`, `round-controller` đều **Exited (0)**, chạy **mỗi verifier một lần cho một RUN_ID**:

```bash
bash scripts/verify.sh
bash scripts/verify-fedavg.sh
```

`verify.sh` kiểm tra journal, **4 completion receipts**, hai global checkpoints và khả năng carry-over mô hình từ Round 1 sang Round 2.

`verify-fedavg.sh` kiểm tra lại phép tổng hợp tham số bằng phép tính độc lập cho cả hai rounds, với sai số cho phép `1e-6`.

**Kết quả mong đợi:** mỗi verifier in `"status": "PASS"`; các giá trị `max_abs_fedavg_error` phải nằm trong ngưỡng cho phép.

> Các verifier tạo container kiểm tra lưu lại làm bằng chứng. Không chạy lặp lại cùng lệnh với cùng RUN_ID nếu container kiểm thử đã tồn tại. Cảnh báo `orphan containers` có thể xuất hiện khi chạy verifier thứ hai; không dùng `--remove-orphans` để xóa dữ liệu kiểm thử.

### Bước 7 — Dừng services (tùy chọn)

Nếu muốn dừng các services còn chạy sau khi đã lưu kết quả:

```bash
bash scripts/stop.sh
```

Lệnh này chỉ dừng containers thuộc job hiện tại; **không xóa images, containers, networks hoặc volumes**. Không dùng `docker compose down -v`, `docker volume prune` hoặc các lệnh xóa dữ liệu khi cần giữ bằng chứng.

## 4. Cấu trúc repository

```text
openswarm/
├── compose.yaml                  # Docker Compose single-host ARM64
├── .env.example                  # Mẫu biến môi trường
├── README.md                     # Hướng dẫn triển khai
├── LICENSE
├── NOTICE.md
├── config/
│   ├── initialize.py             # Sinh bundle và chuẩn bị volumes
│   ├── profile.template.json     # Profile hai rounds
│   ├── verify.py                 # Kiểm tra workflow và checkpoints
│   ├── verify_fedavg.py          # Kiểm tra số học FedAvg
│   └── assets/initial.iutfw      # Bootstrap model đã có checksum
├── scripts/
│   ├── check.sh                  # Kiểm tra trước triển khai
│   ├── start.sh                  # Pull và khởi chạy job mới
│   ├── logs.sh                   # Xem logs
│   ├── verify.sh                 # Kiểm tra kết quả training
│   ├── verify-fedavg.sh          # Replay số học FedAvg
│   └── stop.sh                   # Dừng containers, giữ dữ liệu
├── docs/                         # Tài liệu kỹ thuật và nghiệm thu
├── tests/                        # Kiểm thử và evidence
└── secrets/                      # Thư mục dự phòng, chưa dùng static secret
```

Source ứng dụng không cần có trên Pi; các runtime liên quan được đóng gói trong Docker images. File `compose.yaml` hiện dùng cú pháp JSON hợp lệ trong YAML, có thể hiển thị khác YAML block style nhưng vẫn được Docker Compose hỗ trợ.

## 5. Xử lý sự cố thường gặp

| Hiện tượng | Cách kiểm tra |
| --- | --- |
| `docker: command not found` | Cài Docker Engine và Compose plugin theo hướng dẫn chính thức. |
| `permission denied` khi chạy `docker` | Kiểm tra quyền truy cập Docker socket/tài khoản; không tăng đặc quyền container chỉ để vượt lỗi. |
| `no matching manifest for linux/arm64` | Kiểm tra namespace/tag của image; chỉ dùng các tag ARM64 nêu trên. |
| `ModuleNotFoundError: No module named 'swop'` | Cập nhật repository; `compose.yaml` cần `PYTHONPATH=/app` cho `bundle-init` và `verify`. Kiểm tra image SWOP đúng phiên bản. |
| `RUN_ID` hoặc `EXCLUSIVE_OWNER_ACK` chưa hợp lệ | Kiểm tra `.env` và xác nhận không có controller thứ hai cho job. |
| `Volume not empty`, lỗi init khi chạy lại | Không khởi chạy lại job cũ; giữ logs/evidence và dùng RUN_ID mới cho job mới. |
| Client hoặc controller `Exited (0)` | Bình thường sau khi job hoàn thành; chạy các bước verify. |
| `Exited (1)`, OOM hoặc timeout | Xem `docker compose ps -a` và `docker compose logs --tail=100 <service>`; giữ dữ liệu để điều tra. |
| Cảnh báo `orphan containers` khi verify | Container kiểm thử được lưu lại; không dùng `--remove-orphans` tự động. |

**Quan trọng:** workload này không hỗ trợ khôi phục một training job cũ chỉ bằng cách restart các container, vì một phần trạng thái điều phối nằm trong bộ nhớ SN/SL. Nếu cần một lượt chạy mới, giữ nguyên kết quả cũ, tạo `RUN_ID` mới và chạy lại quy trình từ bước cấu hình.

## 6. Phạm vi và kết quả đã kiểm thử

- **Raspberry Pi 5:** chạy độc lập trên Ubuntu ARM64, hoàn thành 2 rounds FedAvg và kiểm tra số học thành công.
- **Orange Pi 5 Pro:** chạy độc lập trên Ubuntu ARM64, hoàn thành 2 rounds FedAvg và kiểm tra số học thành công.
- **Chưa bao gồm:** một phiên training chung giữa nhiều thiết bị vật lý, monitoring dashboard hoặc deployment production có TLS/xác thực API.

Docker Compose sử dụng network nội bộ cho lab; không publish các API điều phối/training ra Internet.

Thông tin chi tiết cho người phát triển nằm trong [`docs/`](docs/) và kết quả kiểm tra image trong [`tests/evidence/`](tests/evidence/).

---

**Tóm tắt lệnh chạy lần đầu (sau khi đã cài Docker, Git và đáp ứng yêu cầu ARM64):**

```bash
git clone https://github.com/TheAnh-05-UIT/openswarm.git
cd openswarm
cp .env.example .env
RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-$(od -An -N4 -tx1 /dev/urandom | tr -d '[:space:]')"
sed -i "s/^RUN_ID=.*/RUN_ID=$RUN_ID/" .env
# Chỉ bật acknowledgement nếu chắc chắn có một round writer:
sed -i 's/^EXCLUSIVE_OWNER_ACK=.*/EXCLUSIVE_OWNER_ACK=true/' .env
bash scripts/check.sh
bash scripts/start.sh
# Sau khi client-a, client-b và controller đều Exited (0):
bash scripts/verify.sh
bash scripts/verify-fedavg.sh
```
