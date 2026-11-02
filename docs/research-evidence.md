# Thu thập bằng chứng và giới hạn kết luận

Tách bốn loại bằng chứng: cấu hình/source, test offline, metadata image và runtime
vật lý. Test mock/Compose config không thay thế một phép đo trên thiết bị thật.

Operator đã xác nhận native single-host PASS riêng trên Raspberry Pi5 và Orange
Pi5Pro: hai clients hoàn thành hai rounds, controller/verifiers PASS. Sai số replay
được báo cho Orange Pi là0.0; không có giá trị cụ thể cho RPi trong báo cáo đã
cung cấp. Những kết quả đó không phải kết quả một job chung giữa hai thiết bị.
Raw physical logs còn cần lưu bổ sung; không dựng lại log, hash hoặc timestamp.

## Bằng chứng cho mỗi lần chạy mới

| Nhóm | Nội dung cần lưu |
|---|---|
| Nhận dạng | Hostname/board/CPU/OS/kernel, local Docker Engine ID và architecture |
| Image | Reference, config ID, RepoDigests, platform manifest, version thực tế |
| Cấu hình | RUN_ID/job/model/bootstrap, profile/bundle ID đã ghim, import từng host |
| Placement | Container ID, labels, mount/network/port trên hai Engines khác nhau |
| Kết nối | Route/interface, host GET và container GET, vị trí đo, timeout/latency |
| Coordination | Fresh SN epoch chung, registry, hai SL sessions, thứ tự rounds |
| Kết quả ML | Logs của cả hai client, dataset seed/samples, global checkpoints |
| Commit | Hai ROUND_COMPLETED, bốn completion receipts, aggregator luân phiên |
| Số học | Sample-weighted FedAvg độc lập, sai số từng peer/round và tolerance |
| Tài nguyên | RAM/CPU theo cadence, dung lượng, network counters, workload nền |

Lưu bên ngoài cấu hình bất biến; dùng thư mục bằng chứng mới theo RUN_ID, giữ
nguyên file gốc, ghi command/host/người thu/thời gian/exit status. Tính SHA-256
trên dữ liệu thực sau khi thu; so hash bản gốc/bản sao. Không đưa dữ liệu live,
token, credentials hoặc `.env` lên GitHub. Nếu cần công bố, tách bản redacted và
ghi rõ phần đã che. Các thư mục `tests/evidence/`, `multihost/evidence/` chỉ dùng
cho máy phát triển và đã bị Git ignore.

## Những số liệu chưa được công cụ tự ghi đầy đủ

- Journal SWOP hiện có sequence/event nhưng không timestamp cho mọi event.
  Không suy thời gian mỗi round từ số thứ tự. Cần observer có monotonic time
  hoặc instrumentation được duyệt, có sai số lấy mẫu rõ ràng.
- Thời gian sống container gồm import/startup và receipt grace; không đồng nhất
  với thời gian training. Clock giữa host cần kiểm tra trước suy luận độ trễ.
- Local update hash không cho biết số byte truyền trên mạng. Phải phân biệt
  tensor bytes, encoded payload, HTTP overhead và retransmissions.
- Host network counters gồm lưu lượng nền. Một snapshot Docker stats không
  chứng minh peak RAM/CPU. Chỉ lấy mẫu exact owned IDs theo lịch đã duyệt.
- Chưa có SN/SL `/metrics`; không tạo số liệu hoặc dashboard giả.

## Xác minh FedAvg

Một host dùng `scripts/verify.sh` và `scripts/verify-fedavg.sh` sau khi cả hai
clients/controller exit0. Verifier replay có tính toán training nhỏ, không upload
model hoặc mở round; không được coi là thao tác chỉ đọc mạng thuần túy.

Hai host cần thu checkpoint/logs từ cả A và B về môi trường phân tích đã duyệt.
Các verifier một host dùng `/deployment/bundle`, `/deployment/READY.json`,
`/checkpoint-a` và `/checkpoint-b`; không chạy chúng mù trên import multi-host có
bố cục khác. Phương án verifier multi-host vẫn là việc riêng cần phê duyệt.

Kiểm tra cùng epoch/job/model/samples, đủ bốn receipts, global apply và carry-over
round1→2. Tái dựng local updates từ params/seed/base nếu không lưu wire updates
thì phải ghi rõ là **replay tái dựng**, không phải update bắt gói độc lập. Tính
`sum(n_i * W_i) / sum(n_i)` bằng float64 rồi cast float32; ngưỡng tham chiếu1e-6.
Báo sai số đo được, không dùng lại giá trị0.0 của thí nghiệm trước hoặc đổi
tolerance chỉ để tạo PASS. Giữ khác biệt kernel/version/input để điều tra.

Không có kết quả multi-host mới được tạo bởi lần dọn repo này. Bốn image đã
publish được phân biệt với Monitoring/Grafana còn bị chặn compliance; thành công
runtime không phải kết luận pháp lý về mọi nghĩa vụ OSS.
