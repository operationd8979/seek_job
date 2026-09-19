# seek_job

## Web UI

~~~powershell
python -m seek_job ui
~~~

Mở **http://127.0.0.1:8765**. Có thể đổi cổng bằng `--port 8768`.
UI dùng Python hiện có, không cần npm build hay cài frontend dependencies.

- **Run job search**: chọn mục tiêu Tester / Frontend, Fullstack / DevOps hoặc
  cấu hình hiện tại. Preset được snapshot
  cho run mới, không ghi đè `config/search-config.yaml`.
- **Review**: tìm/lọc job, đọc JD và evidence, mở tin gốc, duyệt/loại từng job
  hoặc nhiều job. Có form nhập URL hoặc dán JD đầy đủ kèm thời gian capture;
  JSON nâng cao hỗ trợ bổ sung facts với quote theo observation schema.
- **Approval**: lưu quyết định, ghi chú và fingerprint của JD. JD/facts thay đổi
  thì quyết định cũ hết hiệu lực. Job thiếu JD đầy đủ, đã đóng hoặc chưa giải
  quyết trùng lặp không được duyệt tạo CV. Job còn cảnh báo cần xác nhận ngoại
  lệ và lý do; quyết định người dùng không thay đổi kết luận bộ lọc tự động.
  Quyết định gắn với job trong run, không gắn với từng người.
- **History**: giữ snapshot kết quả theo run; xóa chuyển vào thùng rác và có
  thể khôi phục. Không xóa job dùng chung hoặc CV đã xuất. Với run cũ chưa có
  snapshot, UI thông báo đang đọc candidate state hiện có và lưu snapshot khi
  review; không thể tái dựng phiên bản JD chưa từng được lưu.
- **Tạo CV**: chọn các job đã duyệt, profile và template; xác nhận đúng tên
  ứng viên rồi chạy. Nếu không chọn checkbox job, nút dùng tất cả job đã duyệt.
  Số lượng trên nút cập nhật ngay theo các checkbox đã chọn và chỉ đếm job đã duyệt.
  Chọn người tạo CV độc lập với preset tìm việc; cùng một run có thể tạo các
  batch riêng cho nhiều profile. Hồ sơ nằm ở `latex_cv/profile/<name>/`, gồm
  `personal.md` và các file profile đi kèm. Mặc định là `profile/hang`;
  `profile/dung` hiện là bản mẫu cần bổ sung email, điện thoại, bằng chứng và
  summary được duyệt trước khi tạo CV. UI vẫn đọc cấu trúc flat cũ và
  `profiles/<name>/` để tương thích. UI không tạo hay sửa profile.
- **Thu thập JD bổ sung**: Run job search đã tự thu thập JD. Nút này chạy lại
  adapter công khai cho hàng đợi/link mới; không khởi chạy một lượt tìm web mới.
- **Nhập link / JD → Thu thập từ link**: đọc một link công khai, điền công ty,
  vị trí và JD vào bản nháp để kiểm tra. Chỉ lưu khi bấm **Lưu & kiểm tra**, qua CLI
  ingest. Bằng chứng nguồn được giữ nếu bạn không sửa form; form đã sửa được
  lưu như JD thủ công và cần xác nhận đầy đủ. Nguồn cần đăng nhập/CAPTCHA hoặc
  không có dữ liệu đọc được sẽ yêu cầu dán JD thủ công. Không tự duyệt job.
- **Thư viện CV**: trạng thái từng batch/job, log và PDF đã được kiểm tra.
  Mỗi batch giữ bản JD đã duyệt, hash profile và thư mục output riêng trong
  `latex_cv/applications/seek-job/<batch-id>/<job-id>/` (theo `output_root`).
  Nút **Xóa batch** xóa vĩnh viễn file CV, thư mục output, log và metadata của
  batch đã kết thúc; approval và run tìm job được giữ nguyên.

Search và CV là hai thao tác riêng. Nút tạo CV là yêu cầu chạy CV rõ ràng của
người dùng; không tự tạo CV khi search xong hoặc khi duyệt job. Luồng UI dùng
approval batch riêng, nên vẫn hoạt động với preset tắt `cv_handoff.enabled`;
manifest tự động `cv-ready.json` giữ nguyên các điều kiện nghiêm ngặt của CLI.

