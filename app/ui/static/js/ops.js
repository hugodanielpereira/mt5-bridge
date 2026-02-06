// app/ui/static/js/ops.js
async function opsRun(action){
  const tgt = document.getElementById('opsTarget')?.value || 'all';
  const out = document.getElementById('opsOut');
  if (!out) return;

  out.value += `> ${tgt} ${action}\n`;
  try{
    const j = await api('/proc_run', {
      method:'POST',
      body: JSON.stringify({target:tgt, action})
    });
    out.value += JSON.stringify(j,null,2) + "\n";
  }catch(e){
    out.value += 'erro: ' + (e?.message || e) + "\n";
  }
  out.scrollTop = out.scrollHeight;
}

async function opsStatus(){
  const out = document.getElementById('opsOut');
  if (!out) return;

  // 1) tenta pela API (na base /ui/api)
  try{
    const st = await api('/status');
    out.value += JSON.stringify(st, null, 2) + "\n";
    out.scrollTop = out.scrollHeight;
    return;
  }catch(_){}

  // 2) fallback /health via api helper (se existir endpoint)
  try{
    const health = await api('/health');
    out.value += (typeof health === 'string' ? health : JSON.stringify(health, null, 2)) + "\n";
    out.scrollTop = out.scrollHeight;
    return;
  }catch(_){}

  // 3) fallback absoluto ao root do backend
  try{
    const r = await fetch((window.API_ORIGIN||'') + '/health');
    const txt = await r.text();
    out.value += txt + "\n";
  }catch(e){
    out.value += 'erro: ' + (e?.message || e) + "\n";
  }
  out.scrollTop = out.scrollHeight;
}