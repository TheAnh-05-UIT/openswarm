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

Hai host cần Linux ARM64, Docker local, Compose và IPv4 private định tuyến được;
cài mới theo [install-docker.md](install-docker.md), không cài lại máy đang chạy job.
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
khác nhau và hai rounds. SN/SWOP/User-TF giữ v0.1.0. Với job mới dùng SL v0.1.1,
đặt image của **cả sl-a và sl-b** bằng reference sau trước khi prepare:

```text
docker.io/24520115/independent-sl:v0.1.1-arm64@sha256:dd6fada36039aca41cd3b9590a09484027ebbd067f0fe5ec3bc6faad79ff31fc
```

Allowlist chỉ nhận SL v0.1.0, đúng tag v0.1.1 hoặc bản ghim trên; hai peers phải
cùng reference. Source repo trên hai board phải có thay đổi allowlist đã review.
Không sửa job/bundle cũ để nâng image. Nếu tiếp tục H2 đã seal thì dùng nguyên
canonical bundle/RUN_ID được duyệt, không tạo một job khác theo ví dụ bên dưới.

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

Thay các placeholder trước khi chạy. Tạo các path **mới**, copy input/bootstrap:

```bash
test ! -e "$WORK_DIR" || { echo 'Không ghi đè staging cũ'; exit 1; }
sudo install -d -m 0755 -o 65532 -g 65532 "$WORK_DIR"
sudo install -d -m 0755 "$WORK_DIR/input/assets"
sudo install -m 0644 "$OPS_DIR/profile.template.json" "$WORK_DIR/input/profile.json"
sudo install -m 0644 "$OPS_DIR/assets/initial.iutfw" "$WORK_DIR/input/assets/initial.iutfw"
sudo nano "$WORK_DIR/input/profile.json"
```

Chuẩn bị `WORK_DIR/input/profile.json` từ `multihost/profile.template.json` và
`WORK_DIR/input/assets/initial.iutfw` từ `multihost/assets/initial.iutfw`, không
ghi đè đích cũ. Điền đúng các field sau trong bản sao profile:

- `deployment.run_id=RUN_ID`, `job_id=mh-<RUN_ID>`.
- `hosts`: address của `rpi5` là HOST_A_IP, của `opi5-pro` là HOST_B_IP.
- `deployment.hosts`: `app_bind_ip` khớp address tương ứng.
- Một controller trên A, `exclusive_owner_acknowledged=true` chỉ khi chắc chắn
  có đúng một writer. Giữ placement, peers, bootstrap và round IDs mặc định.
- `published_port` của SN, SL-A, SL-B và controller phải chưa có chủ sở hữu;
  điền cả các cổng job cũ vào `hosts[].reserved_ports`. Ví dụ job mới có thể dùng
  A28400/29401/29600, B29402, nhưng chỉ sau kiểm tra thực tế.
- `resources` và `deployment.hosts` phải khớp RAM/CPU còn dùng được. Với Pi5 có thể
  đặt client1CPU, tool/controller0.25CPU, cpu_quota_budget4, RAM budget6144MiB;
  đây là ví dụ cấu hình, không phải phép đo tài nguyên và phải tính cả job nền.

Validate rồi prepare bằng chính image SWOP có sẵn, không cần thư mục nguồn ngoài
repo. Hai container helper có tên riêng và được giữ lại; không dùng `--rm`:

