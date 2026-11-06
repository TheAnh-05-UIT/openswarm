# Dừng và gỡ deployment single-host hoặc multi-host

Tài liệu dành cho operator **chủ động kết thúc một job đã chọn**. Việc có tài
liệu này không cho phép agent tự xóa Docker resources. Không áp dụng cho job cũ
đang cần bảo toàn hoặc để xử lý lỗi preflight. Không gỡ Docker Engine, xóa images,
reset bridge hoặc cleanup toàn cục.

## 1. Hiểu tác động trước khi chọn

| Thao tác | Kết quả | Có thể tiếp tục job cũ? |
|---|---|---|
| Đọc status/logs, lưu evidence | Không dừng ứng dụng | Có, nếu ứng dụng còn khỏe |
| Stop đúng project | Giữ container, network, dữ liệu; SN/SL mất RAM state khi dừng | Không resume chỉ bằng restart |
| Xóa container đã dừng | Mất writable layer và Docker log của container đó | Không |
| Xóa network không còn endpoint | Mất network của job | Không |
| Xóa volume single-host | Mất bundle, checkpoint hoặc journal trong volume | Không, trừ bản sao lưu riêng |
| Xóa thư mục bind multi-host | Mất dữ liệu host; Docker không quản lý thay operator | Không |

Không dùng `docker compose down`, `--remove-orphans`, `docker system prune`,
container/network/volume prune, `docker rm -f`, `docker rm -v` hoặc xóa hàng loạt
theo tên gần giống. Không truncate file Docker log đang mở. Images dùng chung
được giữ để job khác tiếp tục hoạt động.

## 2. Xác minh và lưu bằng chứng trước khi dừng

Tại đúng board/Engine local:

```bash
hostname
sudo docker context inspect --format '{{.Endpoints.docker.Host}}'
sudo docker info --format 'ID={{.ID}} OS={{.OSType}} ARCH={{.Architecture}}'
sudo docker ps -a --no-trunc --format '{{.ID}} {{.Names}} {{.Status}} {{.Ports}}'
```

Đối chiếu RUN_ID, project, owner, host và bundle; tên container chỉ là gợi ý,
**labels/ID mới là điều kiện kiểm tra**. Lưu bounded logs, container/image IDs,
checkpoint/journal/receipts và mount inventory ra nơi riêng trước khi xóa.
`docker container rm` làm mất log do Docker giữ. Không gửi token/.env lên GitHub.

Container cùng project nhưng thiếu/sai ownership labels phải dừng review, không
lọc bỏ rồi xóa phần còn lại. Không tự nhận container helper là Compose service.

## 3. Dừng single-host, giữ toàn bộ resources

Từ clone và `.env` của đúng job cần kết thúc:

```bash
# Chỉ in RUN_ID, không dump toàn bộ env/secrets.
grep '^RUN_ID=' .env
sudo bash scripts/stop.sh
```

Script kiểm toàn bộ candidates theo project/run/owner rồi dừng controller trước,
tiếp đến clients, SL, SN và helper thuộc project. Không xóa gì. Nếu `.env` hiện
trỏ job khác, **không sửa RUN_ID của nó để gỡ job cũ**: tìm lại cấu hình đã lưu
hoặc kiểm exact resources thủ công, không đoán tên.

Volumes single-host thường là `<PROJECT>_deployment`, `_checkpoint-a`,
`_checkpoint-b`, `_controller-data`, project `openswarm-<RUN_ID>`. Xác minh bằng
inspect, không dùng tên này để xóa nếu labels không khớp.

## 4. Dừng multi-host, thao tác riêng trên hai board

Trên A đặt `HOST_ID=rpi5`, trên B đặt `HOST_ID=opi5-pro`; OPS_ENV phải là env của
**đúng host và đúng job**. Chỉ khi đã cho phép kết thúc job:

```bash
HOST_ID='<rpi5 hoặc opi5-pro>'
OPS_ENV='<absolute path host.env của job cần dừng>'
sudo bash multihost/scripts/status.sh --host "$HOST_ID" --env "$OPS_ENV"
sudo bash multihost/scripts/logs.sh --host "$HOST_ID" --env "$OPS_ENV" --tail 100
# Sau khi đọc lại identity và được cho phép dừng:
sudo bash multihost/scripts/stop.sh --host "$HOST_ID" --env "$OPS_ENV" --execute-approved
```

