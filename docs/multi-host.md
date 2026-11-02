# Triển khai một job chung trên hai host ARM64

Tài liệu cho bộ cấu hình `multihost/`. Chỉ thực hiện các lệnh tạo file/container
khi operator đã phê duyệt đúng thiết bị và pha thử nghiệm. Việc chuẩn hóa repo
không triển khai vật lý hoặc chạy training. Runtime multi-host vẫn chưa xác minh.
Chỉ cần repo này và bốn image đã phát hành; không cần source ứng dụng trên board.

## 1. Topology và điều kiện

| Host ID trong profile/CLI | Dịch vụ | Cổng private mặc định |
|---|---|---|
| `rpi5` — A | SN, SL-A, Client-A, một controller | 18000→8000; 19001→9000; 19200→9200 |
| `opi5-pro` — B | SL-B, Client-B | 19002→9000 |

Host ID không bắt buộc giống hostname. Lab có thể dùng hostname `oran5` cho B,
nhưng CLI vẫn chọn `--host opi5-pro`. Không tự thêm board thứ ba.

Hai host cần Linux ARM64, Docker local, Compose và IPv4 private định tuyến được.
Các wrapper host dùng Bash và Python 3.10+ thư viện chuẩn vốn thường có trên
Ubuntu; không yêu cầu cài package SWOP/TensorFlow trên host. SWOP chạy trong image.
Thiếu prerequisite thì dừng, không tự cài hoặc sửa daemon/OS.

Mỗi host có bridge/network và bind storage riêng. Docker DNS không hoạt động
xuyên hai Engines; mọi endpoint SN/SL/controller dùng private host IP trong
profile. Kiểm tra cả đường container→IP publish trên chính host của nó (hairpin).
Không mặc định cần VPN, Docker Swarm, overlay hay `network_mode: host`.

## 2. Chuẩn bị một profile/bundle chung

**Kế hoạch tạo file/container, chỉ chạy sau phê duyệt.** Một operator tạo một
RUN_ID mới, job ID tương ứng và một bundle; không sinh hai job riêng cho hai host.
`profile.template.json` dùng schema SWOP v2 thực tế, hai dataset seed/sample count
khác nhau và hai rounds. Giữ image refs đã kiểm tra, không tự đổi version.

Chọn thư mục staging **mới**, ngoài dữ liệu single-host. Operator chuẩn bị quyền
cho thư mục mới để UID:GID65532:65532 đọc/ghi; không chown thư mục hoặc volume cũ.
Các tên/path dưới đây là placeholder, chưa được thực thi:

```bash
# CHỈ SAU PHÊ DUYỆT; chạy từ gốc repo openswarm đã clone.
OPS_DIR="$PWD/multihost"
WORK_DIR='<thư mục staging tuyệt đối mới, chủ sở hữu 65532:65532>'
HOST_A_IP='<IPv4 RFC1918 thực tế của A>'
HOST_B_IP='<IPv4 RFC1918 thực tế của B>'
RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-$(od -An -N4 -tx1 /dev/urandom | tr -d '[:space:]')"
```

Chuẩn bị `WORK_DIR/input/profile.json` từ `multihost/profile.template.json` và
`WORK_DIR/input/assets/initial.iutfw` từ `multihost/assets/initial.iutfw`, không
ghi đè đích cũ. Điền đúng các field sau trong bản sao profile:

- `deployment.run_id=RUN_ID`, `job_id=mh-<RUN_ID>`.
- `hosts`: address của `rpi5` là HOST_A_IP, của `opi5-pro` là HOST_B_IP.
- `deployment.hosts`: `app_bind_ip` khớp address tương ứng.
- Một controller trên A, `exclusive_owner_acknowledged=true` chỉ khi chắc chắn
  có đúng một writer. Giữ placement, peers, bootstrap và round IDs mặc định.

Validate rồi prepare bằng chính image SWOP có sẵn, không cần thư mục nguồn ngoài
repo. Hai container helper có tên riêng và được giữ lại; không dùng `--rm`:

