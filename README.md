# OpenSwarm Independent — Docker Compose

Repo **chỉ dùng để triển khai** các image độc lập của dự án. Không chứa source ứng dụng OpenSwarm/HPE cũ, không cần cài Python/TensorFlow/Grafana trên Pi và không build image trên thiết bị.

## 1. Yêu cầu

- Raspberry Pi 5 **8 GB**, Ubuntu Server ARM64; nguồn/tản nhiệt và dung lượng đĩa phù hợp. Dự trù ít nhất 10 GB đĩa trống cho image, unpack, logs và volumes; mức này là dự trù, chưa đo trên Pi.
- Docker Engine Linux ARM64 đang hoạt động, Compose plugin có hỗ trợ `depends_on: service_completed_successfully`, `service_healthy`, `config --quiet` và `run --pull never`. Dùng Compose v2.24+ hoặc bản mới tương đương. Trình kiểm tra trên Windows dùng Compose v5.5.1.
- Git, Bash, coreutils (`sha256sum`, `od`, `date`) và awk; tài khoản đã có quyền dùng Docker. Repo không tự cài phần mềm hoặc sửa quyền/daemon.
- Internet cho `git clone` và `docker compose pull`; workload dùng synthetic data, không tải dataset.

Kiểm tra trên **chính Pi**, dùng local Docker context:

```bash
uname -m                         # aarch64
docker context show
docker info --format '{{.OSType}}/{{.Architecture}}'  # linux/aarch64 hoặc linux/arm64
docker compose version
df -h .
free -h
```

Kết quả `uname` đơn lẻ không chứng minh đã chạy workload native. Không dùng Docker context trỏ về Windows/remote Engine hoặc QEMU để báo native PASS.

## 2. Clone và cấu hình

```bash
git clone https://github.com/TheAnh-05-UIT/openswarm.git
cd openswarm
cp .env.example .env
nano .env
```

Tạo một RUN_ID mới bằng Bash, rồi copy giá trị in ra vào `.env`:

```bash
printf '%s-%s\n' "$(date -u +%Y%m%dt%H%M%Sz)" "$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
```

Điền `RUN_ID=...` và `EXCLUSIVE_OWNER_ACK=true` **chỉ sau khi bảo đảm không có controller hay người vận hành khác mở rounds trong job này**. RUN_ID tạo tên project `openswarm-<RUN_ID>` và job mới. Không dùng lại RUN_ID cũ; không thay đổi cấu hình giữa một job.

Giữ bốn version mặc định `v0.1.0-arm64`, namespace `24520115`, nếu đang kiểm thử bộ đã được xác minh. Thay version/namespace đòi hỏi kiểm tra lại image và contract. Không dùng `latest` hoặc image SWOP cũ chưa có `orchestrate`/receipts. [Registry evidence](tests/evidence/registry-status.json) phân biệt index digest, platform manifest digest và config digest.

Mặc định: hai rounds, 16/32 samples, seeds 100/200, hai epochs, batch 16, learning rate 0.05. `SN_PORT=8000`, `SL_PORT=9000`, `CONTROLLER_PORT=9200` đều là **port nội bộ**, không phải port mở trên Pi. Hai SL ở hai network namespaces nên cùng port 9000 hợp lệ. Controller dùng chung network namespace với SN, vì vậy port controller phải khác SN.

`.env` chỉ nhận literal `KEY=VALUE`, không thêm `export`, dấu nháy, shell command hoặc inline comment. Scripts dùng parser riêng, không `source`/`eval` `.env`, và ưu tiên giá trị từ file. Các lệnh Compose trực tiếp chịu quy tắc ưu tiên biến môi trường của Compose: dùng shell sạch, không export biến trùng tên trong `.env`.

### Secrets

Bốn service hiện tại **không yêu cầu static secret file**. Không tạo password giả hoặc đưa token peer vào `.env`; token heartbeat do SN sinh trong memory và được SL giữ nội bộ. `secrets/.gitkeep` chỉ giữ chỗ, không được mount hoặc sử dụng. Kiểm thử “thiếu secret bắt buộc” là **N/A**; thiếu RUN_ID/owner acknowledgement bị từ chối. Nếu sau này thêm xác thực/Monitoring, phải cập nhật contract và validation secret tương ứng, không tự suy từ repo này.

## 3. Kiểm tra, pull và khởi chạy job mới

Ưu tiên scripts có kiểm tra context, cấu hình và tránh dùng lại resources cũ:

```bash
bash scripts/check.sh
bash scripts/start.sh
```

`start.sh` từ chối project có containers (kể cả đã dừng), network hoặc volume cũ; không tự dọn dữ liệu. Khi tạo project mới, nó pull image rồi `up --no-build --pull never --no-recreate`. Không `docker.sock`, không privileged, không host mounts nhạy cảm.

Workflow Compose trực tiếp tương đương cho **project hoàn toàn mới**, sau khi `check.sh` PASS và tự kiểm tra không trùng tên:

