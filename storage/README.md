# Preset theo mục tiêu tìm kiếm

| File | Role | Level | Địa điểm |
| --- | --- | --- | --- |
| search-config-tester-frontend-hcm.yaml | Tester, Frontend Developer | Intern, fresher, junior | TP.HCM |
| search-config-fullstack-devops-hcm-remote.yaml | Fullstack, DevOps | Middle, senior | TP.HCM hoặc remote quốc tế làm được từ Việt Nam |

Preset chỉ chọn tiêu chí tìm việc. Người tạo CV được chọn riêng trong UI từ
`latex_cv/profile/<name>/`; không có mapping giữa search preset và người.
Approval gắn với job trong run. Bạn có thể tạo nhiều batch với profile khác
nhau từ cùng một danh sách job đã duyệt.

Các title/level và tên thành phố có alias như HCM, TP.HCM, Saigon. Skills nằm
trong `preferred_skills`, không yêu cầu một JD phải có đồng thời mọi công nghệ:

- Tester / Frontend: React, Angular, .NET.
- Fullstack / DevOps: Angular, Azure, Cloud, Microservices. DevOps có thêm
  Docker, Kubernetes, CI/CD, Terraform như gợi ý tùy chọn.
- Nếu kỹ năng thực sự bắt buộc, thêm vào `must_have_skills` ở đúng role.
- Tester / Frontend cần bằng chứng địa điểm HCM. Fullstack / DevOps cho phép
  remote quốc tế nếu có bằng chứng làm được từ Việt Nam; không tự chấp nhận
  remote US-only/UK-only.
- Quyền làm việc và sponsorship chưa xác định. Nơi ở không chứng minh quyền
  làm việc. Thiếu bằng chứng hard filter sẽ chuyển `needs_review`.
- Tin trong 7 ngày, tối đa 30 job mới/run, loại unpaid. Tester / Frontend
  cho phép thêm internship và part-time.
- `cv_handoff.enabled` và `profile_gap_check` tắt để search không đánh giá
  theo một người mặc định. Batch CV từ UI dùng profile bạn chọn sau approval.

## Chọn preset

Trên UI, bấm **Run job search** rồi chọn mục tiêu. UI tạo snapshot mà không
ghi đè cấu hình hoạt động `config/search-config.yaml`.

Nếu chạy qua yêu cầu “Run job search” trong agent, có thể chọn cấu hình hoạt
động trước bằng MỘT trong hai lệnh sau:

```powershell
Copy-Item -LiteralPath storage/search-config-tester-frontend-hcm.yaml -Destination config/search-config.yaml
# Hoặc:
Copy-Item -LiteralPath storage/search-config-fullstack-devops-hcm-remote.yaml -Destination config/search-config.yaml
python -m seek_job validate
```

Tên cũ `search-config-hang.yaml` và `search-config-dung.yaml` đã được thay thế.
Các thư mục lưu trữ cũ `jobs/hang`, `runs/hang`, `state/hang` và tương tự cho
`dung` vẫn được dùng để nối tiếp lịch sử và dedup. Đây chỉ là namespace lưu
trữ kế thừa, không chọn người tạo CV. Run cũ giữ snapshot riêng để resume đúng
tiêu chí ban đầu; đổi tên preset không sửa dữ liệu run đã lưu.

Mọi path trong preset resolve từ root seek_job, không phải storage.
