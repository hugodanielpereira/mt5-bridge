// bridges\mt5-bridge\app\ui\static\js\control.js
(async function () {
  const $key = document.querySelector("#api-key");
  const $save = document.querySelector("#save-key");
  const $out = document.querySelector("#ops-output");

  // carregar / guardar API key
  $key.value = localStorage.getItem("X_API_KEY") || "";
  $save.addEventListener("click", () => {
    localStorage.setItem("X_API_KEY", $key.value.trim());
    $out.value += "✓ API key guardada.\n";
    $out.scrollTop = $out.scrollHeight;
  });

  async function call(target, action, visible=false) {
    $out.value += `\n> ${target} ${action}${visible?" (visível)":" (captura)"}…\n`;
    $out.scrollTop = $out.scrollHeight;
    const body = { target, action, visible };

    try {
      const absUrl = (window.API_ORIGIN || '') + '/ui/ops/run';
      const resp = await fetch(absUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": (localStorage.getItem("X_API_KEY") || "").trim(),
        },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      var data = await resp.json();
    } catch (e) {
      try {
        var data = await api('/ops/run', {
          method: 'POST',
          headers: { "X-API-Key": (localStorage.getItem("X_API_KEY") || "").trim() },
          body: JSON.stringify(body)
        });
      } catch(e2){
        $out.value += `✗ Falha: ${e2}\n`;
        return;
      }
    }

    const rc = (typeof data.rc === "number") ? data.rc : NaN;
    $out.value += `cmd: ${data.cmd || "(desconhecido)"}\n`;
    $out.value += `cwd: ${data.cwd || "(n/a)"}\n`;
    $out.value += `rc: ${Number.isNaN(rc) ? "(sem rc)" : rc}\n`;
    if (data.stdout) $out.value += `stdout:\n${data.stdout}\n`;
    if (data.stderr) $out.value += `stderr:\n${data.stderr}\n`;
    $out.value += data.ok ? "✓ OK\n" : "✗ FALHA\n";
    $out.scrollTop = $out.scrollHeight;
  }

  // 🔧 Liga TODOS os botões (inclui watcher/emitter + governor/promoter)
  const wiring = [
    // bridge
    ["bridge","start"],["bridge","stop"],["bridge","restart"],
    ["bridge","start","vis"],["bridge","restart","vis"],
    // executor
    ["executor","start"],["executor","stop"],["executor","restart"],
    ["executor","start","vis"],["executor","restart","vis"],
    // scheduler (retrain)
    ["scheduler","start"],["scheduler","stop"],["scheduler","restart"],
    ["scheduler","start","vis"],["scheduler","restart","vis"],
    // watcher
    ["watcher","start"],["watcher","stop"],["watcher","restart"],
    ["watcher","start","vis"],["watcher","restart","vis"],
    // emitter
    ["emitter","start"],["emitter","stop"],["emitter","restart"],
    ["emitter","start","vis"],["emitter","restart","vis"],
    // governor (opcional)
    ["governor","start"],["governor","stop"],["governor","restart"],
    ["governor","start","vis"],["governor","restart","vis"],
    // promoter (opcional)
    ["promoter","start"],["promoter","stop"],["promoter","restart"],
    ["promoter","start","vis"],["promoter","restart","vis"],
    // all
    ["all","start"],["all","stop"],["all","restart"],
  ];

  wiring.forEach(([t,a,mode])=>{
    const id = mode==="vis" ? `#btn-${t}-${a}-vis` : `#btn-${t}-${a}`;
    const b = document.querySelector(id);
    if (b) b.addEventListener("click", ()=>call(t,a, mode==="vis"));
  });
})();