```bash
unset COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES COMPOSE_REMOVE_ORPHANS COMPOSE_ENV_FILES
docker compose config -q
docker compose pull
docker compose up -d
docker compose ps --all
```

Không chạy thêm controller bằng `docker compose run round-controller`, không scale controller, không mở rounds thủ công. Không chạy lại `up`/`restart` để cố resume job đang lỗi. Compose config PASS chỉ chứng minh cấu hình parse được, không chứng minh training, permissions hoặc RAM trên Pi.

## 4. Topology và startup

| Container/service | Image | Lệnh/workload | Trạng thái mong đợi |
| --- | --- | --- | --- |
| bundle-init | independent-swop | Sinh bundle/config và chuẩn bị volumes mới | Exited (0) |
| sn | independent-sn | `python -m sn_mvp --host 0.0.0.0 ...` | Running / healthy |
| sl-a, sl-b | independent-sl | SWOP-generated `sl_entry.py`, ML listener/heartbeat/FedAvg | Running / healthy |
| client-a, client-b | independent-user-tf | SWOP-generated `user_entry.py`, training + post-SDK receipts | Exited (0) sau hai rounds |
| round-controller | independent-swop | `python -m swop orchestrate ... --exclusive-owner` | Exited (0) sau hai rounds và grace 30 giây |

`bundle-init` dùng chính generator/launchers trong SWOP image; Pi không cần application source hay Python host. Repo chỉ mang deployment glue, profile và numeric bootstrap 1.1 KiB có checksum. Shared volume chỉ chứa cấu hình/bootstrap/launchers; model updates và global weights đi **qua HTTP/TCP giữa containers**, không trao đổi bằng checkpoint volume.

SN chờ init thành công. SL chờ SN healthy. Clients và controller chờ SL healthy. Controller còn kiểm tra live peer roster, cùng bootstrap/model/job/epoch qua API trước khi tạo round. Clients có thể đang import TensorFlow; controller chờ model updates/receipts theo timeout, không dùng sleep làm bằng chứng readiness.

Một private bridge network riêng/project cung cấp DNS `sn.openswarm.internal`, `sl-a.openswarm.internal`, `sl-b.openswarm.internal`. Controller chia sẻ namespace SN để callback `sn.openswarm.internal:9200` đến đúng listener. Profile có ba **logical DNS targets**, đều trên **cùng một Docker Engine**; các địa chỉ documentation `192.0.2.x` trong metadata generator không được publish hoặc sử dụng để routing ở Compose này. Chỉ `compose.yaml` root là deployment single-Pi; không chạy các per-target Compose phụ mà generator lưu trong volume. Không áp dụng bộ này xuyên host/Orange Pi mà chưa thiết kế và duyệt riêng.

Không có `ports:`/ingress host. Không expose API thiếu TLS/auth ra Internet. Private network không thay thế authentication; chỉ dùng lab tin cậy. API SN/SL chưa có `/metrics`; repo không tạo training metrics giả.

## 5. Theo dõi, health và hai rounds

```bash
docker compose ps --all
docker compose logs --tail 100 bundle-init
bash scripts/logs.sh
```

Kỳ vọng logs: `BUNDLE_READY`, `SL_READY`, `CLIENT_READY`; mỗi client có `ROUND_COMPLETED` cho rounds 1 và 2, sau đó `CLIENT_COMPLETION_REPORTED`. Ctrl+C chỉ thoát xem logs. Xác minh health/API trong containers đang chạy:

```bash
docker compose exec sn python -c "import os,urllib.request; print(urllib.request.urlopen('http://127.0.0.1:'+os.environ['HTTP_PORT']+'/health').read().decode())"
docker compose exec sn python -c "import os,urllib.request; print(urllib.request.urlopen('http://127.0.0.1:'+os.environ['HTTP_PORT']+'/v1/rounds/current').read().decode())"
docker compose exec sl-a python -c "import os,urllib.request; print(urllib.request.urlopen('http://127.0.0.1:'+os.environ['HTTP_PORT']+'/v1/ml/session').status)"
docker compose exec sl-b python -c "import os,urllib.request; print(urllib.request.urlopen('http://127.0.0.1:'+os.environ['HTTP_PORT']+'/v1/ml/session').status)"
docker compose logs --tail 100 round-controller client-a client-b
```

Controller journal chỉ ghi `ROUND_COMPLETED` khi tất cả SL commit cùng global, SN synchronized **và** nhận đủ hai client completion receipts sau SDK return. Round 2 chỉ mở sau mốc đó. Ambiguous/lost successful POST tạo round sẽ fail closed, không tự POST lại/adopt round. Chỉ GET/read retries và receipt replay idempotent được retry trong giới hạn đã định. Exclusive acknowledgement không phải server-side distributed lock.

