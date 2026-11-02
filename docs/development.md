# Phát triển, kiểm thử và chuẩn bị GitHub

## Phạm vi repo

Repo chỉ chứa deployment glue, cấu hình mẫu, tài liệu và tests. Ứng dụng SN/SL/
User-TF/SWOP nằm trong các image đã phát hành. Clone repo không cần một thư mục
workspace khác và không cần chép mã ứng dụng vào board.

- Giữ tên file tiếng Anh, ASCII, theo chức năng; giữ API/CLI/JSON/env names.
- Chú thích và docstring tiếng Việt, giải thích mục đích và giới hạn an toàn.
- File text UTF-8, shell dùng LF. JSON không hỗ trợ comment: giải thích field
  trong tài liệu, không chèn `#` hoặc field mới làm thay đổi schema.
- Giữ nguyên văn LICENSE và giấy phép/thông báo bản quyền cần thiết.
- Không đưa log task, snapshot host, dữ liệu job, cache hoặc secret vào GitHub.

## Kiểm tra trên bản clone mới

Các lệnh dưới đây dành cho **máy phát triển**, không phải yêu cầu cài Python lên
board để chạy single-host. Dùng Python3.11/Bash đã có; không tự cài dependency:

```bash
python3 -B tests/check_repository.py
python3 -B -m unittest discover -s tests -v
python3 -B tests/validate_compose.py
python3 -B multihost/tests/run_offline.py
```

- `check_repository.py`: kiểm tra file bắt buộc, link tài liệu, cú pháp Python,
  path trong repo, bootstrap, env, Compose/security cơ bản và secret bị bỏ qua.
- Unit tests dùng NumPy/SWOP có sẵn cho nhóm tùy chọn; thiếu package sẽ báo SKIP.
  Không gọi đó là toàn bộ tests PASS. Không cần TensorFlow cho suite này.
- `validate_compose.py`: Docker CLI chỉ chạy `compose config` trên bản sao với
  dữ liệu giả; không cần daemon để tạo resource, không pull/run/up/stop.
- `run_offline.py`: copy repo sang vị trí khác, chạy bundle/operations tests và
  regression một host. Cần SWOP v2/NumPy đã có và Docker Compose CLI để chạy đủ.
  `--swop-source <path>` là tùy chọn cho developer đã có source/package riêng;
  không phải dependency mặc định của repo hoặc các board.
- Trên Windows, thêm `--bash "C:/Program Files/Git/bin/bash.exe"` cho runner;
  không vô tình dùng `bash.exe` của WSL. Unit tests trực tiếp có thể đặt BASH_BIN.

Runner trả lỗi nếu thiếu dependency khiến tests bị SKIP, thay vì ghi toàn bộ PASS.
Kết quả được tạo mới trong thư mục evidence bị ignore; không cần commit kết quả
để lần clone tiếp theo chạy được. Tests tạo/xóa fixture tạm do chính chúng sở hữu,
không sửa dữ liệu deployment hoặc tài nguyên Docker đang có.

## Công cụ audit tùy chọn

`tests/probe_registry.py` đọc Registry qua Internet, không pull layer và không
lưu token. `tests/snapshot_docker.py before|after` đọc Engine đang chọn, ghi snapshot
và đối chiếu ID/state; không chứng minh nguyên nhân thay đổi. Hai công cụ này
**không thuộc suite offline mặc định**. Chỉ chạy khi được phép truy cập đúng
Registry/Engine; kiểm tra context trước, không dùng nó để deploy từ xa.

`config/image-manifest.json` là inventory nhỏ trích từ metadata đã kiểm tra trước
đó, không phải kết quả query mới. Giữ các loại digest riêng. Source/package SWOP
chỉ cần cho developer muốn chạy đủ contract tests; trên board dùng package trong
image để prepare/import như [multi-host.md](multi-host.md) hướng dẫn.

## Trước khi đưa lên GitHub

1. Kiểm tra thay đổi bằng `git status --short` và `git diff --check`.
2. Chạy kiểm tra repo, unit tests và Compose config thích hợp.
3. Kiểm tra không có `.env`, credential, private key, job storage, snapshot hoặc
   folder evidence trong danh sách file sẽ commit. `.gitignore` không tự bỏ file
   đã được track; các xóa/di chuyển file cũ cần được người quản lý review.
4. Chỉ commit thư mục repo đã chuẩn bị; không kèm workspace build, bản lưu trữ
   nội bộ hoặc Docker save artifacts. Thao tác commit/push do operator quyết định.
5. Trên board đã có job cũ, kiểm tra local changes trước khi cập nhật repo;
   không reset/clean hoặc ghi đè `.env` để lấy bản mới. Đối chiếu RUN_ID và paths
   trước khi gọi script. Cập nhật code không có nghĩa là được restart job cũ.

Repo không có cleanup toàn cục, không mount docker.sock và không tự thay Docker/
WSL/OS. Thay đổi controller/protocol/permissions phải được test riêng; chỉ dọn
tài liệu/chú thích không được dùng để tuyên bố runtime multi-host đã PASS.
