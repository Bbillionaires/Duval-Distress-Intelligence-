from pathlib import Path
import re

p = Path("login.html")
t = p.read_text(encoding="utf-8", errors="ignore")

# Ensure there's an input with id="password" and a button with id="loginBtn"
# (non-destructive: only inject script at end if missing)

if "function doLogin" not in t:
    inject = r'''
<script>
async function doLogin(){
  const pwEl = document.querySelector('input[type="password"]') || document.getElementById('password');
  const password = (pwEl ? pwEl.value : '').trim();
  const res = await fetch('/api/login', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({password})
  });
  if(res.ok){
    window.location.href = '/admin.html';
    return;
  }
  try{
    const j = await res.json();
    alert(j.error || 'Login failed');
  }catch(e){
    alert('Login failed');
  }
}
document.addEventListener('DOMContentLoaded', ()=>{
  const btn = document.querySelector('button[type="submit"], button');
  if(btn){
    btn.addEventListener('click', (e)=>{ e.preventDefault(); doLogin(); });
  }
  const pwEl = document.querySelector('input[type="password"]');
  if(pwEl){
    pwEl.addEventListener('keydown', (e)=>{ if(e.key==='Enter'){ e.preventDefault(); doLogin(); }});
  }
});
</script>
'''
    if "</body>" in t:
        t = t.replace("</body>", inject + "\n</body>")
    else:
        t += "\n" + inject

p.write_text(t, encoding="utf-8")
print("PATCHED login.html button wiring")
