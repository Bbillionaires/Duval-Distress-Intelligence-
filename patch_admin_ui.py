from pathlib import Path
import re

p = Path("admin.html")
t = p.read_text(encoding="utf-8", errors="ignore")

# Add a simple toolbar + script if not present
if "function adminLogout" not in t:
    toolbar = r'''
<div style="display:flex;gap:10px;align-items:center;margin:12px 0;">
  <button id="btnRebuild" style="padding:8px 12px;cursor:pointer;">Rebuild CSV</button>
  <button id="btnExport" style="padding:8px 12px;cursor:pointer;">Export TRUE CSV</button>
  <button id="btnLogout" style="padding:8px 12px;cursor:pointer;">Logout</button>
  <span id="adminMsg" style="opacity:0.8;"></span>
</div>
<script>
async function adminRebuild(){
  const res = await fetch('/api/rebuild_delinquent',{method:'POST'});
  const j = await res.json();
  document.getElementById('adminMsg').textContent = (j.ok ? 'Rebuilt.' : 'Rebuild failed.');
}
async function adminExport(){
  const res = await fetch('/api/delinquent?page=1&page_size=2000&still_delinquent=true');
  const j = await res.json();
  const rows = j.rows || [];
  if(!rows.length){ alert('No TRUE rows returned'); return; }
  const headers = Object.keys(rows[0]);
  const esc = (v)=>('"'+String(v ?? '').replaceAll('"','""')+'"');
  const csv = [headers.join(',')].concat(rows.map(r=>headers.map(h=>esc(r[h])).join(','))).join('\n');
  const blob = new Blob([csv], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'delinquent_true.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}
async function adminLogout(){
  await fetch('/api/logout',{method:'POST'});
  window.location.href = '/login';
}
document.addEventListener('DOMContentLoaded', ()=>{
  const r=document.getElementById('btnRebuild'); if(r) r.onclick=adminRebuild;
  const e=document.getElementById('btnExport'); if(e) e.onclick=adminExport;
  const l=document.getElementById('btnLogout'); if(l) l.onclick=adminLogout;
});
</script>
'''
    if "<body" in t and "</body>" in t:
        # inject right after <body...>
        t = re.sub(r"(<body[^>]*>)", r"\1\n" + toolbar + "\n", t, count=1)
    else:
        t = toolbar + "\n" + t

p.write_text(t, encoding="utf-8")
print("PATCHED admin.html back office buttons")
