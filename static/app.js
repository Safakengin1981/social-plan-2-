'use strict';
let token=sessionStorage.getItem('socialplan_v21_token')||'',role='',allPosts=[];
const el=id=>document.getElementById(id);
function message(s){el('message').textContent=s||'';window.scrollTo({top:0,behavior:'smooth'});}
async function api(path,options={}){
 const headers={...(options.headers||{})};if(token)headers.Authorization=`Bearer ${token}`;
 const response=await fetch(`/api${path}`,{...options,headers,cache:'no-store'});
 const type=response.headers.get('content-type')||'';
 const data=type.includes('json')?await response.json().catch(()=>({})):null;
 if(!response.ok)throw new Error(typeof data?.detail==='string'?data.detail:`İşlem başarısız (${response.status})`);
 return data;
}
function json(method,data){return{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)};}
async function action(fn,button){if(button)button.disabled=true;try{await fn();}catch(e){message(e.message||'Beklenmeyen hata');}finally{if(button)button.disabled=false;}}
async function register(){const business=el('business').value.trim();if(!business)throw Error('İşletme adı girin');const r=await api('/register',json('POST',{business,email:el('email').value,password:el('password').value}));message(r.message+' Şimdi giriş yapın.');}
async function login(){const r=await api('/login',json('POST',{email:el('email').value,password:el('password').value}));token=r.token;sessionStorage.setItem('socialplan_v21_token',token);await initialize();message('Giriş başarılı.');}
async function initialize(){const me=await api('/me');role=me.role;el('who').textContent=`${me.business} • ${me.email} (${role==='admin'?'Yönetici':'Editör'})`;el('membersTab').classList.toggle('hidden',role!=='admin');el('auth').classList.add('hidden');el('workspace').classList.remove('hidden');await dashboard();show('dashboard');}
async function logout(){if(token)await api('/logout',{method:'POST'}).catch(()=>{});token='';role='';sessionStorage.removeItem('socialplan_v21_token');el('auth').classList.remove('hidden');el('workspace').classList.add('hidden');message('Çıkış yapıldı.');}
function show(tab){if(tab==='members'&&role!=='admin')return;['dashboard','create','posts','publishing','members'].forEach(id=>el(id).classList.toggle('hidden',id!==tab));if(tab==='posts')action(loadPosts);if(tab==='members')action(loadMembers);if(tab==='publishing')action(loadPublishing);if(tab==='dashboard')action(dashboard);}
async function dashboard(){
 const s=await api('/dashboard');
 for(const [id,key] of [['statDraft','draft'],['statApproved','approved'],['statCancelled','cancelled'],['statMembers','members']])el(id).textContent=s[key];
 const panel=el('backupPanel');panel.classList.toggle('hidden',role!=='admin');el('auditPanel').classList.toggle('hidden',role!=='admin');
 if(role==='admin')await backupHealth();
}
async function backupHealth(){
 const label=el('backupText');label.textContent='Kontrol ediliyor…';
 try{
  const result=await api('/admin/backup-status');
  const time=result.time_utc?` • ${new Date(result.time_utc).toLocaleString('tr-TR')}`:'';
  label.textContent=(result.healthy?'✓ ':'⚠ ')+result.message+time;
  el('backupPanel').style.borderColor=result.healthy?'#8bbca2':'#d69b4c';
 }catch(e){label.textContent='⚠ Yedek durumu okunamadı: '+(e.message||'Hata');}
}
async function savePost(){const date=el('scheduled').value;if(!date||!Number.isFinite(new Date(date).getTime()))throw Error('Geçerli bir tarih seçin');let media_id=null;const file=el('media').files[0];if(file){if(file.size>25*1024*1024)throw Error('Dosya 25 MB sınırını aşıyor');const fd=new FormData();fd.append('file',file);const media=await api('/media',{method:'POST',body:fd});media_id=media.id;}await api('/posts',json('POST',{caption:el('caption').value,scheduled_at:new Date(date).toISOString(),media_id}));el('caption').value='';el('media').value='';message('Taslak başarıyla kaydedildi.');show('posts');}
function btn(label,css,handler){const b=document.createElement('button');b.textContent=label;if(css)b.className=css;b.addEventListener('click',()=>action(handler,b));return b;}
async function loadPosts(){allPosts=await api('/posts');renderPosts();}
function renderPosts(){const list=el('postlist');list.replaceChildren();const query=el('searchPosts').value.toLocaleLowerCase('tr-TR');const status=el('filterStatus').value;const rows=allPosts.filter(p=>(status==='all'||p.status===status)&&p.caption.toLocaleLowerCase('tr-TR').includes(query));if(!rows.length){list.textContent='Bu filtreye uygun içerik yok.';return;}for(const p of rows){const card=document.createElement('article');card.className='post';const title=document.createElement('strong');title.textContent=new Date(p.scheduled_at).toLocaleString('tr-TR')+' • ';const badge=document.createElement('span');badge.className='badge';badge.textContent={draft:'Taslak',approved:'Onaylı',cancelled:'İptal'}[p.status]||p.status;const caption=document.createElement('p');caption.textContent=p.caption;card.append(title,badge,caption);if(p.media_id){const media=btn('Medyayı görüntüle','secondary',async()=>{const response=await fetch(`/api/media/${p.media_id}`,{headers:{Authorization:`Bearer ${token}`}});if(!response.ok)throw Error('Medya açılamadı');const blob=await response.blob();const url=URL.createObjectURL(blob);window.open(url,'_blank','noopener');setTimeout(()=>URL.revokeObjectURL(url),60000);});card.append(media);}const actions=document.createElement('div');actions.className='actions';if(p.status==='draft'){actions.append(btn('Taslağı düzenle','secondary',async()=>{const text=prompt('Gönderi açıklaması',p.caption);if(text===null)return;const date=prompt('Planlanan tarih (ISO biçiminde)',p.scheduled_at);if(date===null)return;await api(`/posts/${p.id}`,json('PUT',{caption:text,scheduled_at:date}));message('Taslak güncellendi');await loadPosts();}));if(role==='admin')actions.append(btn('Onayla','',async()=>{await api(`/posts/${p.id}/approve`,{method:'POST'});message('İçerik onaylandı; Instagram’a gönderilmedi.');await loadPosts();await dashboard();}));}if(role==='admin'&&p.status!=='cancelled')actions.append(btn('İptal et','danger',async()=>{if(!confirm('Bu içeriği iptal etmek istiyor musunuz?'))return;await api(`/posts/${p.id}/cancel`,{method:'POST'});message('İçerik iptal edildi');await loadPosts();await dashboard();}));card.append(actions);list.append(card);}}
async function loadMembers(){const rows=await api('/members');const list=el('memberlist');list.replaceChildren();for(const r of rows){const p=document.createElement('p');p.textContent=`${r.email} • ${r.role==='admin'?'Yönetici':'Editör'}`;list.append(p);}}
async function addMember(){await api('/members',json('POST',{email:el('memberEmail').value,password:el('memberPassword').value,role:el('memberRole').value}));el('memberPassword').value='';message('Üye eklendi.');await loadMembers();}
async function loadPublishing(){
 const s=await api('/publishing/status');el('publishStatus').textContent=s.message;
 const panel=el('publishAdmin');panel.classList.toggle('hidden',role!=='admin');
 if(role!=='admin')return;
 const [queue,readiness]=await Promise.all([api('/admin/publishing-queue'),api('/admin/instagram-readiness')]);
 const list=el('publishQueue');list.replaceChildren();
 if(!queue.items.length){list.textContent='Henüz onaylanmış içerik yok.';}
 for(const item of queue.items){
  const card=document.createElement('article');card.className='post';
  const head=document.createElement('strong');head.textContent=`#${item.id} • ${new Date(item.scheduled_at).toLocaleString('tr-TR')} • ${item.due?'Yayın saati geldi':'Planlandı'}`;
  const caption=document.createElement('p');caption.textContent=item.caption;
  const reason=document.createElement('p');reason.className='muted';reason.textContent='Yayın engelleri: '+item.blockers.join('; ');
  card.append(head,caption,reason);
  if(item.media_id)card.append(btn('Yayın işi hazırla','secondary',async()=>{const result=await api(`/admin/publishing-queue/${item.id}/prepare`,{method:'POST'});message(result.created?'Yayın hazırlık kaydı oluşturuldu; paylaşım yapılmadı.':'Bu içerik için yayın işi zaten mevcut.');await loadPublishing();}));
  list.append(card);
 }
 const jobData=await api('/admin/publishing-jobs');
 const jobsBox=el('publishJobs');jobsBox.replaceChildren();
 if(!jobData.jobs.length)jobsBox.textContent='Henüz yayın işi oluşturulmadı.';
 const labels={pending:'Hazırlık bekliyor',creating_container:'Medya hazırlanıyor',container_created:'Medya oluşturuldu',container_ready:'Yayına hazır',publishing:'Gönderim başladı',published:'Yayımlandı (kayıt; Meta doğrulaması yok)',uncertain:'SONUÇ BELİRSİZ — otomatik tekrar yasak',cancelled:'İptal edildi'};
 for(const job of jobData.jobs){
  const card=document.createElement('article');card.className='post';
  const title=document.createElement('strong');title.textContent=`İş #${job.id} • İçerik #${job.post_id}`;
  const state=document.createElement('p');state.textContent=labels[job.status]||job.status;
  card.append(title,state);
  if(job.last_error){const error=document.createElement('p');error.className='muted';error.textContent='Not: '+job.last_error;card.append(error);}
  card.append(btn('İşlem geçmişi','secondary',async()=>{const data=await api(`/admin/publishing-jobs/${job.id}/history`);let detail=card.querySelector('.job-history');if(detail){detail.remove();return;}detail=document.createElement('div');detail.className='job-history';if(!data.events.length)detail.textContent='Henüz durum değişikliği yok.';for(const event of data.events){const line=document.createElement('p');line.textContent=`${new Date(event.created_at).toLocaleString('tr-TR')} • ${event.event}${event.note?' • '+event.note:''}${event.meta_id?' • Meta ID: '+event.meta_id:''}`;detail.append(line);}card.append(detail);}));
  jobsBox.append(card);
 }
 const box=el('instagramReadiness');box.replaceChildren();
 const info=document.createElement('p');info.textContent=readiness.message;box.append(info);
 const ul=document.createElement('ul');for(const step of readiness.steps){const li=document.createElement('li');li.textContent=step;ul.append(li);}box.append(ul);
 const config=document.createElement('p');config.className='muted';config.textContent='Sunucu ayarları: '+Object.entries(readiness.configured).map(([k,v])=>`${k}: ${v?'Tanımlı':'Eksik'}`).join(' • ');box.append(config);
}
async function exportPosts(){const response=await fetch('/api/posts/export.csv',{headers:{Authorization:`Bearer ${token}`}});if(!response.ok)throw Error('Dışa aktarma başarısız');const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download='socialplan_icerikler.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function loadAudit(){
 if(role!=='admin')return;
 const rows=await api('/admin/audit?limit=30');const list=el('auditList');list.replaceChildren();
 if(!rows.length){list.textContent='Henüz kayıtlı işlem yok.';return;}
 const names={post_created:'Oluşturuldu',post_edited:'Düzenlendi',post_approved:'Onaylandı',post_cancelled:'İptal edildi'};
 for(const item of rows){const line=document.createElement('p');line.textContent=`${new Date(item.created_at).toLocaleString('tr-TR')} • İçerik #${item.post_id} • ${names[item.action]||item.action} • ${item.actor}`;list.append(line);}
}
document.addEventListener('DOMContentLoaded',()=>{for(const [id,fn] of [['registerBtn',register],['loginBtn',login],['logoutBtn',logout],['saveBtn',savePost],['memberBtn',addMember],['exportBtn',exportPosts],['backupRefresh',backupHealth],['auditRefresh',loadAudit],['publishRefresh',loadPublishing]]){const b=el(id);b.addEventListener('click',()=>action(fn,b));}document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>show(b.dataset.tab)));el('filterStatus').addEventListener('change',renderPosts);el('searchPosts').addEventListener('input',renderPosts);if(token)action(async()=>{try{await initialize();}catch(e){await logout();}});if('serviceWorker'in navigator&&location.protocol==='https:')navigator.serviceWorker.register('/sw.js').catch(()=>{});});