### Runner và kiểm tra

Máy cần có **Codex CLI** trong PATH và đã đăng nhập (`codex login`) để tìm web
và viết kế hoạch CV. UI chạy `codex exec --json` bằng argv/stdin, sandbox
`workspace-write`, live search chỉ bật cho search. Không yêu cầu API key trong
UI và không đọc/lưu credential. Xem [tài liệu chế độ non-interactive](https://learn.chatgpt.com/docs/non-interactive-mode).
Đặt model và reasoning chung cho cả tìm job lẫn tạo CV trong
[`config/agent-config.yaml`](config/agent-config.yaml). Mặc định là
`gpt-5.6-sol` và `medium`; UI đọc lại file khi bắt đầu mỗi operation, nên
không cần đổi model mặc định của Codex CLI. `python -m seek_job validate`
kiểm tra file cấu hình. Nếu agent thoát với mã lỗi, batch CV được đánh dấu
failed; log giữ thông báo lỗi gốc.

Mỗi workspace chạy một pipeline tại một thời điểm; có log và nút Dừng. Windows
Job Object dọn cây tiến trình khi server dừng. Sau gián đoạn, run cũ vẫn có thể
Resume với ngân sách còn lại. Không có browser takeover trong runner này;
nguồn cần đăng nhập giữ blocked để nhập JD thủ công. Không tự Apply/gửi hồ sơ.

CV runner dùng skill của workspace `latex_cv`, tạo CV tiếng Anh tối đa hai trang.
Mỗi CV cần ít nhất hai project khác nhau từ profile; ưu tiên mức độ phù hợp,
rồi đến kỹ năng có thể áp dụng cho job. Bắt đầu với hai project phù hợp nhất; chỉ thêm
khi còn đủ chỗ. Nếu vượt hai trang, rút gọn nội dung ít liên quan rồi render/build lại.
Không thu nhỏ chữ hay nén giãn dòng để ép trang. Link dùng nhãn ngắn nhưng giữ URL đầy đủ.
Sau agent, runner chạy lại renderer và `build_and_validate.py` để kiểm tra
profile evidence, template và PDF; chỉ công bố link PDF khi thành công. Máy cần
các công cụ build mà `latex_cv` yêu cầu (ví dụ Tectonic). Build lỗi được giữ ở
batch/log, không đánh dấu thành công. Các bài kiểm tra không chạy agent trả phí,
live discovery hay tạo CV thật.
Kiểm tra UI trên Edge headless với dữ liệu giả lập: `node tests/frontend_smoke.cjs`
(cần Node.js 22+ và Microsoft Edge; có thể đặt `EDGE_BIN`). Test không chạy agent
hay gọi nguồn tuyển dụng thật.

Web server chỉ bind `127.0.0.1`, kiểm tra Host/Origin/CSRF, escape nội dung JD.
Mọi mutation đi qua CLI `ui-action` hoặc `ingest`. File UI nằm dưới `state/ui/`,
quyết định nằm trong `reviews.json` của từng run. Không expose server qua LAN
hay reverse proxy; đây là workspace cá nhân, không phải dịch vụ nhiều người dùng.

Pipeline tìm việc thủ công trên Windows. Code quản lý dữ liệu, bằng chứng,
dedup, bộ lọc và output; agent sử dụng web search/browser thực sự có sẵn để
khám phá nguồn và đọc hiểu JD. Không tự chạy theo lịch, nộp hồ sơ hoặc tạo CV.

## Bắt đầu

Python 3.11+:

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m seek_job validate
~~~

Các ví dụ tiếp theo dùng python; thay bằng đường dẫn interpreter trong .venv
nếu chưa activate. Không cần PowerShell execution-policy changes để gọi trực
tiếp python.exe.

Chỉ có hai dependency trực tiếp:

- PyYAML: đọc YAML an toàn, báo duplicate keys và serialize front matter.
- jsonschema: kiểm tra config, observation, metadata và CV manifest theo schema.

HTTP, JSON, hashing, locking, HTML parsing và tests dùng Python standard library.
Không cần API key, plugin mới hoặc dịch vụ trả phí. Không có database.
requirements.txt giới hạn major version; không cài gì trong latex_cv.

## Chỉnh tiêu chí

Sửa config/search-config.yaml. Role/level/skills hiện là VÍ DỤ .NET, không phải
xác nhận năng lực ứng viên. Config không tự đồng bộ với latex_cv/profile.

- Mỗi search profile có role variants, level, tất cả must_have_skills và
  preferred_skills. Pass một profile là đủ, không tạo nhiều record cho một job.
- Remote nằm ở work_modes. job_countries là quốc gia job; work_from_country là
  nơi bạn dự định làm việc. Một remote UK không tự cho phép làm từ VN.
- work_from_country, quyền làm việc, sponsorship và timezone chưa biết được
  giữ unknown. Có thể thêm geography.work_from_city để kiểm tra onsite/hybrid.
- null khác false và khác danh sách rỗng. Không suy ra quyền làm việc từ quốc tịch
  hay nơi cư trú. Thiếu dữ liệu hard filter mặc định needs_review.
- must_have_skills là kỹ năng bạn muốn có trong JD, không có nghĩa profile chứng
  minh bạn đã có kỹ năng đó. Skill alias không nâng tier ứng viên.
- Ngày đăng không biết chuyển needs_review hoặc reject theo policy. Không dùng
  ngày sửa posting/ngày search engine crawl thay ngày đăng.
- company_boards hỗ trợ Greenhouse và Lever, ví dụ bên dưới. Các nguồn khác được
  khám phá qua agent và đọc qua public page hoặc browser/manual capture.
- Một board có thể thêm region: eu cho Lever EU.
- CV workspace mặc định ../latex_cv; path output của seek_job ở trong project.
  Config CV và skill CV được resolve trong workspace riêng.

~~~yaml
company_boards:
  - source: greenhouse
    company: "<tên công ty thật>"
    board: "<board token thật>"
  - source: lever
    company: "<tên công ty thật>"
    board: "<site name thật>"
    region: global
~~~

Chỉ thêm board đã xác minh, không dùng placeholder để tìm. Board mới do agent
tìm được xuất như đề xuất trong ghi chú task; không tự thay config.

Các preset theo mục tiêu tìm kiếm nằm trong [storage](storage/README.md). geography.job_cities
giới hạn thành phố, city_aliases khai báo tên tương đương. Không tìm được bằng
chứng thành phố thì needs_review, không tự pass. allow_international_remote:
true cho phép job remote ngoài địa điểm mục tiêu, nhưng vẫn kiểm tra remote
eligibility theo work_from_country; nó không cho phép bỏ qua giới hạn US/UK-only.
Ba trường này tùy chọn; config cũ giữ nguyên cách hoạt động.

## Chạy trong Codex

Sau khi chỉnh tiêu chí, nói:

~~~text
Run job search
~~~

AGENTS.md hướng dẫn agent validate, tạo run, thực hiện query, lấy JD, nhập bằng
chứng, lọc và xuất báo cáo. Chỉ collect và các công cụ web/browser của agent
truy cập mạng. Lệnh start/resume/ingest/finish chỉ xử lý file local.

CLI không tự có web search hoặc browser. Agent phải thực hiện các task search
được ghi trong checkpoint. --web-search-available và --browser-available chỉ
khai báo công cụ thực có, không cài hay mở công cụ. Phiên triển khai ban đầu
có web search nhưng không có browser takeover.

CLI tương đương cho một run:

~~~powershell
python -m seek_job validate
python -m seek_job start --web-search-available
python -m seek_job status --run <run-id>
# CHỈ chạy khi bạn muốn tìm/fetch job thực tế:
python -m seek_job collect --run <run-id>
python -m seek_job finish --run <run-id>
~~~

Với config mặc định company_boards rỗng, collect không tự khám phá toàn thị
trường. Agent thực hiện queries, import URL, rồi collect để fetch các URL đó.
Chạy start/finish đơn thuần sẽ báo coverage partial, không báo đã tìm job.

## Nhập link, JD và bằng chứng

Tạo file UTF-8 trong inbox/, theo schemas/observation.schema.json. Một file chứa
một object hoặc array các object. Ví dụ discovery (thay URL trước khi dùng):

~~~json
{
  "schemaVersion": 1,
  "source": "company_career_pages",
  "url": "https://company.example/careers/job-id"
}
~~~

~~~powershell
python -m seek_job ingest --run <run-id> --input inbox/discovered.json
python -m seek_job collect --run <run-id>
python -m seek_job export --run <run-id> --job-id <job-id> --out inbox/review-001.json
~~~

Export tạo observation với JD và source content hiện có, không ghi đè file cũ.
Agent đọc và bổ sung facts + evidence vào file rồi ingest lại. jobId chỉ rõ
candidate đang được bổ sung, giữ nguyên folder/identity.

Các trường quan trọng:

| Trường | Ý nghĩa |
| --- | --- |
| description / descriptionFile | Toàn bộ JD hoặc file UTF-8 tương đối với observation |
| sourceContent / sourceContentFile | Nội dung public gốc dùng đối chiếu; không chứa session/headers |
| descriptionStatus | complete, partial hoặc unavailable |
| completenessEvidence | Lý do đã lấy đủ đúng posting; bắt buộc khi complete |
| capturedAt | ISO 8601 có timezone; giữ ngày lấy bản nội dung thực tế |
| availabilityStatus | open, closed hoặc unknown |
| availabilityCheckedAt | Lúc thực sự kiểm tra còn tuyển, không phải lúc import lại |
| facts | locations, jobCountries, workMode, employmentType, level, datePosted, remoteScope, eligibleCountries, sponsorship... |
| evidence | Tên field → câu nguyên văn có trong sourceContent/JD |
| roleMatches | profile ID → result pass/fail và quote khi title variant cần đọc ngữ nghĩa |
| linkedAliases | Alias nguồn + quote chứa URL trực tiếp chứng minh cùng posting |

Không khẳng định complete từ snippet hay HTTP 200. Một JD ngắn có thể đủ; nguồn
không công bố benefits/salary thì không bịa thêm. Parser chỉ kiểm tra quote có
tồn tại, không thể chứng minh mọi suy luận AI đúng: agent/người dùng chịu trách
nhiệm giá trị facts thực sự được quote hỗ trợ. Thiếu bằng chứng thì để null.

Với JD người dùng cung cấp, dùng source: manual, descriptionKind: user_supplied.
URL có thể null; nếu không có URL, cung cấp tenant/sourceJobId ổn định hoặc jobId
khi cập nhật để không tạo candidate mới mỗi lần nhập.

Đường dẫn descriptionFile/sourceContentFile phải nằm trong thư mục observation.
Chỉ lưu nội dung public; không lưu cookie/token hoặc toàn bộ browser network log.
File observation được validate cả batch trước khi commit.

Ghi kết quả một query đã thực hiện:

~~~powershell
python -m seek_job task --run <run-id> --task-id task-001 --status done --note "Searched actual query; imported resulting URLs" --pages 1 --found 5 --active-seconds 25
~~~

Ghi thời gian thực tế ngoài CLI, không gồm chờ người dùng login. Budget active time
là thời gian code chạy cộng active-seconds được báo từ công cụ ngoài. CLI không
đo được thời gian agent nếu agent không khai báo. Query done cần pages >= 1 và
active-seconds > 0. Nguồn bị chặn dùng status blocked và note cụ thể.

## Login, CAPTCHA, resume

Trước khi chờ login, ingest observation có jobId, URL và blockedReason:
auth_required, captcha, browser_required, rate_limited, fetch_failed hoặc not_found.
Candidate/state được lưu, các nguồn độc lập tiếp tục.

Nếu có browser takeover, bạn tự đăng nhập/2FA/CAPTCHA trong browser. Agent chỉ
tiếp tục sau khi bạn hoàn tất và kiểm tra session thực tế. Không gửi password,
cookie hoặc OTP trong chat. Nếu không có browser, cung cấp JD đã copy thủ công.
Không lưu browser profile trong repository hoặc thư mục SYNC.

~~~text
Resume job search <run-id>
~~~

~~~powershell
python -m seek_job resume --run <run-id> --web-search-available
python -m seek_job collect --run <run-id> --job-id <job-id>
~~~

Resume dùng config.snapshot.yaml và ngân sách cũ. Config hiện tại đổi hoặc sai
không thay snapshot; run mới cần config hiện tại hợp lệ. Task bị blocked không tự
retry vô hạn. Sau cooldown, chuyển task sang pending rồi collect; browser/manual
capture có thể nhập qua ingest. HTTP Retry-After được tôn trọng qua checkpoint.
Không tự retry LinkedIn qua HTTP; ưu tiên link career/ATS chính thức.

## Identity, match và xếp hạng

- jobId được tạo một lần; folder không chứa ngày và không đổi khi title/location
  đổi. Namespace source + tenant + sourceJobId ngăn trùng số ID giữa công ty.
- Giữ source URL và alias URL normalized, chỉ bỏ tracking đã biết an toàn. Giữ
  tham số job ID, tenant, locale và fragment routing.
- Direct source ID/URL/link mới được merge tự động. Title/location/JD gần nhau
  tạo possible_duplicate, không gộp. ID khác trong cùng board là posting riêng.
- Khi merge record cũ, giữ mergedInto redirect và lịch sử. Explicit distinct có
  bằng chứng hoặc xác nhận người dùng: distinct --run ... --job-id A --job-id B
  --note "...". Không dùng distinct để bỏ qua nghi vấn tùy tiện.
- Mỗi điều kiện pass/fail/unknown có quote và reasonCode. Điểm cao không bù hard
  filter. "Mentor junior developers" không làm loại role senior.
- Một skill vắng khỏi JD là chưa có bằng chứng, không tự coi có/không có năng lực.
- Điểm mỗi profile = preferredSkillCoverage × weight + freshness × weight.
  Coverage = số kỹ năng preferred khớp / tổng preferred; 0 khi danh sách rỗng.
  Freshness = max(0, min(1, 1 - tuổi bài đăng theo ngày / posted_within_days)).
  Ngày không biết cho freshness 0, không đoán. Chỉ profile accepted được tính vào
  matchScore; lấy điểm cao nhất và sort giảm dần, jobId tăng dần khi bằng điểm.
- descriptionStatus, matchStatus, availabilityStatus, identityStatus và runAction
  là các trục riêng. Các số đếm trong báo cáo có thể chồng lấp.
- 403/429/timeout/404 không tự đồng nghĩa closed. Chỉ bằng chứng job đóng mới
  đặt closed; unavailable/stale không vào manifest mặc định.

## Output và CV handoff

~~~text
config/             Tiêu chí YAML
schemas/            JSON Schemas xuất từ seek_job/contracts.py
state/              Accepted index, candidate queue, writer lock, redo journals
jobs/<stable-name>/ metadata.json, summary.md, job-description.md, source-content.txt
                    .committed.json; revisions/ khi JD thay đổi
runs/<run-id>/      Snapshot, checkpoint, results.json, báo cáo Markdown,
                    cv-ready.json, cv-handoff.md
prompts/            Prompt run/resume/CV handoff
artifacts/          Dry runs synthetic, tách hoàn toàn dữ liệu thật
~~~

accepted cần JD complete, hard filters pass, identity resolved và posting open
được kiểm tra đủ mới. Job đã xuất rồi bị đóng/rejected vẫn giữ folder và lịch sử.
Job incomplete/needs_review/rejected mới chỉ lưu candidate state, không xuất JD
vào jobs/. max_accepted_jobs_per_run giới hạn folder mới, job vượt quota vẫn ở
queue; accepted cũ đủ điều kiện vẫn vào manifest sau re-evaluation.

JD giữ ngôn ngữ nguồn. Hash SHA-256 tính trên phần thân JD UTF-8: CRLF/CR → LF,
bỏ space/tab cuối mỗi dòng, bỏ dòng trống đầu/cuối, thêm đúng một LF cuối khi
body không rỗng. Không hash front matter. Quy tắc chung ở body_normalize.

Manifest dùng path tương đối từ thư mục chứa manifest. CV workspace là ngoại lệ
có chủ đích ngoài seek root; path JD/metadata không được thoát seek root.

~~~powershell
python -m seek_job validate-handoff --manifest runs/<run-id>/cv-ready.json --job-id <job-id>
~~~

Sau đó ở một lượt riêng, yêu cầu tạo CV theo runs/<run-id>/cv-handoff.md và chọn
jobId. Handoff chỉ đọc JD local đã validate; không fetch lại vì có sourceUrl.
Skill latex-cv-tailor đọc profile, tạo application có ngày/version và lưu JD ở
raw/job.md; provenance tại raw/seek-job-source.json. Seek pipeline không chạy
script CV, không sửa latex_cv, không tự tạo cover letter.

Hiện latex_cv chưa có CLI nhận manifest; đây là adapter bằng prompt/file. Handoff
kiểm tra schema, path, hash, metadata/front matter, trạng thái và độ mới. JD hoặc
metadata đã đổi làm manifest cũ bị từ chối. Profile/template thay đổi có thể cần
CV mới dù hash JD giữ nguyên. Manifest không đánh dấu "đã tạo CV".

Profile gap check chỉ đọc skills/preferences theo config CV; không đọc contact
fields để đưa vào log. Tier absent/unverified/familiar/working được ghi chú, không
tự nâng lên professional và không tự đổi search criteria.

## Recovery và giới hạn đồng bộ

~~~powershell
python -m seek_job recover
python -m seek_job rebuild-index
~~~

Một OS lock dùng chung cho mọi writer của project; process chết thì OS nhả lock.
File lock còn đó không có nghĩa đang khóa, không tự xóa file khi writer đang chạy.
Chỉ hỗ trợ một máy, không dùng SYNC làm distributed locking. Phát hiện tên
conflicted copies ở state thì dừng ghi, báo người dùng giải quyết.

Mỗi commit ghi durable redo journal trong state/staging, ghi file tạm rồi replace,
cập nhật bộ job/index/checkpoint/report, sau cùng ghi receipt completed. Sau crash,
lệnh ghi kế tiếp replay toàn bộ journal chưa hoàn tất trước khi đọc dữ liệu.
Đây là roll-forward recovery, không phải filesystem transaction atomic cho mọi
reader: không đọc/generate CV trong lúc writer chạy; validate-handoff từ chối khi
có journal pending và kiểm tra hash. Không đọc trực tiếp index giữa một commit.

Rebuild chỉ scan jobs/*/metadata.json có commit marker, kiểm tra schema/identity,
không chọn âm thầm giữa aliases xung đột. Index cũ được giữ backup. Rebuild không
khôi phục candidate chưa accepted/checkpoint nếu các file đó mất, không tự sửa
state hỏng thành rỗng. Sao lưu cả jobs, state và runs khi cần.

## Kiểm tra offline

~~~powershell
python -m unittest discover -s tests -v
python -m seek_job dry-run
python scripts/validate-config.py
~~~

Dry run tạo workspace synthetic mới dưới artifacts/, chặn network ở runtime,
kiểm tra accepted/incomplete, dedup, manifest và rebuild. Không ghi jobs/runs/state
thật hoặc latex_cv. Test suite còn fault-inject gián đoạn ghi, cross-source aliases,
stale manifests, geo/skills, blocked sources và mocked ATS responses.

Helper scripts rebuild-index.py và validate-handoff.py tương đương CLI. Với root
khác, dùng global option trước subcommand: python -m seek_job --root "<path>" validate.

## Giới hạn và tài liệu adapter

- Chưa xác minh live search trong bước triển khai. Fixture pass không chứng minh
  website bên ngoài đang truy cập được.
- Greenhouse/Lever là API theo company board, không phải global job-search API.
  Lever thường không có datePosted trong contract public nên cần review nguồn
  bổ sung; không dùng created/updated time thay thế tùy tiện.
- Generic HTML adapter chỉ đọc một JobPosting JSON-LD, so với visible HTML. Trang
  JS động, nhiều posting, auth/CAPTCHA hoặc JD không rõ phải qua browser/manual.
  Không có adapter private Workday endpoint hay tự động đăng nhập.
- Native adapters không suy diễn geography/skills sâu từ văn bản; agent bổ sung
  evidence trước khi accepted. Nhiều needs_review là kết quả hợp lệ.
- LinkedIn là discovery/manual, không phải phụ thuộc bắt buộc. Quy định/biện pháp
  chống automation có thể hạn chế truy cập; không vượt chặn.
- Nếu scheduling sau này, chỉ schedule công việc không cần tương tác; auth/manual
  giữ trong queue. Giai đoạn này không tạo Scheduled Task.

Tài liệu nguồn dùng khi triển khai:
[Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html),
[Lever Postings API](https://github.com/lever/postings-api),
[Schema.org JobPosting](https://schema.org/JobPosting).
