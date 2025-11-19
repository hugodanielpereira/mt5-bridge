//bridges\mt5-bridge\app\ui\static\js\common.js
// -----------------------------------------------------------
// Helpers globais + API base configurável
// -----------------------------------------------------------
(function(){
  window.cls = function(v){ return v===true?'ok':(v===false?'bad':'warn'); };
  window.fmtNum = function(x){
    if(x===null||x===undefined||x==='-') return '-';
    const n=Number(x);
    if(!isFinite(n)) return '-';
    return n%1===0?String(n):n.toFixed(1);
  };
  window.setOut = function(s){ const el=document.getElementById('out'); if(el) el.textContent = s || ''; };
  window.appendOut = function(s){ const el = document.getElementById('out'); if(!el) return; el.textContent += (s||'') + "\n"; el.scrollTop = el.scrollHeight; };

  function buildUrl(p){
    const origin = (window.API_ORIGIN || "");
    const base   = (window.API_BASE   || "");
    if (/^https?:\/\//.test(p)) return p;                    // absoluto
    if (p.startsWith('/ui/api/')) return origin + p;         // absoluto para API (sem BASE)
    if (p.startsWith('/')) return origin + base + p;         // relativo à base
    return origin + base + '/' + p;
  }

  window.api = async function(path, opts={}){
    const url = buildUrl(path);
    const r = await fetch(url, Object.assign({headers:{'Content-Type':'application/json'}}, opts));
    if(!r.ok) throw new Error('HTTP '+r.status);
    const ct=r.headers.get('content-type')||'';
    return ct.includes('application/json')?await r.json():await r.text();
  };
})();

// -----------------------------------------------------------
// Tabs internas (Dashboard / Ops / Control) — compat .tab-link/.tab-btn
// -----------------------------------------------------------
(function(){
  function activate(name){
    document.querySelectorAll('.tab-link,.tab-btn').forEach(a=>a.classList.remove('active'));
    const link = document.querySelector(`.tab-link[data-tab="${name}"], .tab-btn[data-tab="${name}"]`);
    if (link) link.classList.add('active');
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    const pane = document.getElementById('tab-'+name);
    if (pane) pane.classList.add('active');
  }
  document.addEventListener('click', (e)=>{
    const t = e.target.closest('.tab-link, .tab-btn');
    if (!t) return;
    e.preventDefault();
    activate(t.dataset.tab);
    history.replaceState(null,'',`#${t.dataset.tab}`);
  });
  (function(){ // hash / ?tab=
    const params = new URLSearchParams(location.search);
    const want = params.get('tab') || location.hash.replace('#','');
    if (want) activate(want);
  })();
})();

// -----------------------------------------------------------
// Theme Manager (data-theme no <html>)
// -----------------------------------------------------------
(function(){
  const THEME_KEY = 'ui.theme';
  const root = document.documentElement;

  function setMeta(theme){
    let meta = document.querySelector('meta[name="color-scheme"][content]');
    if (!meta){ meta = document.createElement('meta'); meta.setAttribute('name','color-scheme'); document.head.appendChild(meta); }
    meta.setAttribute('content', theme === 'dark' ? 'dark light' : 'light dark');
  }
  function applyTheme(theme){
    const t = (theme === 'dark' || theme === 'light') ? theme : 'light';
    root.setAttribute('data-theme', t); setMeta(t);
  }
  function getStored(){ try { const t = localStorage.getItem(THEME_KEY); return (t==='dark'||t==='light')?t:null; } catch(_) { return null; } }
  function setStored(t){ try { localStorage.setItem(THEME_KEY, t); } catch(_) {} }
  function getPreferred(){
    const saved = getStored(); if (saved) return saved;
    try { return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light'; }
    catch(_) { return 'light'; }
  }

  window.applyTheme = applyTheme;
  window.toggleTheme = function(){
    const current = root.getAttribute('data-theme') || getPreferred();
    const next = current === 'dark' ? 'light' : 'dark';
    setStored(next); applyTheme(next);
    const btn = document.getElementById('themeToggle'); if (btn) btn.textContent = (next === 'dark' ? '☀️' : '🌙');
    return next;
  };
  window.initTheme = function(){
    const t = getPreferred(); applyTheme(t);
    const btn = document.getElementById('themeToggle'); if (btn) btn.textContent = (t === 'dark' ? '☀️' : '🌙');
    try {
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      const onSys = (e)=>{ if (!getStored()) applyTheme(e.matches ? 'dark' : 'light'); };
      if (mq.addEventListener) mq.addEventListener('change', onSys);
      else if (mq.addListener) mq.addListener(onSys);
    } catch(_) {}
  };

  document.addEventListener('DOMContentLoaded', function(){
    window.initTheme();
    const btn = document.getElementById('themeToggle'); if (btn) btn.addEventListener('click', window.toggleTheme);
  });
})();