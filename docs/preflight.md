# Kiểm tra host và mạng trước triển khai

Các lệnh trong tài liệu là để operator chạy tại đúng board, không tự SSH/deploy.
Chỉ đọc metadata, không tạo/dừng/xóa container hoặc sửa IP/firewall/daemon.
Kiểm tra chưa chạy phải ghi **NOT TESTED**, không dựa vào fixture/tests để ghi PASS.

## 1. Xác định đúng thiết bị

Ví dụ operator từng báo LAN `192.168.50.0/24`: RPi5 `rpi5`, `eth0`, `.233`;
Orange Pi `oran5`, `enP4p65s0`, `.135`; router Pi4 `.1`. Đây là địa chỉ DHCP đã
được báo, không phải reservation hoặc địa chỉ mặc định trong code. Node `oran51`
`.136` không thuộc lần triển khai hai host này. Ping/TCP22 được báo PASS không
chứng minh SN/SL hoặc callback hoạt động.

Đặt các biến theo trạng thái **hiện tại**, không copy mù ví dụ:

```bash
# A: IFACE=eth0, PEER_IP=<địa chỉ B hiện tại>
# B: IFACE=enP4p65s0, PEER_IP=<địa chỉ A hiện tại>
IFACE='<interface đã xác nhận>'
PEER_IP='<IPv4 private của host còn lại>'
date -u +%Y-%m-%dT%H:%M:%SZ
hostname
uname -m
uname -srvo
cat /etc/os-release
ip -j -4 address show dev "$IFACE"
ip -j -4 route get "$PEER_IP"
ping -n -c 3 -W 2 "$PEER_IP"
timedatectl show --property=NTPSynchronized
```

Yêu cầu Linux ARM64, IP đúng trên interface UP, route usable không phải loopback.
Đổi lease thì dừng đánh giá, không sửa riêng env của bundle đã seal. Clock chưa
đồng bộ hạn chế suy luận độ trễ giữa hai máy; không tự enable NTP trong preflight.

## 2. Docker local, tài nguyên cũ và image identity

```bash
# Chọn socket local cho từng lệnh, không đổi cấu hình Docker context.
dlocal() { timeout 30 sudo docker --host unix:///var/run/docker.sock "$@"; }
dlocal info --format '{"id":{{json .ID}},"os":{{json .OSType}},"arch":{{json .Architecture}}}'
dlocal compose version
dlocal compose ls --all --format json
dlocal container ls --all --no-trunc --format '{{json .}}'
dlocal network ls --no-trunc
dlocal volume ls
free -m
df -hT .
df -i .
nproc
```

Ghi các ID/state/network/mount/volume hiện có trước thử nghiệm và đối chiếu sau.
Không bỏ qua container đã dừng. Trên hai máy phải có hai Engine ID khác nhau,
Linux ARM64 local. Thiếu quyền/socket/tool là NOT TESTED, không tự sửa quyền hoặc
trỏ sang remote Engine. Không ghi nguyên `.Config.Env`, token hoặc credentials.

Với image cần dùng, chạy riêng từng reference đã duyệt:

```bash
dlocal image inspect --format '{"id":{{json .Id}},"digests":{{json .RepoDigests}},"os":{{json .Os}},"arch":{{json .Architecture}},"user":{{json .Config.User}}}' 24520115/independent-sl:v0.1.0-arm64
```

Lặp lại cho SN, User-TF và SWOP theo placement. So với
[image-manifest.json](../config/image-manifest.json), giữ riêng config ID,
platform manifest và tag/index digest; chúng không phải một loại digest. Tag có
thể thay đổi. Missing image hoặc identity khác phải điều tra, không tự pull/retag.

Multi-host dùng project `osm009-<RUN_ID>-<host>`; single-host dùng
`openswarm-<RUN_ID>`. Multi-host dùng bind storage mới, không dùng named volumes
single-host. Xác minh RUN_ID mới không có trên cả hai host; labels phải đúng owner,
run, host, role, bundle. Giữ nguyên resources cũ; nguyên nhân bridge ID thay đổi
trước đây vẫn UNKNOWN, không suy đoán từ snapshot.

## 3. Port, firewall và dung lượng

```bash
sudo ss -H -ltnp
dlocal container ls --no-trunc --format '{{.ID}} {{.Names}} {{.Ports}}'
sudo ufw status verbose
sudo nft list ruleset
sudo iptables -S
sudo iptables -t nat -S
```

Chạy công cụ firewall nào đã có; thiếu quyền/tool không có nghĩa firewall tắt.
Kiểm tra A18000/19001/19200 và B19002, kể cả bind wildcard IPv4/IPv6. Docker DNAT
có thể giữ cổng dù ss không thấy listener. Port trống chỉ là quan sát tức thời.
Không dừng chủ sở hữu cổng khác hoặc mở firewall để ép PASS.

Memory caps multi-host: A3200MiB, B2560MiB; CPU quota tổng A4.5, B3. Đó không phải
mức sử dụng thực, và A có thể oversubscribe bốn cores. Cần xét workload cũ, OS,
disk/inode và bằng chứng/checkpoint dự kiến; không suy RAM đủ từ tổng caps.

## 4. Preflight có import và ma trận kết nối ứng dụng

Board chưa import hợp lệ dùng các bước trên. Sau khi được phê duyệt import và đã
có OPS_ENV mới từ canonical bundle:

```bash
HOST_ID='<rpi5 hoặc opi5-pro>'
OPS_ENV='<file env của đúng host import, không phải .env single-host>'
sudo bash multihost/scripts/check.sh --host "$HOST_ID" --env "$OPS_ENV"
sudo bash multihost/scripts/preflight.sh --host "$HOST_ID" --env "$OPS_ENV" --probe-endpoints
```

Đọc JSON từng check, không chỉ exit code. PASS chỉ áp dụng quan sát đó. FAIL phải
dừng; NOT TESTED không phải PASS; REQUIRES PHYSICAL TESTING chỉ rõ cần phép đo trên
đường mạng container thật. Không `preflight && start` làm điều kiện tự động.

| Vị trí đo | Đích | Điều kiện chạy phép đo |
|---|---|---|
| A host | SL-B `B:19002/v1/ml/session` | Sau phê duyệt SL-B startup |
| B host | SN `A:18000/health`, SL-A `A:19001/v1/ml/session` | Sau phê duyệt SN/SL startup |
| Hai SL containers | SN và cả hai SL URLs | Dùng exact owned IDs đã kiểm tra, GET có timeout |
| Mỗi client | SL được gán tại host của nó | Sau phê duyệt client startup |
| Controller container | Cả hai SL | Chỉ tồn tại sau phê duyệt controller/training |
| Client-B và Client-A | Callback `A:19200/health` | Sau khi controller listener tồn tại; startup có thể mở round |

GET từ host không chứng minh container path. HTTP200 không đủ: so đúng peer,
model/job, bootstrap và cùng `sn_instance_id`; registry phải có đúng hai alive
peers với advertised URLs đúng host IP/port. Fresh SN chưa có round current.
Không mở round thử, upload giả hoặc tạo test container tự động.

Container-level GET cần phê duyệt exec riêng, sử dụng containers của job mới đã
chạy. Controller không có listener-only mode; không chạy nó trong pha service-only
chỉ để thử callback. Actual model exchange, bốn receipts, hai rounds đúng thứ tự
và numeric verification mới là bằng chứng training multi-host.