```bash
# CHỈ SAU PHÊ DUYỆT; WORK_DIR/input đã tồn tại và đọc được bởi UID65532.
docker run --name "osm009-${RUN_ID}-validate" --pull never --platform linux/arm64 \
  --network none --read-only --user 65532:65532 --cap-drop ALL \
  --security-opt no-new-privileges:true -e PYTHONPATH=/app \
  --mount "type=bind,source=$OPS_DIR,target=/ops,readonly" \
  --mount "type=bind,source=$WORK_DIR,target=/work,readonly" \
  --entrypoint python 24520115/independent-swop:v0.1.0-arm64 \
  /ops/tooling.py validate /work/input/profile.json
docker run --name "osm009-${RUN_ID}-prepare" --pull never --platform linux/arm64 \
  --network none --read-only --user 65532:65532 --cap-drop ALL \
  --security-opt no-new-privileges:true -e PYTHONPATH=/app \
  --mount "type=bind,source=$OPS_DIR,target=/ops,readonly" \
  --mount "type=bind,source=$WORK_DIR,target=/work" \
  --entrypoint python 24520115/independent-swop:v0.1.0-arm64 \
  /ops/tooling.py prepare /work/input/profile.json --output /work/bundle
```

Đích `/work/bundle` phải chưa tồn tại. Prepare kiểm tra lại toàn bộ output và in
bundle ID `sha256:...`. ID này không phải digest Docker. Không dùng fixture ID
trong tests làm ID live. Người phát triển có package SWOP đã cài cũng có thể gọi
`python3 -B multihost/tooling.py validate/prepare` với các tham số tương ứng;
đây là lựa chọn tùy chọn, không phải yêu cầu source trên board.

## 3. Phân phối, import và tạo env cho từng host

Operator chuyển cùng **thư mục bundle đầy đủ** và cung cấp expected ID qua kênh
tin cậy riêng. Repo không tự SSH hoặc chuyển file. Không đưa archive ZIP/TAR vào
tooling, không thay file rồi tính hash để giả lập bundle đã được duyệt.

Trên A đặt `HOST_ID=rpi5`; trên B đặt `HOST_ID=opi5-pro`. Mỗi host chọn STAGE_DIR
mới, owner65532:65532, không phải path của deployment single-host cũ. JOB_ROOT là
`STAGE_DIR/job` chưa tồn tại. BUNDLE_DIR đọc được bởi UID65532; OPS_ENV là file
env **mới**, parent có sẵn, nằm ngoài JOB_ROOT và không phải `.env` single-host.

```bash
# CHỈ SAU PHÊ DUYỆT IMPORT; thực hiện riêng trên mỗi host.
HOST_ID='<rpi5 hoặc opi5-pro>'
BUNDLE_ID='<ID sha256 đã duyệt qua kênh tin cậy>'
BUNDLE_DIR='<đường dẫn tuyệt đối bundle nhận được>'
STAGE_DIR='<parent staging mới của host, owner 65532:65532>'
OPS_ENV='<đường dẫn env mới ngoài job>'
test ! -e "$STAGE_DIR/job"
test "$(stat -c '%u:%g' "$STAGE_DIR")" = '65532:65532'
sudo docker run --name "osm009-${RUN_ID}-${HOST_ID}-import" --pull never \
  --platform linux/arm64 --network none --read-only --user 65532:65532 \
  --cap-drop ALL --security-opt no-new-privileges:true -e PYTHONPATH=/app \
  --mount "type=bind,source=$OPS_DIR,target=/ops,readonly" \
  --mount "type=bind,source=$BUNDLE_DIR,target=/bundle,readonly" \
  --mount "type=bind,source=$STAGE_DIR,target=/staging" \
  --entrypoint python 24520115/independent-swop:v0.1.0-arm64 \
  /ops/tooling.py import-host /bundle --host "$HOST_ID" \
  --expected-id "$BUNDLE_ID" --output /staging/job
JOB_ROOT="$STAGE_DIR/job"
sudo bash multihost/scripts/configure.sh --host "$HOST_ID" \
  --job-root "$JOB_ROOT" --expected-id "$BUNDLE_ID" --output "$OPS_ENV"
```

