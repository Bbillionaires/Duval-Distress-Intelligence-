from pathlib import Path
import re

p = Path("login.html")
t = p.read_text(encoding="utf-8", errors="ignore")

marker = "<!-- LOGIN_PATCH_V1 -->"
if marker not in t:
    # If there's no script tag, add one before </body>
    script = f"""
{marker}
<script>
async function doLogin(e){{
  if(e) e.preventDefault();
  const pw = document.getElementById('password') ? document.getElementById('password').value : '';
  const res = await fetch('/api/login', {{
    method:'POST',
    headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{password: pw}})
  }});
  if(res.ok){{
    window.location.href = '/admin.html';
    return false;
  }}
  let msg = 'Login failed';
  try {{
    const j = await res.json();
    if(j && j.error) msg = j.error;
  }} catch(e) {{}}
  alert(msg);
  return false;
}}
window.addEventListener('DOMContentLoaded', ()=>{{
  const f = document.querySelector('form');
  if(f) f.addEventListener('submit', doLogin);
  const btn = document.querySelector('button');
  if(btn && !btn.closest('form')) btn.addEventListener('click', doLogin);
}});
</script>
"""
    if "</body>" in t:
        t = t.replace("</body>", script + "\n</body>", 1)
    else:
        t = t + "\n" + script

# Ensure there is an input with id=password
if 'id="password"' not in t:
    # crude add: put a password input near top of body
    t = re.sub(r"(<body[^>]*>)", r"\1\n<h2>Admin Login</h2>\n<form>\n<input id=\"password\" type=\"password\" placeholder=\"Password\" required />\n<button type=\"submit\">Login</button>\n</form>\n", t, count=1)

p.write_text(t, encoding="utf-8")
print("PATCHED login.html: added password form + /api/login JS")
