//#bridges\mt5-bridge\app\ui\static\js\ops.js
async function opsRun(action){
  const tgt = document.getElementById('opsTarget').value;
  const out = document.getElementById('opsOut');
  out.value += `> ${tgt} ${action}\n`;
  try{
    const j = await api('/proc_run', {
      method:'POST',
      body: JSON.stringify({target:tgt, action})
    });
    out.value += JSON.stringify(j,null,2) + "\n";
  }catch(e){
    out.value += 'erro: '+e+"\n";
  }
  out.scrollTop = out.scrollHeight;
}

async function opsStatus(){
  const out = document.getElementById('opsOut');
  try{
    // usar fallback absoluto para /health (fora do prefixo), se precisares:
    const health = await api('/health');  // se no backend expões /ui/api/health
    out.value += JSON.stringify(health, null, 2) + "\n";
  }catch(e){
    // fallback absoluto ao root /health
    try{
      const r = await fetch((window.API_ORIGIN||'') + '/health');
      out.value += (await r.text())+"\n";
    }catch(e2){
      out.value += 'erro: '+e2+"\n";
    }
  }
  out.scrollTop = out.scrollHeight;
}