Sau khi hai clients/controller đều **Exited (0)**, kiểm tra evidence tự động bằng cùng các image, không Python host:

```bash
bash scripts/verify.sh
bash scripts/verify-fedavg.sh
```

- `verify.sh`: đọc journal/4 receipts, thứ tự hai rounds, cùng model/epoch/samples, global apply, carry-over round 1→2; kiểm checksum/tensors checkpoints và SN/SL commit hiện tại. Chỉ GET API, không mở round/upload. Output phải có `status: PASS`.
- `verify-fedavg.sh`: chỉ cho chạy sau jobs exit 0; replay local training nhỏ từ bootstrap, seeds và global checkpoint round trước, tính độc lập `sum(samples_i * weights_i) / sum(samples_i)` bằng NumPy float64 rồi cast float32; so sánh **cả hai global checkpoints trong cả hai rounds**. Tolerance `1e-6`, in `max_abs_fedavg_error` từng round/client, không tự ghi PASS nếu sai. Replay không gửi model và không thay đổi checkpoints. Kết quả này cùng journal xác minh số học và flow; receipt/hash giống nhau đơn lẻ chưa chứng minh số học FedAvg đúng.

Round 1 aggregator `sl-a`, round 2 `sl-b`: một aggregator deterministic được chọn theo round, không tuyên bố decentralized/Byzantine-resilient aggregation.

## 6. RAM, persistence và dừng/khởi động lại

Mức cap: SN 512 MiB / 1 CPU; mỗi SL 512 MiB / 1 CPU; mỗi User-TF **2 GiB / 2 CPUs**, thread TensorFlow/BLAS = 1; controller 128 MiB / 0.5 CPU. Tổng active caps **5760 MiB (5.625 GiB)**, chừa danh nghĩa 2.375 GiB trong 8 GiB cho OS/Engine/cache. Init 128 MiB kết thúc trước ứng dụng. CPU quotas có thể oversubscribe bốn cores, không phải reservations hoặc throughput đảm bảo. Chưa đo peak RSS/native; nếu OOM, lưu evidence và điều chỉnh có duyệt, không tự suy RAM đủ chỉ từ caps.

```bash
docker compose stats --no-stream
bash scripts/stop.sh
```

Volumes project-scoped: `deployment` (config/bootstrap/generated launchers), `checkpoint-a`, `checkpoint-b` (mỗi client có `round-1.iutfw`, `round-2.iutfw`), `controller-data` (fresh `rounds.jsonl`). Dữ liệu/containers/network được giữ lại; script stop chỉ dừng exact container IDs có đủ project/owner/run labels. Không down/prune/delete. Docker log rotation theo daemon hiện có; repo không sửa daemon.

**Không resume bằng restart.** SN/SL state chỉ memory; mất process sẽ đổi/làm mất epoch/token/round state. Journal cũ bị từ chối ngay cả job đã hoàn thành; checkpoints không phục hồi coordination state. Khi cần job mới: dừng job cũ bằng script, lưu logs/evidence, sửa `.env` sang **RUN_ID mới**, chạy check/start. Không xóa volume/journal để bypass fencing; không dùng lại resources cũ.

## 7. Troubleshooting

| Hiện tượng | Kiểm tra/xử lý |
| --- | --- |
| Thiếu RUN_ID hoặc ACK | Điền `.env`, xác nhận đúng một round writer; không bỏ validation |
| `pull access denied` / tag missing | Kiểm tra namespace/version/network hoặc quyền Docker Hub; Registry snapshot có thời điểm, tag có thể đổi |
| `No module named swop` / thiếu orchestrate | Dùng đúng SWOP v0.1.0-arm64 có config digest trong evidence; không dùng image 005B |
| Init lỗi volume không rỗng | Giữ evidence, dùng RUN_ID mới; không chown/reuse/xóa dữ liệu cũ |
| Init permission/capability error | Ghi log/rootless/userns/storage settings thực tế; chưa có native permission PASS, không tăng privileged |
| Clients/controller chờ hoặc timeout | Kiểm SN/SL healthy, DNS/ports nội bộ, epochs, launchers, đủ hai peers/clients; không mở round thủ công |
| `CREATE_UNCERTAIN_NO_RETRY` | Dừng/chẩn đoán job, giữ journal; không retry create/reupload, không tự resume |
| Client/controller Exited (0) | Bình thường với finite job, kiểm đủ hai rounds/receipts; exit khác 0 là lỗi |
| TensorFlow duplicate `gs` registration | Known issue TensorFlow I/O GCS; local synthetic/file checkpoints là phạm vi test; remote GCS chưa được xác minh |
| OOMKilled / numeric replay khác | Ghi `ps`, logs, stats, kernel/runtime/version; numeric mismatch cần điều tra, không đổi tolerance để tạo PASS |
| Verifier name đã tồn tại | Giữ evidence cũ, không tự xóa; xin duyệt thao tác riêng hoặc dùng job mới |
