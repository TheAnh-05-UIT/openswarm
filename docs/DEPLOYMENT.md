# OpenSwarm Deployment Repository

## 1. Bốn Docker Images đã xác minh

Chi tiết đầy đủ: [`tests/evidence/registry-status.json`](../tests/evidence/registry-status.json).

| Image | Tag | Platform | Config Digest | Trạng thái |
| ------- | ----- | ---------- | --------------- | ------------ |
| `24520115/independent-sn` | `v0.1.0-arm64` | `linux/arm64` | `sha256:2c426c892cf5...` | PASS |
| `24520115/independent-sl` | `v0.1.0-arm64` | `linux/arm64` | `sha256:ca0c22b20dbf...` | PASS |
| `24520115/independent-user-tf` | `v0.1.0-arm64` | `linux/arm64` | `sha256:7e2edf5051b0...` | PASS |
| `24520115/independent-swop` | `v0.1.0-arm64` | `linux/arm64` | `sha256:d2810148d814...` | PASS |

Kiểm tra lại trước khi triển khai trên Pi.

---

## 2. Cấu trúc Repository

```
openswarm/
├── compose.yaml                   # Docker Compose JSON syntax, platform linux/arm64
├── .env.example                   # Template cấu hình (không chứa secret thật)
├── .gitignore                     # Loại trừ .env, secrets/*, tests/tmp/, logs/
├── .gitattributes                 # LF cho *.sh *.py *.yaml; binary cho *.iutfw
├── LICENSE                        # MIT License
├── NOTICE.md                      # Deployment provenance và scope
├── README.md                      # Hướng dẫn đầy đủ bằng tiếng Việt
├── config/
│   ├── initialize.py              # Bundle-init glue (chạy trong SWOP image)
│   ├── profile.template.json      # SWOP profile template cho 2 rounds
│   ├── verify.py                  # Evidence verifier (chạy trong SWOP image)
│   ├── verify_fedavg.py           # Numeric FedAvg replay (chạy trong User-TF image)
│   └── assets/
│       └── initial.iutfw          # Numeric bootstrap (SHA-256 verified)
├── docs/
│   ├── GITHUB_SETUP.md            # Hướng dẫn tạo và push GitHub repo thủ công
│   └── TASK_ARM_008_DEPLOYMENT.md # File này — báo cáo nghiệm thu
├── scripts/
│   ├── common.sh                  # Literal .env reader, validation, dc() wrapper
│   ├── check.sh                   # Pre-flight: validate .env + compose config
│   ├── start.sh                   # Pull + up cho job hoàn toàn mới
│   ├── stop.sh                    # Stop exact owned containers, không xóa data
│   ├── logs.sh                    # Follow logs tất cả services
│   ├── verify.sh                  # Chạy verifier container (profile tools)
│   └── verify-fedavg.sh           # Chạy numeric replay sau khi job xong
├── secrets/
│   └── .gitkeep                   # Placeholder; bốn services không dùng static secret
└── tests/
    ├── test_deployment.py          # 48 offline tests (42 non-Bash + 6 Shell)
    ├── test_numeric.py             # 2 NumPy arithmetic unit tests
    ├── validate_compose.py         # Compose config validation script
    ├── probe_registry.py           # Registry API probe (read-only)
    ├── snapshot_docker.py          # Docker state snapshot (read-only)
    └── evidence/
        ├── unit-tests.txt          # 48 tests PASS (môi trường Linux với Bash)
        ├── numeric-unit-tests.txt  # 2 NumPy tests PASS
        ├── compose-validation.json # Compose config PASS/FAIL cases
        ├── registry-status.json    # Registry identity snapshot 01/11/2026
        └── docker-before.json      # Docker state snapshot trước task
```

---

## 3. Lệnh triển khai trên Raspberry Pi 5

```bash
# 1. Clone repository
git clone https://github.com/USERNAME/openswarm.git
cd openswarm

# 2. Tạo .env từ template
cp .env.example .env

# 3. Sinh RUN_ID mới
printf '%s-%s\n' "$(date -u +%Y%m%dt%H%M%Sz)" "$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
# Copy giá trị in ra vào .env

# 4. Điền .env (dùng editor bất kỳ)
nano .env
# Điền: RUN_ID=<giá trị từ bước 3>
# Điền: EXCLUSIVE_OWNER_ACK=true  (chỉ khi chắc chắn một round writer)
# Giữ nguyên các giá trị mặc định khác nếu kiểm thử bộ đã nghiệm thu.

# 5. Pre-flight check
bash scripts/check.sh

# 6. Khởi chạy (pull + up)
bash scripts/start.sh

# 7. Theo dõi
docker compose ps --all
bash scripts/logs.sh        # Ctrl+C để thoát, không dừng containers

# 8. Sau khi job hoàn thành (clients/controller exited 0)
bash scripts/verify.sh
bash scripts/verify-fedavg.sh

# 9. Dừng (không xóa data)
bash scripts/stop.sh
```

---
