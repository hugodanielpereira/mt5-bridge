// app/ui/static/js/dashboard.js
(async function(){
  // ==== refs a elementos existentes no HTML ====
  const $ts = document.getElementById('ts');
  const $bridge = document.getElementById('bridgeCard');
  const $exec = document.getElementById('executorCard');
  const $retr = document.getElementById('retrainCard');
  const $watch = document.getElementById('watcherCard');   // opcional (se existir no HTML)
  const $emit  = document.getElementById('emitterCard');   // opcional
  const $gov   = document.getElementById('governorCard');  // opcional
  const $prom  = document.getElementById('promoterCard');  // opcional

  const $strategies = document.getElementById('strategies');
  const $yaml = document.getElementById('scheduleYaml');
  const $healthLine = document.getElementById('healthLine');
  const $refreshBtn = document.getElementById('refreshBtn');

  // --------------------------------------------------
  // Helpers
  // --------------------------------------------------
  async function _fetchExecutorState() {
    try { return await api('/executor_state'); } catch(_){ return {}; }
  }
  async function _fetchSchedulerState() {
    try { return await api('/scheduler_state'); } catch(_){ return {}; }
  }

  // “é vazio/zero?” — útil para decidir quando usar fallback
  function _isMissingNum(x) {
    return x === null || x === undefined || (typeof x === 'number' && !isFinite(x));
  }

  function kv(container, rows){
    if (!container) return;
    container.innerHTML = '';
    rows.forEach(([k,v,klass])=>{
      const div=document.createElement('div');
      const a=document.createElement('span'); a.textContent=k;
      const b=document.createElement('span'); b.textContent=v; if(klass) b.className=klass;
      div.appendChild(a); div.appendChild(b); container.appendChild(div);
    });
  }

  function setLight(el, state, title) {
    if (!el) return;
    el.classList.remove("ok","warn","bad","off");
    el.classList.add(state || "off");
    if (title) el.title = title;
  }

  function fmtMeta(mem, upt) {
    if (mem == null && upt == null) return "";
    const m = mem != null ? `${Math.round(mem)} MB` : "–";
    const u = upt != null ? `${Math.round(upt)} s` : "–";
    return `[${m} / ${u}]`;
  }

  function fmt1(n){ const x=Number(n); return Number.isFinite(x)? x.toFixed(1) : '—'; }
  function fmtUptimeSec(s){
    const x = Number(s);
    if(!Number.isFinite(x) || x < 0) return '—';
    const d = Math.floor(x/86400), h = Math.floor((x%86400)/3600), m = Math.floor((x%3600)/60);
    if (d>0) return `${d}d ${h}h`;
    if (h>0) return `${h}h ${m}m`;
    return `${m}m`;
  }
  const basename = p => (p || '').split(/[\\/]/).pop() || '—';

  // usa fmtNum do common.js (não redefinir aqui)
  const fmtNumSafe = (x)=> (typeof window.fmtNum==='function' ? window.fmtNum(x) : fmt1(x));

  // --- helper: mostrar um log na caixa "Logs" ---
  async function showLog(name = 'retrain.log', n = 300){
    const out = document.getElementById('out');
    if (!out) return;
    try{
      const txt = await api(`/logs/get?name=${encodeURIComponent(name)}&n=${n}`);
      out.value = (typeof txt === 'string') ? txt : JSON.stringify(txt, null, 2);
      out.scrollTop = out.scrollHeight;
    }catch(e){
      out.value = 'Erro a carregar log: ' + e;
    }
  }

  // --- contador de refresh automático (15s) ---
  let _refreshTicker = null, _refreshLeft = 0;
  function startAutoRefresh(runNow){
    const banner = document.getElementById('globalStatusCountdown');
    const T = 15; // segundos
    if (_refreshTicker) clearInterval(_refreshTicker);
    _refreshLeft = T;
    function tick(){
      if (banner) banner.textContent = `↻ ${_refreshLeft}s`;
      _refreshLeft -= 1;
      if (_refreshLeft < 0){
        _refreshLeft = T;
        document.getElementById('refreshBtn')?.click();
      }
    }
    tick();
    _refreshTicker = setInterval(tick, 1000);
    if (runNow) document.getElementById('refreshBtn')?.click();
  }

  // -------------------------------------------------------
  // loadHealth — bridge + executor + retrain + watcher + emitter (+ gov/prom se existirem)
  // -------------------------------------------------------
  async function loadHealth(){
    try{
      const s = await api('/status'); // -> /ui/api/status
      if ($ts) $ts.textContent = 'Atualizado: ' + new Date().toLocaleString();

      // ---------- BRIDGE ----------
      const b = s?.bridge || {};
      const bOk = !!b.ok || !!b.diag?.ok;
      kv($bridge, [
        ['Status',  bOk ? 'OK' : 'OFF', bOk ? 'ok' : 'bad'],
        ['PID',     b.pid ?? '—'],
        ['Uptime',  fmtUptimeSec(b.uptime_s)],
        ['CPU (s)', b.cpu_s != null ? Math.round(b.cpu_s) : '—'],
        ['Mem (MB)',b.mem_mb != null ? Math.round(b.mem_mb) : '—'],
      ]);
      if ($healthLine) $healthLine.textContent = 'Bridge=' + (bOk ? 'OK' : 'FAIL');
      setLight(document.getElementById('light-bridge'), bOk ? 'ok' : 'bad', 'Bridge');
      const bMeta = document.getElementById('meta-bridge');
      if (bMeta) bMeta.textContent = fmtMeta(
        b.mem_mb != null ? Math.round(b.mem_mb) : null,
        b.uptime_s != null ? Math.round(b.uptime_s) : null
      );

      // ---------- EXECUTOR ----------
      let e = s?.executor || {};
      // fallback: se estiverem a faltar métricas, tenta /executor_state
      if (!e || (!e.pid && (_isMissingNum(e.cpu_s) || _isMissingNum(e.mem_mb) || _isMissingNum(e.uptime_s)))) {
        const es = await _fetchExecutorState();
        e = {
          ...e,
          pid: e.pid ?? es.pid,
          cpu_s: !_isMissingNum(e.cpu_s) ? e.cpu_s : es.cpu_s,
          mem_mb: !_isMissingNum(e.mem_mb) ? e.mem_mb : es.mem_mb,
          uptime_s: !_isMissingNum(e.uptime_s) ? e.uptime_s : es.uptime_s,
          log_age_min: e.log_age_min ?? es.age_min,
          log_file: e.log_file ?? es.log_file,
          fresh: (typeof e.fresh === 'boolean') ? e.fresh : es.fresh
        };
      }

      kv($exec, [
        ['PID', e.pid ?? '—'],
        ['CPU (s)', fmt1(e.cpu_s)],
        ['Mem (MB)', e.mem_mb != null ? Math.round(e.mem_mb) : '—'],
        ['Log age (min)', e.log_age_min != null ? e.log_age_min : '—'],
        ['Log file', basename(e.log_file)],
        ['Uptime', fmtUptimeSec(e.uptime_s)],
      ]);

      const eState = (e.fresh === false) ? 'bad' : (e.pid ? 'ok' : 'off');
      setLight(document.getElementById('light-exec'), eState, 'Executor');
      const eMeta = document.getElementById('meta-exec');
      if (eMeta) eMeta.textContent = fmtMeta(
        e.mem_mb != null ? Math.round(e.mem_mb) : null,
        e.uptime_s != null ? Math.round(e.uptime_s) : null
      );

      // ---------- RETRAIN (scheduler) ----------
      let r = s?.retrain || {};
      // fallback: se estiverem a faltar métricas, tenta /scheduler_state
      if (!r || (!r.pid && (_isMissingNum(r.cpu_s) || _isMissingNum(r.mem_mb) || _isMissingNum(r.uptime_s)))) {
        const rs = await _fetchSchedulerState();
        r = {
          ...r,
          pid: r.pid ?? rs.pid,
          cpu_s: !_isMissingNum(r.cpu_s) ? r.cpu_s : rs.cpu_s,
          mem_mb: !_isMissingNum(r.mem_mb) ? r.mem_mb : rs.mem_mb,
          uptime_s: !_isMissingNum(r.uptime_s) ? r.uptime_s : rs.uptime_s,
          log_age_min: r.log_age_min ?? rs.age_min,
          log_file: r.log_file ?? rs.log_file,
          fresh: (typeof r.fresh === 'boolean') ? r.fresh : rs.fresh,
          locks: Array.isArray(r.locks) ? r.locks : (Array.isArray(rs.locks) ? rs.locks : [])
        };
      }

      kv($retr, [
        ['PID', r.pid ?? '—'],
        ['CPU (s)', fmt1(r.cpu_s)],
        ['Mem (MB)', r.mem_mb != null ? Math.round(r.mem_mb) : '—'],
        ['Log age (min)', r.log_age_min != null ? r.log_age_min : '—'],
        ['Log file', basename(r.log_file)],
        ['Uptime', fmtUptimeSec(r.uptime_s)],
        ['Locks', (Array.isArray(r.locks) && r.locks.length) ? r.locks.join(', ') : 'none'],
      ]);

      const rState = (r.fresh === false) ? 'warn' : (r.pid ? 'ok' : 'off');
      setLight(document.getElementById('light-retrain'), rState, 'Retrain');
      const rMeta = document.getElementById('meta-retrain');
      if (rMeta) rMeta.textContent = fmtMeta(
        r.mem_mb != null ? Math.round(r.mem_mb) : null,
        r.uptime_s != null ? Math.round(r.uptime_s) : null
      );

      // ---------- WATCHER ----------
      const w = s?.watcher || {};
      if ($watch){
        kv($watch, [
          ['PID', w.pid ?? '—'],
          ['CPU (s)', fmt1(w.cpu_s)],
          ['Mem (MB)', w.mem_mb != null ? Math.round(w.mem_mb) : '—'],
          ['Log age (min)', w.log_age_min != null ? w.log_age_min : '—'],
          ['Log file', basename(w.log_file)],
          ['Uptime', fmtUptimeSec(w.uptime_s)],
        ]);
      }
      const wState = w.pid ? 'ok' : ((w.fresh === false) ? 'warn' : 'off');
      setLight(document.getElementById('light-watcher'), wState, 'Watcher');
      const wMeta = document.getElementById('meta-watcher');
      if (wMeta) wMeta.textContent = fmtMeta(
        w.mem_mb != null ? Math.round(w.mem_mb) : null,
        w.uptime_s != null ? Math.round(w.uptime_s) : null
      );

      // ---------- EMITTER ----------
      const m = s?.emitter || {};
      if ($emit){
        kv($emit, [
          ['PID', m.pid ?? '—'],
          ['CPU (s)', fmt1(m.cpu_s)],
          ['Mem (MB)', m.mem_mb != null ? Math.round(m.mem_mb) : '—'],
          ['Log age (min)', m.log_age_min != null ? m.log_age_min : '—'],
          ['Log file', basename(m.log_file)],
          ['Uptime', fmtUptimeSec(m.uptime_s)],
        ]);
      }
      const mState = (m.fresh === false) ? 'warn' : (m.pid ? 'ok' : 'off');
      setLight(document.getElementById('light-emitter'), mState, 'Emitter');
      const mMeta = document.getElementById('meta-emitter');
      if (mMeta) mMeta.textContent = fmtMeta(
        m.mem_mb != null ? Math.round(m.mem_mb) : null,
        m.uptime_s != null ? Math.round(m.uptime_s) : null
      );

      // ---------- GOVERNOR (opcional) ----------
      const g = s?.governor || {};
      const gs = g.state || {};
      const g_pid = g.pid ?? gs.pid;
      const g_mem = g.mem_mb ?? gs.mem_mb;
      const g_upt = g.uptime_s ?? gs.uptime_s;
      const g_ok = !!g_pid || !!gs.pid;

      if ($gov) {
        kv($gov, [
          ['PID', g_pid ?? '—'],
          ['CPU (s)', fmt1(g.cpu_s ?? gs.cpu_s)],
          ['Mem (MB)', g_mem != null ? Math.round(g_mem) : '—'],
          ['Uptime', fmtUptimeSec(g_upt)],
          ['Fresh', String(g.fresh ?? gs.fresh ?? '—')],
          ['Log age (min)', g.log_age_min ?? '—'],
          ['Log file', basename(g.log_file)],
        ]);
      }
      setLight(document.getElementById('light-governor'), g_ok ? 'ok' : 'off', 'Governor');
      const gMeta = document.getElementById('meta-governor');
      if (gMeta) gMeta.textContent = fmtMeta(
        g_mem != null ? Math.round(g_mem) : null,
        g_upt != null ? Math.round(g_upt) : null
      );

      // ---------- PROMOTER (opcional) ----------
      const p = s?.promoter || {};
      const ps = p.state || {};
      const p_pid = p.pid ?? ps.pid;
      const p_mem = p.mem_mb ?? ps.mem_mb;
      const p_upt = p.uptime_s ?? ps.uptime_s;
      const p_ok = !!p_pid || !!ps.pid;

      if ($prom) {
        kv($prom, [
          ['PID', p_pid ?? '—'],
          ['CPU (s)', fmt1(p.cpu_s ?? ps.cpu_s)],
          ['Mem (MB)', p_mem != null ? Math.round(p_mem) : '—'],
          ['Uptime', fmtUptimeSec(p_upt)],
          ['Fresh', String(p.fresh ?? ps.fresh ?? '—')],
          ['Log age (min)', p.log_age_min ?? '—'],
          ['Log file', basename(p.log_file)],
          ['Scan every (min)', p.scan_every_min ?? ps.scan_every_min ?? '—'],
          ['Require MODEL_OK', String(p.require_model_ok ?? ps.require_model_ok ?? '—')],
        ]);
      }
      setLight(document.getElementById('light-promoter'), p_ok ? 'ok' : 'off', 'Promoter');
      const pMeta = document.getElementById('meta-promoter');
      if (pMeta) pMeta.textContent = fmtMeta(
        p_mem != null ? Math.round(p_mem) : null,
        p_upt != null ? Math.round(p_upt) : null
      );

      // ---------- TOP BANNER ----------
      const banner = document.getElementById("globalStatus");
      if (banner){
        let state='ok', msg='✅ Serviços operacionais';
        if (!bOk)                   { state='bad';  msg='❌ Bridge inativo'; }
        else if (e.fresh === false) { state='bad';  msg='⚠️ Executor sem atividade recente'; }
        else if (m.fresh === false) { state='warn'; msg='ℹ️ Emitter inativo / em espera'; }
        else if (w.fresh === false) { state='warn'; msg='ℹ️ Watcher inativo / em espera'; }
        else if (r.fresh === false) { state='warn'; msg='ℹ️ Retrain parado / em espera'; }

        banner.className = `status-banner ${state}`;
        let right = document.getElementById('globalStatusCountdown');
        if (!right){
          right = document.createElement('span');
          right.id = 'globalStatusCountdown';
          right.style.float = 'right';
          right.style.opacity = '0.8';
          banner.appendChild(right);
        }
        banner.textContent = msg + ' ';
        banner.appendChild(right);
      }
    } catch(e){
      if ($healthLine) $healthLine.textContent = 'Falha health: ' + e;
      const banner = document.getElementById("globalStatus");
      if (banner){
        banner.className = 'status-banner off';
        banner.textContent = '⚫ Sem ligação à API';
      }
      kv($bridge, [['Status','OFF','bad']]);
      kv($exec,   [['PID','—'],['CPU (s)','—'],['Mem (MB)','—'],['Log age (min)','—'],['Uptime','—']]);
      kv($retr,   [['PID','—'],['CPU (s)','—'],['Mem (MB)','—'],['Log age (min)','—'],['Uptime','—'],['Locks','—']]);
      if ($watch){ kv($watch,[['PID','—'],['CPU (s)','—'],['Mem (MB)','—'],['Log age (min)','—'],['Uptime','—']]); }
      if ($emit){  kv($emit, [['PID','—'],['CPU (s)','—'],['Mem (MB)','—'],['Log age (min)','—'],['Uptime','—']]); }
      if ($gov){   kv($gov,  [['PID','—'],['Uptime','—']]); }
      if ($prom){  kv($prom, [['PID','—'],['Uptime','—']]); }

      setLight(document.getElementById('light-bridge'),'off');
      setLight(document.getElementById('light-exec'),'off');
      setLight(document.getElementById('light-retrain'),'off');
      setLight(document.getElementById('light-watcher'),'off');
      setLight(document.getElementById('light-emitter'),'off');
      setLight(document.getElementById('light-governor'),'off');
      setLight(document.getElementById('light-promoter'),'off');
      ['meta-bridge','meta-exec','meta-retrain','meta-watcher','meta-emitter'].forEach(id=>{
        const el=document.getElementById(id); if(el) el.textContent='';
      });
    }
  }

  // --------------------------------------------------
  // Load Strategies Table
  // -------------------------------------------------
  async function loadStrategies(){
    try{
      const data = await api('/strategies');
      if (Array.isArray(data?.rows) && data.rows.length){
        $yaml && ($yaml.style.display='none');
        const tbl = document.createElement('table');
        tbl.innerHTML = `
          <thead><tr>
            <th>Symbol</th><th>TF</th><th>Fonte</th><th>Window (min)</th>
            <th>Last run (min)</th><th>Due?</th><th>Next in (min)</th><th>Config</th><th>Ações</th>
          </tr></thead>
          <tbody></tbody>`;
        const tb = tbl.querySelector('tbody');
        data.rows.forEach(r=>{
          const tr=document.createElement('tr');
          const fonte = r.source || 'schedule';
          tr.innerHTML = `
            <td>${r.symbol||'-'}</td>
            <td><span class="tag">${r.timeframe||'-'}</span></td>
            <td><a class="tag" href="${r.source_url||'#'}">${fonte}</a></td>
            <td>${fmtNumSafe(r.window_min)}</td>
            <td>${fmtNumSafe(r.last_run_min)}</td>
            <td>${r.due?'<span class="ok">Sim</span>':'<span class="muted">Não</span>'}</td>
            <td>${fmtNumSafe(r.next_in_min)}</td>
            <td class="mono">${r.config_file||'-'}</td>
            <td class="actions">
              <button class="btn-small" data-cfg="${r.config_file||''}" data-symbol="${r.symbol||''}">Run</button>
              ${r.config_file? `<a class="btn-small" href="${(window.API_ORIGIN||'') + (window.API_BASE||'')}/view_config?file=${encodeURIComponent(r.config_file)}" target="_blank">Ver cfg</a>`:''}
            </td>`;
          tb.appendChild(tr);
        });
        $strategies.innerHTML='';
        $strategies.appendChild(tbl);

        $strategies.querySelectorAll('button[data-cfg]').forEach(btn=>{
          btn.addEventListener('click', async ()=>{
            try{
              appendOut(`> retrain (single) ${btn.dataset.symbol}`);
              const j = await api('/retrain_one', {
                method:'POST',
                body: JSON.stringify({config: btn.dataset.cfg})
              });
              appendOut(JSON.stringify(j, null, 2));
              await refreshAll();
            }catch(e){ appendOut('erro: '+e); }
          });
        });
        return;
      }
    }catch(_){}

    try{
      const y = await api('/schedule_yaml');
      $strategies.innerHTML='';
      if ($yaml){ $yaml.style.display=''; $yaml.textContent = typeof y==='string' ? y : JSON.stringify(y,null,2); }
      return;
    }catch(_){}

    try{
      const r = await fetch('/ui/retrain.yaml');
      if (r.ok){
        const txt = await r.text();
        $strategies.innerHTML='';
        if ($yaml){ $yaml.style.display=''; $yaml.textContent = txt; }
        return;
      }
    }catch(_){}

    $strategies.innerHTML='<div class="muted">Sem estratégias.</div>';
    if ($yaml) $yaml.style.display='none';
  }

  async function loadPortfolioBox(){
  const box = document.getElementById('portfolioBox');
  if (!box) return;
  try {
    const data = await api('/portfolio');
    const items = Array.isArray(data?.items) ? data.items : (data?.selection?.items || []);
    if (!items || !items.length){
      box.innerHTML = '<div class="muted">Sem seleção no momento.</div>';
      return;
    }
    // tabela compacta
    let html = '<table><thead><tr><th>#</th><th>Symbol</th><th>TF</th><th>PF</th><th>Trades</th><th>Sharpe</th><th>maxDD</th></tr></thead><tbody>';
    items.forEach((it, i)=>{
      html += `<tr>
        <td>${i+1}</td>
        <td>${it.symbol||'-'}</td>
        <td><span class="tag">${it.timeframe||'-'}</span></td>
        <td>${fmtNum(it.pf)}</td>
        <td>${fmtNum(it.trades)}</td>
        <td>${fmtNum(it.sharpe)}</td>
        <td>${fmtNum(it.max_dd)}</td>
      </tr>`;
    });
    html += '</tbody></table>';
    box.innerHTML = html;
  } catch(e){
    box.innerHTML = '<div class="muted">Erro a ler portfolio: '+e+'</div>';
  }
}

  // --------------------------------------------------
  // Refresh logic + spinner visual
  // --------------------------------------------------
  const spinner = document.createElement('span');
  spinner.innerHTML = ' ⟳';
  spinner.style.animation = 'spin 1s linear infinite';
  spinner.style.display = 'none';
  spinner.style.marginLeft = '5px';
  spinner.style.color = '#888';
  if ($refreshBtn) $refreshBtn.parentNode.insertBefore(spinner, $refreshBtn.nextSibling);

  const style = document.createElement('style');
  style.textContent = `
  @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
  `;
  document.head.appendChild(style);

  async function refreshAll() {
    try {
      if ($refreshBtn) {
        $refreshBtn.disabled = true;
        spinner.style.display = 'inline-block';
      }
      await Promise.all([loadHealth(), loadStrategies(), loadPortfolioBox()]);
    } finally {
      if ($refreshBtn) {
        $refreshBtn.disabled = false;
        spinner.style.display = 'none';
      }
    }
  }

  // --------------------------------------------------
  // Botões principais
  // --------------------------------------------------
  $refreshBtn?.addEventListener('click', refreshAll);

  document.getElementById('runDueBtn')?.addEventListener('click', async ()=>{
    try{
      const j = await api('/retrain_run_due', {method:'POST'});
      appendOut(JSON.stringify(j,null,2));
      const st = await api('/status');
      const lf = st?.retrain?.log_file || 'scheduler_retrain.log';
      await showLog(basename(lf), 400);
      await refreshAll();
    }catch(e){ appendOut('erro: '+e); }
  });

  document.getElementById('runAllBtn')?.addEventListener('click', async ()=>{
    try{
      const j = await api('/retrain_run_all', {method:'POST'});
      appendOut(JSON.stringify(j,null,2));
      const st = await api('/status');
      const lf = st?.retrain?.log_file || 'scheduler_retrain.log';
      await showLog(basename(lf), 400);
      await refreshAll();
    }catch(e){ appendOut('erro: '+e); }
  });

  document.getElementById('rebuildBtn')?.addEventListener('click', async ()=>{
    try{
      const j = await api('/retrain_rebuild', {method:'POST'});
      appendOut(JSON.stringify(j,null,2));
      await loadStrategies();
    }catch(e){ appendOut('erro: '+e); }
  });

  document.getElementById('viewScheduleBtn')?.addEventListener('click', ()=>{
    window.open((window.API_ORIGIN||'') + (window.API_BASE||'') + '/view_schedule', '_blank');
  });

  async function proc(action){
    const tgt = document.getElementById('procTarget').value;
    try{
      const j = await api('/proc_run', {
        method:'POST',
        body: JSON.stringify({target:tgt, action})
      });
      appendOut(JSON.stringify(j,null,2));
      await refreshAll();
    }catch(e){ appendOut('erro: '+e); }
  }

  document.getElementById('procUpBtn')?.addEventListener('click', ()=>proc('start'));
  document.getElementById('procDownBtn')?.addEventListener('click', ()=>proc('stop'));
  document.getElementById('procRestartBtn')?.addEventListener('click', ()=>proc('restart'));

  // --------------------------------------------------
  // Inicialização
  // --------------------------------------------------
  await refreshAll();
  startAutoRefresh(true);

  // ====== LOG VIEWER (inclui watcher/emitter; extensível para gov/prom) ======
  (function(){
    const logsCard = document.querySelector('#strategies')?.closest('.card')?.nextElementSibling;
    const logArea = document.getElementById('out');
    if (!logsCard || !logArea) return;

    const bar = document.createElement('div');
    bar.className = 'actions';
    bar.style.gap = '8px';
    bar.style.marginBottom = '8px';

    const sel = document.createElement('select');
    sel.id = 'logSelect';

    const tailN = document.createElement('input');
    tailN.type = 'number'; tailN.min = '50'; tailN.value = '200';
    tailN.style.width='80px'; tailN.title='N últimas linhas';

    const btnLoad = document.createElement('button');
    btnLoad.className='btn-small'; btnLoad.textContent='Abrir log';

    const btnRefresh = document.createElement('button');
    btnRefresh.className='btn-small'; btnRefresh.textContent='Atualizar';

    bar.appendChild(sel); bar.appendChild(tailN); bar.appendChild(btnLoad); bar.appendChild(btnRefresh);
    logsCard.insertBefore(bar, logArea);

    async function populateLogs(){
      try{
        const j = await api('/logs/list');
        sel.innerHTML = '';
        (j.files || []).forEach(f=>{
          const o=document.createElement('option');
          o.value = f.name;
          o.textContent = f.name;
          sel.appendChild(o);
        });
        if (!sel.value){
          ['executor.log','scheduler_retrain.log','retrain.log','watch_signals.log','emitter.log','governor.log','promoter.log']
            .forEach(n=>{
              const o=document.createElement('option'); o.value=n; o.textContent=n; sel.appendChild(o);
            });
        }
      }catch(e){
        ['executor.log','scheduler_retrain.log','retrain.log','watch_signals.log','emitter.log','governor.log','promoter.log']
          .forEach(n=>{
            const o=document.createElement('option'); o.value=n; o.textContent=n; sel.appendChild(o);
          });
      }
    }

    async function loadLog(){
      const name = sel.value;
      const n = Math.max(10, parseInt(tailN.value||'200',10));
      try{
        const txt = await api(`/logs/get?name=${encodeURIComponent(name)}&n=${n}`);
        logArea.value = typeof txt==='string'? txt : JSON.stringify(txt,null,2);
        logArea.scrollTop = logArea.scrollHeight;
      }catch(e){
        logArea.value = 'Erro a carregar log: ' + e;
      }
    }

    btnLoad.addEventListener('click', loadLog);
    btnRefresh.addEventListener('click', loadLog);

    (async ()=>{ await populateLogs(); setTimeout(loadLog, 300); })();
  })();

})();