Ở job đã chạy training: kết thúc controller trên A trước khi dừng phía B; helper
stop của A dừng controller trước các local services. Hai host không có atomic
stop xuyên mạng. Không SSH tự động; operator thực hiện và ghi kết quả từng board.
Nếu một pha lỗi, kiểm tra trạng thái còn lại, không tự retry/xóa để che lỗi.

Project là `osm009-<RUN_ID>-<host>`, network `<project>_lab`. Dữ liệu ở bind paths
JOB_ROOT/deployment, **không phải Docker named volumes**. Docker network rm hoặc
container rm không xóa checkpoint/journal host; không dùng volume rm thay thế.

## 5. Xóa container/network/volume đã dừng — phê duyệt riêng

Chỉ sau khi sao lưu và chọn đúng từng resource. Các hàm sau kiểm labels ngay
trước khi xóa, từ chối container running hoặc network/volume còn được sử dụng.
Chúng không tìm rồi xóa toàn bộ project, không có cờ force. Mỗi lần chỉ xóa **một
resource cụ thể**, phải gõ lại ID/name đầy đủ. Không chạy cho job đang được bảo vệ.

### 5.1. Chọn mode và nhận dạng mong đợi

Mở Bash trên board, đặt một bộ biến cho job cần xóa; không dùng giá trị job mới
đang chuẩn bị hoặc một ID từ ví dụ cũ:

```bash
MODE=single  # Hoặc multi, chỉ chọn một mode.
RUN_ID='<RUN_ID của job đã cho phép xóa>'
HOST_ID='<rpi5 hoặc opi5-pro nếu multi>'
BUNDLE_ID='<sha256:... đã xác minh nếu multi>'

if [[ ! "$RUN_ID" =~ ^[0-9]{8}t[0-9]{6}z-[0-9a-f]{8}$ ]]; then
  echo 'RUN_ID không hợp lệ'; exit 1
fi
case "$MODE" in
  single) PROJECT="openswarm-$RUN_ID" ;;
  multi)
    [[ "$HOST_ID" == rpi5 || "$HOST_ID" == opi5-pro ]] || exit 1
    [[ "$BUNDLE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
    PROJECT="osm009-$RUN_ID-$HOST_ID"
    ;;
  *) echo 'Mode không hợp lệ'; exit 1 ;;
esac
```

Định nghĩa guard; dùng Unix socket đã xác minh trên **board hiện tại**, không
đổi Docker context hoặc dùng remote endpoint:

```bash
dlocal() { sudo docker --host unix:///var/run/docker.sock "$@"; }
check_owner() {
  local kind="$1" resource="$2" actual expected
  if [[ "$MODE" == single ]]; then
    expected="$PROJECT|openswarm-deployment|$RUN_ID|single-pi-v1"
    if [[ "$kind" == container ]]; then
      actual=$(dlocal container inspect --format '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "io.openswarm.deployment.owner"}}|{{index .Config.Labels "io.openswarm.deployment.run"}}|{{index .Config.Labels "io.openswarm.deployment.contract"}}' "$resource") || return 1
    else
      actual=$(dlocal "$kind" inspect --format '{{index .Labels "com.docker.compose.project"}}|{{index .Labels "io.openswarm.deployment.owner"}}|{{index .Labels "io.openswarm.deployment.run"}}|{{index .Labels "io.openswarm.deployment.contract"}}' "$resource") || return 1
    fi
  else
    expected="$PROJECT|openswarm-multihost|osm009|$RUN_ID|$HOST_ID|$BUNDLE_ID"
    if [[ "$kind" == container ]]; then
      actual=$(dlocal container inspect --format '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "io.openswarm.rebuild.owner"}}|{{index .Config.Labels "io.openswarm.rebuild.project"}}|{{index .Config.Labels "io.openswarm.rebuild.run"}}|{{index .Config.Labels "io.openswarm.rebuild.host"}}|{{index .Config.Labels "io.openswarm.bundle"}}' "$resource") || return 1
    else
      actual=$(dlocal "$kind" inspect --format '{{index .Labels "com.docker.compose.project"}}|{{index .Labels "io.openswarm.rebuild.owner"}}|{{index .Labels "io.openswarm.rebuild.project"}}|{{index .Labels "io.openswarm.rebuild.run"}}|{{index .Labels "io.openswarm.rebuild.host"}}|{{index .Labels "io.openswarm.bundle"}}' "$resource") || return 1
    fi
  fi
  [[ "$actual" == "$expected" ]] || { echo 'OWNERSHIP MISMATCH: không xóa'; return 1; }
}
confirm_delete() {
  local answer
  read -r -p "Gõ DELETE $1 để xác nhận: " answer || return 1
  [[ "$answer" == "DELETE $1" ]]
}
```

