# Hai preset tìm việc

| File | Role | Level | Địa điểm |
| --- | --- | --- | --- |
| search-config-hang.yaml | Tester, Frontend Developer | Intern, fresher, junior | TP.HCM |
| search-config-dung.yaml | Fullstack, DevOps | Middle, senior | TP.HCM hoặc remote quốc tế làm được từ Việt Nam |

“internal” của Hang được hiểu là intern/internship. Hai file có các cách viết
tương đương của title/level và tên thành phố, như HCM, TP.HCM, Saigon.

Skills được đặt vào preferred_skills để ưu tiên xếp hạng, không bắt một JD phải
đồng thời có mọi công nghệ. Điều này tránh loại job tester không nhắc React,
hoặc job frontend dùng React thay vì Angular. Nếu một skill thực sự bắt buộc,
chuyển nó sang must_have_skills ở đúng role.

- Hang: React, Angular, .NET.
- Dung: Angular, Azure, Cloud, Microservices. DevOps có thêm Docker, Kubernetes,
  CI/CD, Terraform như gợi ý tùy chọn cho phần “...”.
- Cho phép onsite/hybrid/remote. Với Hang, JD vẫn cần bằng chứng địa điểm HCM.
  Với Dung, chỉ remote mới được bỏ giới hạn thành phố/quốc gia của job; vẫn phải
  xác minh được làm từ Việt Nam. Remote US-only/UK-only không được chấp nhận.
- Quyền làm việc và sponsorship vẫn chưa xác định; nơi ở không chứng minh quyền
  làm việc. Job thiếu bằng chứng địa điểm hoặc eligibility sẽ needs_review.
- Các giá trị vận hành kế thừa preset gốc: bài đăng trong 7 ngày, tối đa 30 job
  mới/run, loại unpaid. Hang có thêm loại hợp đồng internship và part-time.
- Hai preset tắt CV handoff/profile gap check cho đến khi trỏ workspace tới đúng
  hồ sơ của từng người. Không tự gán chung profile latex_cv cho Hang và Dung.

## Chọn preset

Chạy tại root seek_job, sao chép MỘT file làm cấu hình hoạt động:

~~~powershell
# Hang
Copy-Item -LiteralPath storage/search-config-hang.yaml -Destination config/search-config.yaml
python -m seek_job validate

# Hoặc Dung
Copy-Item -LiteralPath storage/search-config-dung.yaml -Destination config/search-config.yaml
python -m seek_job validate
~~~

Sau đó yêu cầu “Run job search”. Việc tạo preset không tự chạy tìm việc.

Kết quả/state tách theo người: jobs/hang, runs/hang, state/hang và tương tự cho
dung. Mọi path trong preset resolve từ root seek_job, không phải storage.
Resume vẫn dùng snapshot của run cũ, kể cả sau khi chuyển preset.
