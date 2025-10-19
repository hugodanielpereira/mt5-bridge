// app/ui/static/js/theme.js
(function(){
  const KEY = "UI_THEME"; // "light" | "dark" | null
  const root = document.documentElement;

  function applyTheme(mode){
    root.classList.remove("theme-light","theme-dark");
    if (mode === "light") root.classList.add("theme-light");
    if (mode === "dark")  root.classList.add("theme-dark");
  }

  // tenta ler do localStorage; se não houver, deixa o @media decidir
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch(e) {}
  if (saved === "light" || saved === "dark") applyTheme(saved);

  // botão (se existir na página)
  function currentMode(){
    if (root.classList.contains("theme-dark")) return "dark";
    if (root.classList.contains("theme-light")) return "light";
    // sem escolha explícita: deteta preferencia do SO
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
      ? "dark" : "light";
  }

  function setMode(mode){
    applyTheme(mode);
    try { localStorage.setItem(KEY, mode); } catch(e) {}
    // acessório: aria-label do botão
    const btn = document.querySelector('[data-action="toggle-theme"]');
    if (btn) btn.setAttribute('aria-label', mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
  }

  function toggle(){
    setMode(currentMode() === "dark" ? "light" : "dark");
  }

  document.addEventListener("DOMContentLoaded", ()=>{
    const btn = document.querySelector('[data-action="toggle-theme"]');
    if (btn) btn.addEventListener('click', toggle);
    // assegura label correta logo ao entrar
    setMode(currentMode());
  });
})();