### 5.2. Xóa đúng một container đã dừng

Lấy ID64 ký tự từ inventory, đọc lại labels/role/mounts. Sau đó:

```bash
CONTAINER_ID='<full 64-character container ID đã duyệt>'
remove_one_container() {
  [[ "$CONTAINER_ID" =~ ^[0-9a-f]{64}$ ]] || return 1
  check_owner container "$CONTAINER_ID" || return 1
  [[ "$(dlocal container inspect --format '{{.State.Running}}' "$CONTAINER_ID")" == false ]] || return 1
  confirm_delete "$CONTAINER_ID" || return 1
  check_owner container "$CONTAINER_ID" || return 1
  dlocal container rm "$CONTAINER_ID"
}
remove_one_container
```

Không thêm -f/-v. Nếu container vừa được khởi chạy lại, Docker sẽ từ chối xóa
running container. Helper validate/prepare/import ngoài Compose có thể thiếu
Compose labels và bị guard từ chối: cần approval/ownership review riêng, không
sửa labels hoặc bỏ guard để xóa chúng.

### 5.3. Xóa network riêng sau khi không còn endpoint

```bash
NETWORK_ID='<full 64-character network ID đã duyệt>'
remove_one_network() {
  [[ "$NETWORK_ID" =~ ^[0-9a-f]{64}$ ]] || return 1
  check_owner network "$NETWORK_ID" || return 1
  [[ "$(dlocal network inspect --format '{{len .Containers}}' "$NETWORK_ID")" == 0 ]] || return 1
  confirm_delete "$NETWORK_ID" || return 1
  check_owner network "$NETWORK_ID" || return 1
  dlocal network rm "$NETWORK_ID"
}
remove_one_network
```

Không detach/force-disconnect container để vượt kiểm tra. Không xóa default
bridge, host, none hoặc network của dự án khác; chúng không có labels của job.

### 5.4. Xóa named volume — chỉ single-host

Chỉ sau khi checkpoint/journal đã sao lưu và đã xóa container tham chiếu, kể cả
container verifier đã dừng. Xóa volume là mất dữ liệu thật:

```bash
VOLUME_NAME='<exact named volume đã duyệt>'
remove_one_volume() {
  [[ "$MODE" == single ]] || return 1
  case "$VOLUME_NAME" in
    "${PROJECT}_deployment"|"${PROJECT}_checkpoint-a"|"${PROJECT}_checkpoint-b"|"${PROJECT}_controller-data") ;;
    *) return 1 ;;
  esac
  check_owner volume "$VOLUME_NAME" || return 1
  local users
  users=$(dlocal ps -aq --filter "volume=$VOLUME_NAME") || return 1
  [[ -z "$users" ]] || { echo 'Volume còn container tham chiếu'; return 1; }
  confirm_delete "$VOLUME_NAME" || return 1
  check_owner volume "$VOLUME_NAME" || return 1
  dlocal volume rm "$VOLUME_NAME"
}
remove_one_volume
```

Không xóa `secrets`, repo, image hoặc filesystem Docker. Với multi-host, xóa bind
storage cần **một quyết định riêng kèm absolute path đã resolve**, so JOB_ROOT,
import receipt và mount refs của mọi container, bảo đảm path nằm trong đúng job
được duyệt và không có mount dùng chung. Tài liệu không cung cấp `rm -rf` tổng
quát: giữ storage là lựa chọn mặc định sau gỡ containers/network.

## 6. Kiểm tra sau gỡ và chạy lại

```bash
dlocal ps -a --no-trunc --format '{{.ID}} {{.Names}} {{.Status}}'
dlocal network ls --no-trunc
dlocal volume ls
```

Đối chiếu snapshot trước/sau, ghi exact IDs đã xóa và kết quả từng bước; mọi thay
đổi ngoài scope giữ UNKNOWN tới khi có bằng chứng. Không tự khôi phục DevOps hoặc
quy nguyên nhân bridge ID thay đổi. Giữ evidence và storage đã chọn bảo toàn.

Một lần chạy mới cần RUN_ID mới, bundle/import/storage mới; không lấy checkpoint
cũ để giả lập resume SN epoch. Quay lại [single-host](single-host.md) hoặc
[multi-host](multi-host.md) theo topology muốn thử.