RUN_ID/OPS_DIR cũng phải được đặt đúng trên B, không suy từ hostname hoặc thư mục
download. Import xác minh bằng actual SWOP generator trước khi ghi. Cấu hình bất
biến nằm ở `canonical/` và `deployment/`; checkpoints tại
`deployment/data/checkpoints/client-a` trên A, `.../client-b` trên B; journal
tại `deployment/swop-output` chỉ trên A. Không chia sẻ Docker volume xuyên host.

Không dùng `--defer-ownership` để triển khai thật. Quyền native và các mount vẫn
phải được kiểm thử khi thực thi; rootfs chỉ đọc, app UID65532, `/tmp` là tmpfs.

## 4. Kiểm tra trước khi chạy và thứ tự startup

```bash
# CHỈ ĐỌC; yêu cầu đã có host import hợp lệ và OPS_ENV tương ứng.
sudo bash multihost/scripts/check.sh --host "$HOST_ID" --env "$OPS_ENV"
sudo bash multihost/scripts/preflight.sh --host "$HOST_ID" --env "$OPS_ENV"
sudo bash multihost/scripts/plan.sh --host "$HOST_ID" --env "$OPS_ENV" --stage sl
```

Đọc từng JSON check. Hiện preflight có thể exit0 dù một check là FAIL; exit0
không phải giấy phép startup. Tool từ chối remote Docker context; không tự sửa
context/daemon. Endpoint chưa chạy là NOT TESTED, không được ghi PASS.

Trình tự sau cần hai mức phê duyệt riêng:

1. **Service-only:** SN mới trên A → chờ readiness → SL-A và SL-B → xác minh
   registry đúng hai peer, cùng epoch/job/model/bootstrap, kết nối SL hai chiều.
   Dừng để đánh giá trước khi chạy client/controller.
2. **Training:** khởi động hai clients → operator kiểm tra cả hai CLIENT_READY →
   một controller trên A → hai rounds với aggregator sl-a rồi sl-b → bốn receipts.

CLI thực tế để yêu cầu một stage là
`sudo bash multihost/scripts/start.sh --host HOST --env ENV --stage STAGE --execute-approved`.
STAGE chỉ nhận `sn`, `sl`, `clients`, `controller`; B không được chọn sn/controller.
Clients cần thêm `--ack-container-paths`; controller cần cả
`--ack-container-paths --ack-remote-client`. Các ack không thay thế phép đo.
Không thực hiện các lệnh này khi chỉ được duyệt preflight.

Controller mở callback rồi chạy orchestration ngay, có thể mở round1. Không có
listener-only mode. Không khởi động controller chỉ để thử cổng callback trong pha
service-only. Đường Client-B→callback chỉ có thể kiểm tra live sau khi listener
tồn tại và vẫn cần receipts thực làm bằng chứng. Nếu yêu cầu kiểm tra trước round
là bắt buộc thì cần đề xuất sửa source riêng, không tự thêm flag không tồn tại.

## 5. Trạng thái, logs và dừng có phạm vi

```bash
# Chỉ đọc đúng host/job đã chọn.
sudo bash multihost/scripts/status.sh --host "$HOST_ID" --env "$OPS_ENV"
sudo bash multihost/scripts/logs.sh --host "$HOST_ID" --env "$OPS_ENV" --tail 100
# Chỉ khi được phê duyệt dừng các tài nguyên của job này:
sudo bash multihost/scripts/stop.sh --host "$HOST_ID" --env "$OPS_ENV" --execute-approved
```

Scripts kiểm tra project/run/host/bundle/role labels trước thao tác. Stop chỉ dừng
exact owned IDs, không xóa. Không `down`, prune, rm, cleanup toàn cục hoặc tự retry
mutation không rõ kết quả. Network, container và dữ liệu cũ được giữ lại; không
restart job memory-only để resume. Tiếp theo đọc [kế hoạch bằng chứng](research-evidence.md).
