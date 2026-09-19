'use strict';
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const httpUrl = (u) => /^https?:\/\//i.test(String(u ?? '')) ? String(u) : '';
const labels = {partial:'Cần bổ sung',complete:'Hoàn tất',completed:'Hoàn tất',paused:'Đã lưu',running:'Đang chạy',queued:'Đang chờ',approved:'Đã duyệt',rejected:'Đã loại',pending:'Chờ duyệt',needs_review:'Cần kiểm tra',accepted:'Đạt bộ lọc',stale:'Cần duyệt lại',failed:'Lỗi',cancelled:'Đã dừng',interrupted:'Gián đoạn',closed:'Đã đóng',open:'Còn tuyển',unknown:'Chưa rõ'};
const badge = s => '<span class="badge '+esc(s)+'">'+esc(labels[s]||s)+'</span>';
const date = s => s ? new Date(s).toLocaleString('vi-VN',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
const btn = (text, action, cls='', attrs='') => '<button class="'+cls+'" data-action="'+action+'" '+attrs+'>'+text+'</button>';
const state = {data:null,view:'runs',run:null,job:null,selected:new Set(),filter:'all',search:'',tab:'jd',busy:false};
let toastTimer, polling=false;
function toast(message,error=false){const t=$('#toast');t.textContent=message;t.className=error?'error':'';t.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.hidden=true,error?9000:4000);}
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.data.csrf},body:JSON.stringify(body)}:{});const data=await r.json();if(!r.ok)throw Error(data.error||'Không thể hoàn tất thao tác.');return data;}
function modal(title,body,foot){$('#modal-content').innerHTML='<div class="modal-head"><h2>'+title+'</h2>'+btn('✕','close','quiet','aria-label="Đóng"')+'</div><div class="modal-body">'+body+'</div><div class="modal-foot">'+btn('Hủy','close')+foot+'</div>';$('#modal').showModal();}
function close(){ if(!state.fetching)$('#modal').close(); }
function active(){return state.data.operations.find(o=>['queued','running','cancelling'].includes(o.status));}
function heading(kicker,title,sub,action=''){return '<div class="page-heading"><div><div class="eyebrow">'+kicker+'</div><h1>'+title+'</h1><p>'+sub+'</p></div>'+action+'</div>';}
function operationBanner(){
 const op=active() || [...state.data.operations].sort((a,b)=>b.createdAt.localeCompare(a.createdAt))[0];
 if(!op)return '';
 return '<div class="operation"><div><strong>'+badge(op.status)+' &nbsp; '+(op.kind==='cv'?'Tạo CV theo job đã duyệt':'Tìm kiếm & thu thập job')+'</strong><p>'+esc(op.runId)+(op.error?' · '+esc(op.error):'')+'</p></div><div class="tools">'+btn('Xem log','log','','data-id="'+op.id+'"')+(active()?btn('Dừng','cancel','danger'):'')+'</div></div>';
}
function metric(label,value,note,icon){return '<div class="metric"><div class="metric-label">'+label+'<i>'+icon+'</i></div><div class="metric-value">'+value+'</div><small>'+note+'</small></div>';}
function flow(step=1){return '<div class="flow">'+[['Tìm kiếm','Chọn preset & khám phá'],['Review JD','Đọc nội dung & bằng chứng'],['Approval','Chọn cơ hội phù hợp'],['Tạo CV','Tailor với latex_cv']].map((s,i)=>'<div class="flow-step '+(i+1===step?'active':'')+'"><b>0'+(i+1)+'</b><div><strong>'+s[0]+'</strong><small>'+s[1]+'</small></div></div>').join('')+'</div>';}
function summaryLabel(label){return label.replaceAll('_',' ');}
function renderRuns(){
 const runs=state.data.runs;
 const count=runs.reduce((n,r)=>n+(r.counts.uniqueCandidatesThisRun||0),0);
 const approved=runs.reduce((n,r)=>n+r.approved,0);
 const pdfCount=state.data.batches.flatMap(b=>b.results).reduce((n,r)=>n+(r.pdfs?.length||0),0);
 $('#main').innerHTML=heading('MAKE YOUR NEXT MOVE','Cơ hội tiếp theo, bắt đầu từ đây.','Quản lý tìm việc và CV trong cùng một workspace.',btn('＋ Run job search','new-run','primary',active()?'disabled':''))+
 '<div class="metrics">'+metric('Lượt tìm kiếm',runs.length,'Lịch sử luôn được lưu','↗')+metric('Tin đã thu thập',count,'Tổng theo từng run','▦')+metric('Job đã duyệt',approved,'Quyết định của bạn','✓')+metric('CV hoàn tất',pdfCount,'Đã qua kiểm tra PDF','▤')+'</div>'+flow()+operationBanner()+
 '<section class="panel"><div class="panel-heading"><div><h2>Lịch sử tìm kiếm <span class="count-pill">'+runs.length+'</span></h2><p>Mở một run để xem tiến độ và duyệt các job phù hợp.</p></div><div class="tools"><input class="search" id="run-search" placeholder="Tìm run hoặc tên preset…" aria-label="Tìm run"></div></div><div id="run-table"></div></section>';
 renderRunTable('');
}
function renderRunTable(q){
 const runs=state.data.runs.filter(r=>(r.id+' '+r.label).toLowerCase().includes(q.toLowerCase()));
 $('#run-table').innerHTML=runs.length?'<div class="mobile-scroll"><table><thead><tr><th>Run / ứng viên</th><th>Trạng thái</th><th>Tiến độ</th><th>Thu thập</th><th>Đã duyệt</th><th></th></tr></thead><tbody>'+runs.map(r=>'<tr><td><span class="run-icon">⌕</span><strong>'+esc(summaryLabel(r.label))+'</strong><small>'+date(r.startedAt)+' · '+esc(r.id.slice(-6))+'</small></td><td>'+badge(r.status)+'</td><td><small>'+r.tasksDone+' / '+r.tasksTotal+' truy vấn</small><progress class="progress" value="'+r.tasksDone+'" max="'+(r.tasksTotal||1)+'"></progress></td><td>'+ (r.counts.uniqueCandidatesThisRun||0)+' tin</td><td>'+r.approved+'</td><td>'+btn('Review →','open-run','','data-id="'+r.id+'"')+' '+btn('⌫','delete-run','quiet danger','data-id="'+r.id+'" aria-label="Xóa run"')+'</td></tr>').join('')+'</tbody></table></div><div class="table-footer">Dữ liệu từng run được giữ riêng · Preset mới không thay đổi lịch sử cũ</div>':'<div class="empty"><div class="empty-icon">⌕</div><h3>Chưa có run phù hợp</h3><p>Chọn Hang hoặc Dung để bắt đầu một lượt tìm kiếm mới.</p>'+btn('Tạo lượt tìm kiếm','new-run','primary')+'</div>';
}
async function openRun(id){
 state.run=await api('/api/run?id='+encodeURIComponent(id));state.view='review';state.job=state.run.jobs[0]?.jobId;state.selected.clear();state.filter='all';state.search='';render();
}
function renderReview(){
 const d=state.run,cp=d.checkpoint;const approved=d.jobs.filter(j=>j.review.status==='approved');
 $('#main').innerHTML=heading('SEARCH → REVIEW → APPROVE','Review cơ hội','Run '+esc(cp.runId)+' · '+date(cp.startedAt),btn('← Lịch sử','back','quiet'))+flow(approved.length?3:2)+operationBanner()+
 '<div class="panel"><div class="panel-heading"><div><h2>'+esc(summaryLabel(d.config.search_profiles.map(p=>p.id).join(' / ')))+' <span class="count-pill">'+d.jobs.length+' jobs</span></h2><p>'+cp.tasks.filter(t=>t.status==='done').length+'/'+cp.tasks.length+' truy vấn · '+approved.length+' đã duyệt</p></div><div class="tools">'+btn('Tiếp tục tìm','resume','',active()?'disabled':'')+btn('Thu thập JD bổ sung','collect','',(active()?'disabled ':'')+'title="Run job search đã tự thu thập JD. Dùng nút này cho link mới hoặc JD còn thiếu."')+btn('Nhập link / JD','import','',active()?'disabled':'')+btn('Tạo CV →','cv-dialog','primary','id="create-cv"')+'</div></div></div>'+
 (d.legacySnapshot?'<div class="notice info">Run cũ chưa có snapshot JD riêng. Màn hình đang đọc dữ liệu đã lưu hiện có; lần review đầu sẽ lưu phiên bản này vào lịch sử run.</div>':'')+
 '<div class="tools"><input id="job-search" class="search" placeholder="Tìm công ty hoặc vị trí…" aria-label="Tìm job"><select id="job-filter" aria-label="Lọc job"><option value="all">Tất cả job</option><option value="pending">Chờ duyệt</option><option value="approved">Đã duyệt</option><option value="rejected">Đã loại</option><option value="needs_review">Cần kiểm tra</option><option value="blocked">Thiếu JD / bị chặn</option></select>'+btn('Chọn tất cả đang hiển thị','select-visible','quiet')+btn('Cấu hình & tiến độ','config','quiet')+'</div><br><div id="selection"></div><div class="review-layout"><section class="panel"><div class="job-list" id="job-list"></div></section><section class="panel"><div class="job-detail" id="job-detail"></div></section></div>';
 $('#job-search').value=state.search;$('#job-filter').value=state.filter;renderJobs();renderJob();renderSelection();
}
function filteredJobs(){return state.run.jobs.filter(j=>(j.jobTitle+' '+j.company).toLowerCase().includes(state.search.toLowerCase())&&(state.filter==='all'||(state.filter==='blocked'&&j.descriptionStatus!=='complete')||(state.filter==='needs_review'&&j.matchStatus==='needs_review')||j.review.status===state.filter));}
function renderJobs(){
 $('#job-list').innerHTML=filteredJobs().map(j=>'<div class="job-row '+(j.jobId===state.job?'active':'')+'" data-job="'+j.jobId+'" role="button" tabindex="0"><input type="checkbox" aria-label="Chọn '+esc(j.jobTitle)+'" data-select="'+j.jobId+'" '+(state.selected.has(j.jobId)?'checked':'')+'><div><h3>'+esc(j.jobTitle||'Chưa có tiêu đề')+'</h3><p>'+esc(j.company||'Chưa rõ công ty')+' · '+esc(j.locations?.join(', ')||'Chưa rõ địa điểm')+'</p><div class="badges">'+badge(j.review.status)+badge(j.matchStatus)+(j.descriptionStatus!=='complete'?'<span class="badge">Thiếu JD</span>':'')+'</div></div></div>').join('')||'<div class="empty"><h3>Không có job phù hợp bộ lọc</h3><p>Thử đổi bộ lọc hoặc nhập link tuyển dụng.</p></div>';
}
function cvCandidates(){return state.run.jobs.filter(j=>j.review.status==='approved'&&(!state.selected.size||state.selected.has(j.jobId)));}
function renderSelection(){
 const n=state.selected.size, count=cvCandidates().length, button=$('#create-cv');
 if(button){button.textContent=(n?'Tạo CV đã chọn (':'Tạo CV tất cả đã duyệt (')+count+') →';button.disabled=!count||!!active();}
 $('#selection').innerHTML=n?'<div class="selection-bar"><span>Đã chọn <strong>'+n+' job</strong></span><div class="tools">'+btn('Bỏ chọn','clear-selection','quiet')+btn('Loại đã chọn','reject-many')+btn('Duyệt đã chọn','approve-many','primary')+'</div></div>':'';
}
function currentJob(){return state.run.jobs.find(j=>j.jobId===state.job);}
function renderJob(){
 const j=currentJob();if(!j){$('#job-detail').innerHTML='<div class="empty"><h3>Chọn một job để bắt đầu review</h3></div>';return;}
 const facts=[['Địa điểm',j.locations?.join(', ')],['Hình thức',j.workMode],['Cấp bậc',j.level],['Ngày đăng',j.datePosted],['Tuyển dụng',labels[j.availabilityStatus]],['JD',j.descriptionStatus==='complete'?'Đầy đủ':j.descriptionStatus]];
 const source=j.sourceUrl&&/^https?:\/\//i.test(j.sourceUrl)?'<a href="'+esc(j.sourceUrl)+'" target="_blank" rel="noopener noreferrer">Mở tin gốc ↗</a>':'Không có link nguồn';
 $('#job-detail').innerHTML='<div class="detail-top"><span class="eyebrow">JOB DETAILS</span>'+source+'</div><h2>'+esc(j.jobTitle)+'</h2><p class="company">'+esc(j.company)+' &nbsp; '+badge(j.review.status)+'</p><div class="facts">'+facts.map(([k,v])=>'<div><small>'+k+'</small><span>'+esc(v||'Chưa xác minh')+'</span></div>').join('')+'</div>'+
 (j.approvalBlocks.length?'<div class="notice">'+j.approvalBlocks.map(esc).join('<br>')+'</div>':'')+
 (j.approvalWarnings.length?'<div class="notice info">'+j.approvalWarnings.map(esc).join('<br>')+'</div>':'')+
 '<div class="tabs">'+[['jd','Nội dung JD'],['evidence','Bằng chứng'],['history','Lịch sử duyệt']].map(([id,title])=>btn(title,'tab',state.tab===id?'active':'','data-id="'+id+'"')).join('')+'</div><div id="job-tab"></div><div class="review-actions">'+btn('Duyệt job','approve','primary',j.approvalBlocks.length||active()?'disabled':'')+btn('Loại','reject','',active()?'disabled':'')+btn('Bỏ quyết định','reset','quiet',active()?'disabled':'')+btn('Bổ sung JD','edit-job','quiet',active()?'disabled':'')+'</div>';
 if(state.tab==='jd')$('#job-tab').innerHTML='<pre class="jd">'+esc(j._description||'Chưa lấy được JD đầy đủ. Mở tin gốc và nhập bản JD đã capture để tiếp tục review.')+'</pre>';
 if(state.tab==='evidence')$('#job-tab').innerHTML='<pre class="pre-json">'+esc(JSON.stringify({evidence:j.evidence,roleMatches:j.roleMatches,reviewNotes:j.reviewNotes,reasonCodes:j.reasonCodes,profileGaps:j.profileGaps},null,2))+'</pre>';
 if(state.tab==='history')$('#job-tab').innerHTML=state.run.reviewEvents.filter(e=>e.jobId===j.jobId).reverse().map(e=>'<div class="history-line">'+badge(e.status)+'<small>'+date(e.at)+'</small>'+esc(e.note||'Không có ghi chú')+'</div>').join('')||'<p class="muted">Chưa có quyết định duyệt.</p>';
}
function newRun(){
 const opts=state.data.presets.map(p=>'<option value="'+p.id+'" '+(p.id==='tester-frontend-hcm'?'selected':'')+'>'+esc(p.name)+'</option>').join('');
 modal('Bắt đầu một lượt tìm kiếm','<p>Chọn bộ tiêu chí. Tiến độ, kết quả và các nguồn bị chặn sẽ được lưu trong run mới.</p><label class="field">Mục tiêu tìm kiếm<select id="preset">'+opts+'</select></label><div id="preset-info"></div><div class="notice info">Tìm kiếm dùng Codex CLI đã đăng nhập trên máy này.'+(state.data.agent?' Model: '+esc(state.data.agent.model)+' · reasoning '+esc(state.data.agent.reasoning_effort)+'.':'')+' Bạn có thể theo dõi log và dừng khi cần.</div>',btn('Run job search →','start-search','primary',state.data.capabilities.codex?'':'disabled'));
 presetInfo();
}
function presetInfo(){const p=state.data.presets.find(p=>p.id===$('#preset').value),c=p.config;$('#preset-info').innerHTML='<div class="preset-info"><h3>'+esc(p.name)+'</h3><p>'+esc(c.search_profiles.flatMap(p=>p.target_roles).join(' · '))+'</p><div class="chips">'+[...(c.geography.job_cities||c.geography.job_countries),...c.work_modes,'Tin trong '+c.freshness.posted_within_days+' ngày'].map(v=>'<span class="badge">'+esc(v)+'</span>').join('')+'</div></div>';}
function reviewDialog(status,ids){
 const jobs=state.run.jobs.filter(j=>ids.includes(j.jobId));if(!jobs.length)return;
 const blocked=status==='approved'?jobs.flatMap(j=>j.approvalBlocks):[];
 const warns=status==='approved'?jobs.flatMap(j=>j.approvalWarnings):[];
 state.reviewRequest={status,jobIds:ids,fingerprints:Object.fromEntries(jobs.map(j=>[j.jobId,j.fingerprint]))};
 modal(status==='approved'?'Duyệt '+jobs.length+' job':status==='rejected'?'Loại '+jobs.length+' job':'Bỏ quyết định duyệt',
 '<p>'+jobs.map(j=>esc(j.company)+' — '+esc(j.jobTitle)).join('<br>')+'</p>'+
 (blocked.length?'<div class="notice">'+[...new Set(blocked)].map(esc).join('<br>')+'</div>':'')+
 (warns.length?'<div class="notice">'+[...new Set(warns)].map(esc).join('<br>')+'</div><label class="check"><input type="checkbox" id="override">Tôi đã xem cảnh báo và vẫn muốn đưa các job này vào danh sách tạo CV.</label>':'')+
 '<label class="field">Ghi chú'+(warns.length?' (bắt buộc khi duyệt ngoại lệ)':'')+'<textarea id="review-note" placeholder="Lý do quyết định hoặc điều cần lưu ý…"></textarea></label>',
 btn('Lưu quyết định','save-review','primary',blocked.length?'disabled':''));
}
function cvDialog(){
 const jobs=cvCandidates();
 if(!jobs.length)return toast('Chọn job đã duyệt trước khi tạo CV.',true);
 state.cvJobs=jobs.map(j=>j.jobId);const cv=state.run.cv;
 modal('Tạo CV cho '+jobs.length+' job','<p>Chọn người tạo CV độc lập với mục tiêu tìm kiếm. Mỗi job có một CV riêng, dùng đúng hồ sơ đã chọn và JD đã duyệt.</p><label class="field">Profile ứng viên<select id="cv-profile">'+cv.profiles.map(p=>'<option value="'+esc(p.path)+'">'+esc(p.name)+' · '+esc(p.path)+'</option>').join('')+'</select></label><label class="field">Template<select id="cv-template">'+cv.templates.map(t=>'<option '+(t===cv.defaultTemplate?'selected':'')+'>'+esc(t)+'</option>').join('')+'</select></label><label class="check"><input type="checkbox" id="confirm-profile">Tôi xác nhận profile được chọn thuộc đúng người ứng tuyển.</label><div class="notice info">Chỉ tạo CV.'+(state.data.agent?' Model: '+esc(state.data.agent.model)+' · reasoning '+esc(state.data.agent.reasoning_effort)+'.':'')+' PDF sẽ xuất hiện trong Thư viện CV sau khi qua bước kiểm tra của latex_cv.</div>',
 btn('Tạo '+jobs.length+' CV →','start-cv','primary',!cv.profiles.length||active()?'disabled':''));
}
function importDialog(job){
 state.importJob=job;state.fetchedDraft=null;
 const facts=job?Object.fromEntries(['locations','jobCountries','workMode','employmentType','level','datePosted','salary','remoteScope','eligibleCountries','timezoneRequirements','timezoneOverlapHours','sponsorship','authorizationRequired'].map(k=>[k,job[k]??null])):{};
 const seed=job?{schemaVersion:1,jobId:job.jobId,source:'manual',url:job.sourceUrl,company:job.company,jobTitle:job.jobTitle,description:job._description||'',sourceContent:job._sourceContent||'',descriptionStatus:job.descriptionStatus,capturedAt:job.fetchedAt||nowISO(),descriptionKind:'user_supplied',completenessEvidence:job.completenessEvidence||'',availabilityStatus:job.availabilityStatus,availabilityCheckedAt:job.availabilityCheckedAt,facts,evidence:job.evidence||{},roleMatches:job.roleMatches||{}}:{schemaVersion:1,source:'web_search',url:'https://'};
 state.observationSeed=seed;
 modal(job?'Bổ sung nội dung tuyển dụng':'Nhập link hoặc JD',
 '<p>Dán link rồi bấm Thu thập từ link để điền thông tin. Kiểm tra nội dung và bấm Lưu & kiểm tra để thêm vào run. Bạn cũng có thể dán JD thủ công.</p>'+
 '<label class="field">Link tuyển dụng<input id="capture-url" type="url" value="'+esc(job?.sourceUrl||'')+'" placeholder="https://…"></label>'+
 '<div class="tools">'+btn('Thu thập từ link','fetch-jd')+'</div><p id="capture-status" role="status" class="muted"></p>'+
 '<div class="capture-grid"><label class="field">Công ty<input id="capture-company" value="'+esc(job?.company||'')+'"></label><label class="field">Vị trí<input id="capture-title" value="'+esc(job?.jobTitle||'')+'"></label></div>'+
 '<label class="field">Nội dung JD<textarea id="capture-jd" rows="9" placeholder="Toàn bộ mô tả, yêu cầu và quyền lợi…">'+esc(job?._description||'')+'</textarea></label>'+
 '<label class="field">Thời gian bạn lấy JD<input id="capture-time" type="datetime-local" value="'+esc(new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16))+'"></label>'+
 '<label class="check"><input id="capture-complete" type="checkbox">Tôi xác nhận đây là toàn bộ JD từ tin gốc, không phải đoạn trích kết quả tìm kiếm.</label>'+
 '<details><summary>Nhập dữ liệu có cấu trúc & bằng chứng (nâng cao)</summary><label class="check"><input id="use-json" type="checkbox">Sử dụng JSON bên dưới thay cho form</label><label class="field">Observation JSON<textarea id="observation" rows="12" spellcheck="false">'+esc(JSON.stringify(seed,null,2))+'</textarea></label></details>',btn('Lưu & kiểm tra','save-observation','primary'));
}
function captureFields(){return ['url','company','title','jd','time'].map(k=>$('#capture-'+k).value).concat($('#capture-complete').checked);}
function nowISO(){return new Date().toISOString();}
function renderCV(){
 $('#main').innerHTML=heading('APPROVED JOBS → TAILORED CVS','Thư viện CV','Các batch độc lập, dùng JD đã duyệt và profile đã xác nhận.')+operationBanner()+
 (state.data.batches.length?'<div class="cv-grid">'+[...state.data.batches].reverse().map(b=>'<section class="panel cv-card"><div class="detail-top"><h2>'+esc(b.profileName)+'</h2><div class="tools">'+badge(b.status)+btn('Xóa batch','delete-cv','quiet danger','data-id="'+esc(b.id)+'" '+(active()?'disabled':''))+'</div></div><p>'+date(b.createdAt)+' · '+esc(b.template)+'<br>'+b.jobs.length+' jobs · Run '+esc(b.runId.slice(-6))+'</p>'+b.jobs.map(j=>{const r=b.results.find(r=>r.jobId===j.jobId);return '<div class="cv-file"><div><strong>'+esc(j.company)+'</strong><p>'+esc(j.jobTitle)+'</p></div><div class="cv-links">'+(r?.pdfs?.length?r.pdfs.map(p=>'<a target="_blank" rel="noopener" href="/api/pdf?batch='+b.id+'&job='+j.jobId+'&name='+encodeURIComponent(p)+'">Mở PDF ↗</a>').join(' '):badge(r?.status||b.status))+(httpUrl(j.sourceUrl)?'<a target="_blank" rel="noopener noreferrer" href="'+esc(httpUrl(j.sourceUrl))+'">Tin gốc ↗</a>':'')+btn('Mở thư mục','open-folder','quiet','data-id="'+esc(b.id)+'" data-jobid="'+esc(j.jobId)+'"')+'</div></div>';}).join('')+'<div class="tools cv-card-tools">'+btn('Xem log','batch-log','','data-id="'+b.id+'"')+btn('Mở thư mục batch','open-folder','','data-id="'+esc(b.id)+'"')+'</div>'+'</section>').join('')+'</div>':'<section class="panel empty"><div class="empty-icon">▤</div><h3>CV của bạn sẽ xuất hiện ở đây</h3><p>Mở một run, duyệt các job phù hợp rồi chọn “Tạo CV”.</p>'+btn('Về lịch sử run','back','primary')+'</section>');
}
function renderTrash(){
 $('#main').innerHTML=heading('WORKSPACE HISTORY','Thùng rác','Khôi phục run đã xóa. Job dùng chung và các CV đã tạo vẫn được giữ.')+'<section class="panel">'+(state.data.trash.length?'<table><thead><tr><th>Run</th><th>Ngày chạy</th><th></th></tr></thead><tbody>'+state.data.trash.map(r=>'<tr><td><strong>'+esc(summaryLabel(r.label))+'</strong><small>'+r.id+'</small></td><td>'+date(r.startedAt)+'</td><td>'+btn('Khôi phục','restore','','data-id="'+r.id+'"')+'</td></tr>').join('')+'</tbody></table>':'<div class="empty"><h3>Thùng rác trống</h3><p>Các run bị xóa sẽ được chuyển vào đây.</p></div>')+'</section>';
}
function render(){
 if(!state.data)return;
 $('#run-count').textContent=state.data.runs.length;$('#trash-count').textContent=state.data.trash.length;$('#cv-count').textContent=state.data.batches.length;
 $('#runner-status').textContent=active()?'Pipeline đang chạy':!state.data.agent?'Server UI cần khởi động lại':state.data.capabilities.codex?'Codex CLI sẵn sàng':'Chưa tìm thấy Codex CLI';
 $('#breadcrumb').textContent={runs:'Pipeline & lịch sử',review:'Review & approval',cv:'Thư viện CV',trash:'Thùng rác'}[state.view];
 document.querySelectorAll('[data-view]').forEach(n=>n.classList.toggle('active',n.dataset.view===(state.view==='review'?'runs':state.view)));
 if(state.view==='runs')renderRuns();if(state.view==='review')renderReview();if(state.view==='cv')renderCV();if(state.view==='trash')renderTrash();
}
async function refresh(redraw=true){
 state.data=await api('/api/state');
 if(state.run&&state.view==='review'){state.run=await api('/api/run?id='+encodeURIComponent(state.run.checkpoint.runId));}
 if(redraw)render();
}
async function queue(kind,extra={}){
 if(kind!=='collect'&&!state.data.agent)throw Error('Server UI chưa nạp cấu hình model mới. Hãy khởi động lại server trước khi chạy agent.');
 await api('/api/action',{action:'queue',kind,runId:state.run?.checkpoint.runId,...extra});close();toast('Đã bắt đầu pipeline.');await refresh();
}
async function logDialog(id){
 const d=await api('/api/log?id='+id);state.logId=id;
 modal('Nhật ký pipeline','<pre class="log" id="live-log"></pre>',btn('Tải lại log','refresh-log'));
 $('#live-log').textContent=d.log||'Tiến trình đang khởi động…';
}
document.addEventListener('click',async e=>{
 const nav=e.target.closest('[data-view]');if(nav){state.view=nav.dataset.view;render();return;}
 const row=e.target.closest('[data-job]');if(row&&!e.target.matches('input')){state.job=row.dataset.job;renderJobs();renderJob();return;}
 const b=e.target.closest('[data-action]');if(!b||b.disabled)return;
 const a=b.dataset.action;const id=b.dataset.id;
 try{
 if(a==='close')return close();if(a==='back'){state.view='runs';return render();}
 if(a==='new-run')return newRun();if(a==='open-run')return await openRun(id);
 if(a==='tab'){state.tab=id;return renderJob();}
 if(a==='start-search')return await queue('search',{preset:$('#preset').value});
 if(a==='resume'||a==='collect')return await queue(a);
 if(a==='cancel'){await api('/api/cancel',{});toast('Đang dừng tiến trình…');return;}
 if(a==='clear-selection'){state.selected.clear();renderJobs();return renderSelection();}
 if(a==='select-visible'){filteredJobs().forEach(j=>state.selected.add(j.jobId));renderJobs();return renderSelection();}
 if(a==='approve'||a==='reject'||a==='reset')return reviewDialog(a==='approve'?'approved':a==='reject'?'rejected':'pending',[state.job]);
 if(a==='approve-many'||a==='reject-many')return reviewDialog(a==='approve-many'?'approved':'rejected',[...state.selected]);
 if(a==='save-review'){await api('/api/action',{action:'review',runId:state.run.checkpoint.runId,...state.reviewRequest,note:$('#review-note').value,override:$('#override')?.checked||false});close();toast('Đã lưu quyết định.');return await refresh();}
 if(a==='cv-dialog')return cvDialog();
 if(a==='start-cv'){if(!$('#confirm-profile').checked)throw Error('Xác nhận đúng profile ứng viên trước khi tạo CV.');const p=state.run.cv.profiles.find(p=>p.path===$('#cv-profile').value);return await queue('cv',{jobIds:state.cvJobs,profile:p.path,confirmedName:p.name,template:$('#cv-template').value});}
 if(a==='delete-run'){state.deleteId=id;return modal('Xóa run khỏi lịch sử?','<p>Run <strong>'+esc(id)+'</strong> sẽ được đưa vào thùng rác. Bạn có thể khôi phục sau. Các job dùng chung và CV đã tạo được giữ lại.</p>',btn('Chuyển vào thùng rác','confirm-delete','danger'));}
 if(a==='confirm-delete'){await api('/api/action',{action:'delete',runId:state.deleteId});close();toast('Đã chuyển run vào thùng rác.');return await refresh();}
 if(a==='open-folder'){await api('/api/reveal',{batch:id,job:b.dataset.jobid||null});toast('Đã mở thư mục CV.');return;}
 if(a==='delete-cv'){const batch=state.data.batches.find(b=>b.id===id);if(!batch)return;state.deleteBatchId=id;return modal('Xóa batch CV?','<p>Batch của <strong>'+esc(batch.profileName)+'</strong> ('+batch.jobs.length+' job) sẽ bị xóa vĩnh viễn, gồm PDF, file làm việc và log. Approval của các job vẫn được giữ.</p>',btn('Xóa batch CV','confirm-delete-cv','danger'));}
 if(a==='confirm-delete-cv'){await api('/api/action',{action:'cv-delete',id:state.deleteBatchId});close();toast('Đã xóa batch CV.');return await refresh();}
 if(a==='restore'){await api('/api/action',{action:'restore',runId:id});toast('Đã khôi phục run.');return await refresh();}
 if(a==='import'||a==='edit-job')return importDialog(a==='edit-job'?currentJob():null);
 if(a==='fetch-jd'){
 const url=$('#capture-url').value.trim();if(!httpUrl(url))throw Error('Nhập link http hoặc https hợp lệ.');
 const dialog=$('#modal-content'), controls=[...dialog.querySelectorAll('input,textarea,button')];
 controls.forEach(c=>{c.dataset.wasDisabled=String(c.disabled);c.disabled=true;});
 state.fetching=true;
 const status=$('#capture-status');status.textContent='Đang thu thập thông tin từ link…';
 try{
 const result=await api('/api/fetch-jd',{runId:state.run.checkpoint.runId,url,jobId:state.importJob?.jobId});
 const o=result.observation;
 $('#capture-url').value=o.url;$('#capture-company').value=o.company||'';$('#capture-title').value=o.jobTitle||'';$('#capture-jd').value=o.description||'';
 const time=new Date(o.capturedAt);$('#capture-time').value=new Date(time.getTime()-time.getTimezoneOffset()*60000).toISOString().slice(0,16);
 $('#capture-complete').checked=false;$('#use-json').checked=false;
 $('#observation').value=JSON.stringify(o,null,2);
 state.fetchedDraft={observation:o,fields:captureFields()};
 status.textContent=(o.descriptionStatus==='complete'?'Đã lấy JD đầy đủ theo bằng chứng nguồn.':'Chỉ lấy được một phần JD; hãy kiểm tra và bổ sung.')+' Chưa lưu vào run; bấm Lưu & kiểm tra khi sẵn sàng.';
 }catch(err){status.textContent=err.message;throw err;}
 finally{state.fetching=false;controls.forEach(c=>{c.disabled=c.dataset.wasDisabled==='true';delete c.dataset.wasDisabled;});}
 return;
 }
 if(a==='save-observation'){
 let observation;
 if($('#use-json').checked)observation=JSON.parse($('#observation').value);
 else if(state.fetchedDraft&&JSON.stringify(captureFields())===JSON.stringify(state.fetchedDraft.fields))observation=state.fetchedDraft.observation;
 else{
 const jd=$('#capture-jd').value.trim(),url=$('#capture-url').value.trim();
 if(!url)throw Error('Nhập link nguồn của tin tuyển dụng.');
 observation={schemaVersion:1,source:jd?'manual':'web_search',url,company:$('#capture-company').value.trim()||null,jobTitle:$('#capture-title').value.trim()||null};
 if(state.importJob)observation.jobId=state.importJob.jobId;
 if(jd){const time=new Date($('#capture-time').value);if(Number.isNaN(time.getTime()))throw Error('Chọn thời gian capture hợp lệ.');
 Object.assign(observation,{description:jd,sourceContent:jd,capturedAt:time.toISOString(),descriptionKind:'user_supplied',descriptionStatus:$('#capture-complete').checked?'complete':'partial',completenessEvidence:$('#capture-complete').checked?'User confirms the complete posting was captured directly from the source URL at the stated time.':'',reviewNotes:['JD provided through the local review UI; unverified facts remain unknown.']});}
 }
 await api('/api/import',{runId:state.run.checkpoint.runId,observation});close();toast('Đã kiểm tra và nhập dữ liệu.');return await refresh();}
 if(a==='config')return modal('Cấu hình & tiến độ run','<pre class="pre-json">'+esc(JSON.stringify({config:state.run.config,tasks:state.run.checkpoint.tasks},null,2))+'</pre>','');
 if(a==='log')return await logDialog(id);
 if(a==='batch-log'){const o=state.data.operations.find(o=>o.batchId===id);if(o)return await logDialog(o.id);}
 if(a==='refresh-log'){const d=await api('/api/log?id='+state.logId);$('#live-log').textContent=d.log||'Chưa có log.';}
 }catch(err){toast(err.message,true);}
});
document.addEventListener('change',e=>{
 if(e.target.id==='preset')presetInfo();
 if(e.target.id==='cv-profile')$('#confirm-profile').checked=false;
 if(e.target.id==='job-filter'){state.filter=e.target.value;renderJobs();}
 if(e.target.dataset.select){const id=e.target.dataset.select;if(e.target.checked)state.selected.add(id);else state.selected.delete(id);renderSelection();}
});
document.addEventListener('input',e=>{
 if(e.target.id==='run-search')renderRunTable(e.target.value);
 if(e.target.id==='job-search'){state.search=e.target.value;renderJobs();}
});
document.addEventListener('keydown',e=>{if(e.target.matches('[data-job]')&&['Enter',' '].includes(e.key)){e.preventDefault();e.target.click();}});
async function poll(){
 if(polling||document.hidden)return;polling=true;
 try{
 const before=JSON.stringify(state.data?.operations);const beforeRuns=JSON.stringify(state.data?.runs);
 const d=await api('/api/state');state.data=d;
 if($('#live-log')&&$('#modal').open){const log=await api('/api/log?id='+state.logId);$('#live-log').textContent=log.log||'Đang chạy…';}
 if(!$('#modal').open&&(before!==JSON.stringify(d.operations)||beforeRuns!==JSON.stringify(d.runs))){if(state.view==='review')state.run=await api('/api/run?id='+state.run.checkpoint.runId);render();}
 }catch(err){$('#runner-status').textContent='Mất kết nối · đang thử lại…';}finally{polling=false;}
}
$('#modal').addEventListener('cancel',e=>{if(state.fetching)e.preventDefault();});
refresh().catch(e=>{$('#main').innerHTML='<div class="notice">'+esc(e.message)+'</div>';});
setInterval(poll,3000);
