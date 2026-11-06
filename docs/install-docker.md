# Cài Docker trên Raspberry Pi 5 và Orange Pi 5 Pro

Hướng dẫn cho **Ubuntu Server 64-bit** trên ARM64, gồm Ubuntu 22.04/24.04.
Nếu board đã có Docker hoạt động, chỉ làm mục 1 và 4; **không cài lại hoặc nâng
version khi đang có job chạy**. Các bước cài đặt dưới đây dành cho máy mới và do
người quản trị chủ động thực hiện. Repository không tự cài Docker.

## 1. Kiểm tra trước khi cài

```bash
uname -m
cat /etc/os-release
dpkg --print-architecture
command -v docker || true
```

Kiến trúc cần là `aarch64` và `arm64`. Nếu OS không phải Ubuntu, dùng hướng dẫn
đúng distro; không gán codename Ubuntu tùy ý cho Armbian/Debian. Bản Ubuntu tùy
biến của nhà sản xuất có thể cần kiểm tra hỗ trợ riêng.

Nếu đã có Docker:

```bash
sudo docker version
sudo docker compose version
sudo docker ps -a
```

Giữ nguyên môi trường đang chạy tốt. Nếu gặp xung đột package `docker.io`,
`containerd`, `runc` hoặc bản cài khác, dừng để lập phương án migration; hướng dẫn
này không yêu cầu gỡ chúng tự động hoặc xóa `/var/lib/docker`.

## 2. Cài từ APT repository chính thức — chỉ máy mới

Tham chiếu [Docker Engine cho Ubuntu](https://docs.docker.com/engine/install/ubuntu/).
Các lệnh thay đổi package/config hệ thống; đọc từng bước trước khi chạy.

```bash
sudo apt update
sudo apt install ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

Thêm repository phù hợp kiến trúc và codename OS đã xác minh:

```bash
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Không chạy lại mục này để nâng Docker của job hiện có. Ghi lại version thực tế;
hướng dẫn không pin một version Docker mới cho mọi board.

## 3. Chọn cách cấp quyền

Cách đơn giản là dùng `sudo docker ...`; với wrapper của repo, dùng
`sudo bash scripts/check.sh` hoặc `sudo bash multihost/scripts/check.sh ...`.
Đọc [quyền Docker trên Linux](https://docs.docker.com/engine/install/linux-postinstall/):
thành viên nhóm `docker` có quyền tương đương root qua daemon.

Nếu quản trị viên chọn cấp quyền này cho tài khoản hiện tại:

```bash
sudo usermod -aG docker "$USER"
```

Đăng xuất rồi đăng nhập lại, sau đó thử `docker info`. Không dùng
`chmod 666 /var/run/docker.sock`; không mở Docker TCP socket ra mạng.

## 4. Kiểm tra sau cài, không tạo container thử

```bash
sudo systemctl is-active docker
sudo docker context show
sudo docker context inspect --format '{{.Endpoints.docker.Host}}'
sudo docker info --format 'OS={{.OSType}} ARCH={{.Architecture}} ROOT={{.DockerRootDir}}'
sudo docker compose version
git --version
free -h
df -h /
df -i /
```

Engine cần `linux/aarch64` hoặc `linux/arm64`, endpoint local `unix://...`.
Compose cần hỗ trợ các tính năng trong repo; mốc tham khảo là v2.24+, không loại
trừ Compose major mới hơn. `docker-compose` bản legacy không phải lệnh được dùng.
Nếu service chưa active trên máy mới, kiểm tra `sudo journalctl -u docker -n 50
--no-pager` trước; không tự restart Engine đang phục vụ job khác.

Không cần `hello-world`, QEMU, Docker Desktop, TensorFlow host hoặc build source
để kiểm tra các điều kiện trên. Multi-host cần thêm Bash, Python3.10+ thư viện
chuẩn, `ip`, `ss`, `timeout`, `sha256sum`; single-host không cần Python host.

## 5. Lưu trữ, logging và mạng

- Xem dung lượng tại DockerRootDir và containerd storage thực tế, không chỉ `/`.
  Images, layer giải nén, checkpoint và logs đều chiếm đĩa; kiểm cả inode.
- Compose của repo đặt `json-file`, `max-size=10m`, `max-file=3` cho container mới.
  Đây là budget rotation danh nghĩa, không phải hard cap tổng dung lượng.
  Git pull không đổi log policy container cũ; không recreate SN để áp dụng ngay.
- Published ports của Docker cần được kiểm trong chính sách firewall/forwarding;
  không dựa riêng vào UFW. Xem [Docker và firewall](https://docs.docker.com/engine/network/packet-filtering-firewalls/).
  Chỉ cho các host LAN được phép liên lạc, không port-forward API ra Internet.

Tiếp tục bằng [single-host](single-host.md) hoặc [multi-host](multi-host.md).
Gỡ một job nằm trong [remove-deployment.md](remove-deployment.md), không phải
gỡ Docker Engine hay xóa toàn bộ dữ liệu daemon.