```bash
# CHỈ SAU PHÊ DUYỆT; WORK_DIR/input đã tồn tại và đọc được bởi UID65532.
docker run --name "osm009-${RUN_ID}-validate" --pull never --platform linux/arm64 \
  --network none --read-only --user 65532:65532 --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 \
  --memory 128m --cpus 0.25 --pids-limit 64 \
  --label io.openswarm.rebuild.owner=openswarm-multihost \
  --label "io.openswarm.rebuild.run=$RUN_ID" \
  -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 \
  --mount "type=bind,source=$OPS_DIR,target=/ops,readonly" \
  --mount "type=bind,source=$WORK_DIR,target=/work,readonly" \
  --entrypoint python 24520115/independent-swop:v0.1.0-arm64 \
  /ops/tooling.py validate /work/input/profile.json
docker run --name "osm009-${RUN_ID}-prepare" --pull never --platform linux/arm64 \
  --network none --read-only --user 65532:65532 --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 \
  --memory 128m --cpus 0.25 --pids-limit 64 \
  --label io.openswarm.rebuild.owner=openswarm-multihost \
  --label "io.openswarm.rebuild.run=$RUN_ID" \
  -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 \
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
  --cap-drop ALL --security-opt no-new-privileges:true \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 \
  --memory 128m --cpus 0.25 --pids-limit 64 \
  --label io.openswarm.rebuild.owner=openswarm-multihost \
  --label "io.openswarm.rebuild.run=$RUN_ID" \
  -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 \
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

Đọc từng JSON check và `readiness`: exit0 chỉ khi gate bắt buộc PASS, exit2 khi
có gate FAIL, exit3 nếu thiếu gate hoặc UNKNOWN/NOT TESTED. Exit0 không phải
giấy phép startup. `--probe-endpoints` yêu cầu thêm host GET tới SN và cả hai SL;
controller endpoint và container/native paths vẫn là observation. Tool từ chối
remote Docker context; không tự sửa context/daemon. Xem phần logging bên dưới.

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
restart job memory-only để resume. Muốn gỡ job đã kết thúc, đọc
[remove-deployment.md](remove-deployment.md); không xóa resources để ép preflight PASS.

## 6. Preflight chỉ đọc và kiểm tra mạng

Trước import/start, kiểm trên từng board; nếu đã có bằng chứng preflight đáng tin
cậy, không cần lặp toàn bộ, chỉ đo lại phần còn thiếu hoặc dễ đổi.

```bash
uname -m
sudo docker context inspect --format '{{.Endpoints.docker.Host}}'
sudo docker info --format 'ID={{.ID}} OS={{.OSType}} ARCH={{.Architecture}} ROOT={{.DockerRootDir}}'
sudo docker compose version
ip -4 address
PEER_IP='<private IPv4 của board còn lại>'
ip -4 route get "$PEER_IP"
sudo ss -H -ltn
sudo docker ps -a --no-trunc --format '{{.ID}} {{.Names}} {{.Status}} {{.Ports}}'
free -m
nproc
df -h .
df -i .
```

Cần native Linux ARM64 và Engine local Unix socket, không phải Docker context
trỏ Windows/host khác. Ghi Engine ID riêng hai máy. Host ID `rpi5`/`opi5-pro`
không nhất thiết trùng hostname OS. Contract chỉ nhận IPv4 RFC1918 khác nhau,
không nhận DNS, loopback, public IP, CGNAT hoặc địa chỉ documentation. Bind là
địa chỉ ứng dụng nghe trong container; advertised URL dùng private host IP và
published port, không dùng0.0.0.0 hoặc container DNS xuyên Engines.

Docker DNAT có thể giữ port mà ss không thấy listener. Đọc binding cấu hình lẫn
runtime của các container liên quan, kể cả stopped/unhealthy; không đọc Env:

```bash
CONTAINER_ID='<ID cần đối chiếu từ inventory>'
sudo docker inspect --format 'NAME={{.Name}} CONFIGURED={{json .HostConfig.PortBindings}} RUNTIME={{json .NetworkSettings.Ports}} MOUNTS={{json .Mounts}}' "$CONTAINER_ID"
```

Đối chiếu wildcard IPv4/IPv6 và private bind IP. Trước Git pull vào clone đang
dùng, review local changes/mounts để không làm đổi code được job cũ mount trực
tiếp. Không reset/clean, không restart/recreate để cập nhật tài liệu/config.

UFW inactive, route, ping hoặc TCP22 chưa chứng minh các cổng SN/SL. Review
firewall/forwarding policy bằng công cụ đã có, không tự sửa policy. API chưa có
production auth/TLS: chỉ cho phép LAN cần thiết, không expose/port-forward Internet.
Hai host phải truy cập SN/SL của nhau và published IP của chính mình (hairpin).
Không tạo listener tạm khi chưa được cho phép. Địa chỉ đổi giữa job thì dừng,
không sửa riêng env của bundle đã seal để resume.

Kiểm disk/inode ở DockerRootDir, containerd storage thực tế và bind job path, không
chỉ `/`. Dự trù compressed/unpacked image, checkpoints, log mới và tăng trưởng log
cũ. Không dùng số RAM tổng làm RAM available. Nếu không đủ capacity, giữ nguyên
job cũ và xin phương án riêng; không cleanup hoặc truncate log.

### Pull SL v0.1.1 và đối chiếu identity

Chỉ sau cho phép tải image, chạy trên cả hai board:

```bash
SL_IMAGE='docker.io/24520115/independent-sl:v0.1.1-arm64@sha256:dd6fada36039aca41cd3b9590a09484027ebbd067f0fe5ec3bc6faad79ff31fc'
sudo docker pull --platform linux/arm64 "$SL_IMAGE"
sudo docker image inspect "$SL_IMAGE" --format 'ID={{.Id}} OS={{.Os}} ARCH={{.Architecture}} USER={{.Config.User}} DIGESTS={{json .RepoDigests}} ROOTFS={{json .RootFS}}'
```

Index đã review là digest trong reference trên; ARM64 platform manifest
`sha256:4863cca792d1cffea3b7840f5754521f00325bf3c4a48b4b75146fdeb5d9ff6d`,
config `sha256:53eb296748e2f0a0758e1d47980e21abb9cfbf5422fcec33a2f4283d49492c74`.
Không nhầm index/platform/config với cùng một loại image ID. Cần linux/arm64,
user65532:65532 và content chain/RootFS đã nghiệm thu; mismatch thì dừng để review,
không retag hoặc bỏ kiểm. Native metadata chưa phải runtime PASS. Kiểm đĩa sau pull.

SWOPv0.1.0 cần trên hai host để prepare/import; SNv0.1.0 trên A, User-TFv0.1.0 trên
hai host trước training. Inspect các images đã có, chỉ pull bản thiếu sau phê
duyệt; không tự nâng version hoặc build source trên board.

## 7. Lệnh startup theo từng pha

Các biến HOST_ID, OPS_DIR, JOB_ROOT, OPS_ENV phải đúng **host hiện tại**. Trước start
đã check/import/preflight PASS, storage mới, port trống, identity đúng và đã cho
phép pha đó. Không chạy cả project bằng `compose up` để bỏ kiểm tra từng service.

```bash
# Chỉ A: SN mới; chờ healthy/fresh epoch rồi mới chạy lệnh tiếp.
sudo bash multihost/scripts/start.sh --host rpi5 --env "$OPS_ENV" --stage sn --execute-approved
# Chỉ A, sau SN mới healthy:
sudo bash multihost/scripts/start.sh --host rpi5 --env "$OPS_ENV" --stage sl --execute-approved
```

Trên B, sau SN mới healthy, OPS_ENV trỏ env riêng B:

```bash
sudo bash multihost/scripts/start.sh --host opi5-pro --env "$OPS_ENV" --stage sl --execute-approved
```

CLI dùng no-build, pull-never, no-recreate, no-deps cho service được chọn. Không
reuse SN epoch/job cũ. Khi cả hai SL hoạt động, kiểm shared state trên mỗi host:

```bash
sudo python3 - "$OPS_DIR" "$HOST_ID" "$OPS_ENV" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import runtime_contract, preflight
ctx = runtime_contract.load_context(sys.argv[2], sys.argv[3])
print({'new_sn_epoch': preflight.fresh_shared_state(ctx, preflight.Runner(), both_sl=True)})
PY
sudo bash multihost/scripts/preflight.sh --host "$HOST_ID" --env "$OPS_ENV" --probe-endpoints
```

Cần epoch mới khác job cũ, chưa có round (`/v1/rounds/current` trả404), đúng hai
alive peers, cùng job/model/bootstrap, committed version0. Host GET không thay
phép đo trong container. Lấy đúng full new SL ID, so labels trước exec:

```bash
PROJECT="osm009-${RUN_ID}-${HOST_ID}"
SL_ROLE=sl-a  # Trên B phải đổi sl-b.
SL_ID="$(sudo docker ps -q --no-trunc --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$SL_ROLE")"
test -n "$SL_ID" || { echo 'Chưa có SL mới'; exit 1; }
sudo docker inspect "$SL_ID" --format 'ID={{.Id}} LABELS={{json .Config.Labels}} LOG={{json .HostConfig.LogConfig}}'
# Chỉ sau khi labels đúng run/host/bundle/role:
sudo docker exec "$SL_ID" python -c 'import platform,sys; print(platform.machine(), sys.version)'
```

GET từ mỗi SL mới tới SN, SL-A và SL-B dùng đúng IP/ports canonical:

```bash
sudo python3 - "$OPS_DIR" "$HOST_ID" "$OPS_ENV" "$SL_ID" <<'PY'
import subprocess, sys
sys.path.insert(0, sys.argv[1])
import runtime_contract, preflight
ctx = runtime_contract.load_context(sys.argv[2], sys.argv[3])
owned, _ = preflight.owned(ctx, preflight.Runner())
role = 'sl-a' if ctx.host == 'rpi5' else 'sl-b'
assert owned[role]['id'] == sys.argv[4]
urls = preflight.endpoints(ctx)
targets = [urls['sn'] + '/health', urls['sl-a'] + '/v1/ml/session', urls['sl-b'] + '/v1/ml/session']
code = 'import sys,urllib.request; print([(u,urllib.request.urlopen(u,timeout=3).status) for u in sys.argv[1:]])'
subprocess.run(['docker', 'exec', sys.argv[4], 'python', '-c', code, *targets], check=True, timeout=15)
PY
```

Ghi kết quả mỗi host/vantage, đủ local hairpin và cross-host. Không query job cũ
thay job mới. Nếu chỉ được duyệt service-only H2, **dừng ở đây**; không chạy clients
hoặc controller để thử callback.

### Training sau phê duyệt riêng

Trên mỗi host, sau khi container paths đã đo PASS và User-TFv0.1.0 đã có:

```bash
sudo bash multihost/scripts/start.sh --host "$HOST_ID" --env "$OPS_ENV" --stage clients --execute-approved --ack-container-paths
sudo bash multihost/scripts/logs.sh --host "$HOST_ID" --env "$OPS_ENV" --tail 100
```

Operator xác nhận **cả hai** CLIENT_READY qua logs; không có API remote-client-ready.
Sau đó chỉ A chạy một controller; callback có thể mở và round bắt đầu ngay:

```bash
sudo bash multihost/scripts/start.sh --host rpi5 --env "$OPS_ENV" --stage controller --execute-approved --ack-container-paths --ack-remote-client
```

Cờ ack là xác nhận operator, không phải phép đo/distributed lock. Cần cả hai
clients hoàn thành hai rounds, cùng global model/apply, dùng weights round trước
cho round kế, bốn receipts. Đường B→callback chỉ được kiểm chứng thật sau listener
và receipts hoạt động; không có listener-only mode.

## 8. Logging, dữ liệu và bằng chứng

Compose vận hành nằm trong repo, qua operations.py; không chạy bản Compose
reference SWOP trong bundle để bỏ runtime/logging guards. Containers mới dùng
json-file10m×3, restart=no, UID:GID65532:65532, read-only rootfs, tmpfs và mounts
riêng. Helpers trong mục2/3 cũng có log rotation, không dùng --rm/docker.sock.
Policy không thay container cũ; không recreate SN hoặc truncate file log đang mở.

```bash
# Mốc UTC thu thật; chờ đủ cửa sổ muốn quan sát rồi mới lấy logs.
SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sudo bash multihost/scripts/logs.sh --host "$HOST_ID" --env "$OPS_ENV" --tail 300 --since "$SINCE"
```

Logs có timestamps; --tail1..1000/service, --since UTC RFC3339 tuyệt đối, có thể
có1..9 chữ số phần giây, không nhận duration hoặc timezone offset. Shared byte
budget1048576 stdout+stderr, metadata nêu truncation, timeout, decode/read error.
Exit3 incomplete, exit2 command lỗi, exit0 command thu đủ; không đồng nghĩa đủ
lịch sử job hoặc runtime PASS. Boundary có thể lặp, không phải exactly-once cursor.

A lưu checkpoints `deployment/data/checkpoints/client-a` và journal/receipts ở
`deployment/swop-output`; B lưu `.../client-b`. Không shared volume xuyên Engines.
`verify-local` chỉ cho import mới chưa có runtime outputs; không xóa data để ép
verify lại. Mất SN epoch phải fence job, không restart/retry mơ hồ để resume.

Lưu riêng evidence A/B: host/kernel/Engine IDs, image identity, profile/bundle,
epoch/peers/sessions, labels/mounts/log policy, logs, journal/receipts/checkpoints,
RAM/CPU/disk và thời gian/command/exit status thực. Hash sau khi thu, không dựng
logs/timestamp/sai số. Clock chưa đồng bộ hạn chế latency; stats một lần không
chứng minh peak; chưa có SN/SL `/metrics`. Không đưa tokens/.env/raw logs lên Git.

Verifier single-host dùng layout volumes khác, **không chạy nguyên xi trên
multi-host**. Cần thu checkpoint/logs hai phía về môi trường phân tích được duyệt;
hiện chưa có verifier cross-host hoàn chỉnh tương đương scripts/verify-fedavg.sh.
Tính `sum(n_i * W_i) / sum(n_i)` bằng float64 rồi castfloat32, tolerance tham
chiếu1e-6; replay tái dựng theo seed/base phải ghi rõ, không gọi là bắt gói wire
update. Không lấy sai số0.0 của lần chạy khác làm kết quả mới. Native multi-host
và FedAvg chỉ PASS khi có evidence thật. Default bridge ID đổi vẫn UNKNOWN.

## 9. Kiểm thử offline dành cho developer

```bash
python3 -B multihost/tests/run_offline.py
```

Dùng SWOPv2/NumPy/Bash/ComposeCLI đã chuẩn bị ở máy phát triển. --swop-source chỉ
cho developer có package/source riêng; không phải dependency board. Windows chọn
--bash "C:/Program Files/Git/bin/bash.exe". Có thể đặt OPENSWARM_TEST_WORK và
OPENSWARM_TEST_EVIDENCE ngoài repo, không trỏ vào data live. Runner dùng bản sao;
SKIP không phải toàn suite PASS. Compose config/mock tests không phải native
runtime. Không thêm Monitoring khi chưa giải quyết blocker phát hành riêng.
