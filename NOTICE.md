# Nguồn gốc và phạm vi sử dụng

Repository chứa cấu hình triển khai, công cụ vận hành/kiểm thử do dự án độc lập
phát triển và một bootstrap số học thuộc dự án. Không lấy source, launcher,
binary hay quyền sử dụng từ image OpenSwarm/HPE cũ chưa được cấp phép.
Runtime launcher được image Independent SWOP đã chọn sinh ra.

Bootstrap có SHA-256:
`590525b9b37b66b74a21c492a910a27b30ca030d2d15bac64a1b4203ae314702`.
Định dạng gồm JSON metadata có giới hạn và tensor float32; không phải code,
pickle, dataset tải ngoài hoặc pretrained model tải về.

File do dự án viết áp dụng giấy phép MIT kèm trong LICENSE. Giữ nguyên văn bản
giấy phép và thông báo bản quyền khi phân phối. Dependency, base image và các
thành phần trong Docker images giữ giấy phép riêng của chúng. Repo không khẳng
định nghĩa vụ SBOM, Corresponding Source hoặc phân phối của mọi image đã hoàn
tất chỉ vì image được publish hay runtime chạy thành công.

Bốn image SN/SL/User-TF/SWOP đã được publish và operator xác nhận chạy single-host
native ARM64 riêng trên RPi5 và Orange Pi5Pro. Monitoring/Grafana bị chặn phát
hành riêng và không nằm trong deployment mặc định. Lần chuẩn hóa repo này không
thực hiện một cuộc license audit mới và không mở rộng kết luận runtime.

Không tuyên bố sự bảo trợ của HPE/OpenSwarm hay khả năng tương thích API với sản
phẩm đó. LICENSE được giữ bằng tiếng Anh để bảo toàn văn bản pháp lý gốc.
