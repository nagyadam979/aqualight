#pragma once
#include <Arduino.h>

const char INDEX_HTML[] PROGMEM = R"AQUAHTML(<!doctype html>
<html lang="hu">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1116">
<title>AquaLight · Akváriumvilágítás</title>
<style>
:root{color-scheme:dark;--bg:#0b1116;--panel:#141e25;--line:#293740;--text:#eff5f7;--muted:#91a4af;--warm:#f3ba72;--cool:#81cdec;--mint:#80c6ad;--danger:#ff9e98;--radius:22px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-synthesis:none}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 12% 0%,#46342220,transparent 42%),radial-gradient(ellipse at 95% 20%,#21516a1c,transparent 45%),var(--bg);color:var(--text);font-size:15px;line-height:1.5;min-width:300px}button,input{font:inherit}button{cursor:pointer;-webkit-tap-highlight-color:transparent}button:disabled{opacity:.4;cursor:not-allowed}button,input,summary{touch-action:manipulation}button:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--cool);outline-offset:4px}button{min-height:44px}a{color:var(--cool)}[hidden]{display:none!important}
.app{width:min(100% - 32px,920px);margin:auto;padding:23px 0 max(32px,env(safe-area-inset-bottom))}.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px;gap:12px}.brand{display:flex;align-items:center;gap:11px;font-size:20px;font-weight:680;letter-spacing:-.7px}.brandmark{height:37px;width:37px;border:1px solid #8fb8c040;border-radius:12px;display:grid;place-items:center;background:linear-gradient(135deg,#e6b16c20,#7ec6e520)}.brandmark svg{width:26px;height:26px}.topright{display:flex;gap:9px;align-items:center}.connection{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:6px}.dot{width:6px;height:6px;border-radius:50%;background:var(--muted)}.connection.online .dot{background:var(--mint);box-shadow:0 0 10px #80c6ad60}.connection.offline .dot{background:var(--danger)}.iconbtn{border:1px solid var(--line);border-radius:13px;background:#141e2570;color:var(--muted);width:44px;display:grid;place-items:center}.iconbtn svg{width:19px;height:19px}.heading{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:20px}.eyebrow{color:var(--muted);font-size:10px;font-weight:650;letter-spacing:1.8px;text-transform:uppercase;margin:0 0 5px}h1{font-size:clamp(22px,4.6vw,30px);line-height:1.2;letter-spacing:-.9px;font-weight:630;margin:0}.clockblock{text-align:right;flex:none}.clock{font-size:25px;font-weight:500;letter-spacing:-.7px;font-variant-numeric:tabular-nums}.clocklabel{color:var(--muted);font-size:10px;margin-top:1px}.sensors{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:18px}.sensor{border:1px solid var(--line);background:#141e2590;border-radius:17px;padding:14px 16px;position:relative}.sensor-label{font-size:12px;color:var(--muted);display:flex;gap:7px;align-items:center}.sensor-label svg{width:16px;height:16px;stroke:var(--mint)}.sensor-value{font-size:28px;letter-spacing:-1px;line-height:1.25;font-weight:550;margin-top:7px;font-variant-numeric:tabular-nums}.sensor-value small{font-size:14px;font-weight:400;letter-spacing:0;color:var(--muted);margin-left:4px}.sensor-note{font-size:10px;color:var(--muted);margin-top:4px}.sensor.invalid .sensor-label svg{stroke:var(--danger)}
.notices{display:grid;gap:8px}.notice{border-radius:13px;padding:11px 13px;border:1px solid #f3ba7240;background:#f3ba720c;color:#f4cc99;font-size:12px;margin-bottom:12px}.notice.danger{color:#ffb6b1;border-color:#ff9e9840;background:#ff9e980c}.notice button{border:0;background:transparent;color:inherit;padding:0 4px;text-decoration:underline;font-size:12px}.modebar{background:#10191f;border:1px solid var(--line);border-radius:15px;padding:5px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:5px;margin-bottom:18px}.modebtn{border:1px solid transparent;background:transparent;border-radius:10px;color:var(--muted);font-size:13px;font-weight:550;display:flex;gap:7px;align-items:center;justify-content:center;transition:background .18s,border .18s,color .18s}.modebtn svg{width:15px;height:15px;stroke:currentColor}.modebtn[aria-pressed="true"]{background:linear-gradient(115deg,#f3ba7218,#81cdec18);border-color:#9cb0b44f;color:var(--text)}.modebtn[data-mode="off"][aria-pressed="true"]{background:#ffffff0c;border-color:#73859060}.modebtn.pending{opacity:.6}.panel{background:linear-gradient(145deg,#19252d,#141e25);border:1px solid var(--line);border-radius:var(--radius);padding:19px;margin-bottom:16px}.panelhead{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:6px}.panelhead h2,.sectionhead h2{font-size:15px;font-weight:600;letter-spacing:-.2px;margin:0}.subtle{color:var(--muted);font-size:11px}.badge{border:1px solid #80c6ad35;background:#80c6ad0d;border-radius:7px;padding:3px 8px;font-size:10px;white-space:nowrap;color:var(--mint)}.badge.draft{border-color:#f3ba7240;color:var(--warm);background:#f3ba720c}.graphlegend{display:flex;gap:20px;align-items:center;margin:13px 0 0;color:var(--muted);font-size:11px}.legenditem{display:flex;align-items:center;gap:6px}.swatch{width:14px;height:3px;border-radius:2px;background:var(--warm)}.swatch.cool{background:var(--cool)}.chart{width:100%;height:224px;overflow:hidden;margin-top:4px}.chart svg{display:block;width:100%;height:224px;overflow:visible}.graphfoot{display:flex;align-items:center;justify-content:space-between;gap:10px;padding-top:1px;color:var(--muted);font-size:10px}.liveoutputs{display:flex;gap:12px;font-variant-numeric:tabular-nums}.liveoutputs strong{font-weight:600}.warmtext{color:var(--warm)}.cooltext{color:var(--cool)}
.sectionhead{display:flex;justify-content:space-between;gap:12px;align-items:center;margin:23px 2px 12px}.channels{display:grid;gap:12px;grid-template-columns:1fr 1fr}.channel{--channel:var(--warm);border:1px solid #f3ba7228;background:linear-gradient(145deg,#241f1d,#151d23 80%);border-radius:19px;padding:17px 17px 19px}.channel.cool{--channel:var(--cool);border-color:#81cdec28;background:linear-gradient(145deg,#172830,#151d23 80%)}.channeltop{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:17px}.channelname{font-size:14px;font-weight:550}.channelmeta{font-size:10px;color:var(--muted);margin-top:1px}.channelsymbol{color:var(--channel);display:flex;align-items:center;gap:9px}.channelsymbol svg{width:21px;height:21px;stroke:currentColor}.percent{color:var(--channel);font-size:25px;font-weight:500;letter-spacing:-.8px;font-variant-numeric:tabular-nums}.percent small{font-size:12px;letter-spacing:0;margin-left:2px}.sliderrow{display:flex;align-items:center;gap:12px}.stepbtn{flex:none;width:44px;min-height:44px;border-radius:50%;background:#ffffff03;border:1px solid #ffffff20;color:var(--text);font-size:23px;font-weight:300;padding:0;line-height:1}.stepbtn:active{background:#ffffff10}.slider{--fill:0%;-webkit-appearance:none;appearance:none;width:100%;height:44px;border-radius:4px;background:transparent;margin:0;min-width:0;outline:none;cursor:pointer;touch-action:pan-y}.slider::-webkit-slider-runnable-track{height:5px;border-radius:4px;background:linear-gradient(to right,var(--channel) var(--fill),#ffffff18 var(--fill))}.slider::-moz-range-track{height:5px;border-radius:4px;background:linear-gradient(to right,var(--channel) var(--fill),#ffffff18 var(--fill))}.slider::-webkit-slider-thumb{-webkit-appearance:none;margin-top:-9.5px;width:24px;height:24px;border-radius:50%;background:var(--channel);border:3px solid #182027;box-shadow:0 0 0 1px var(--channel),0 0 16px #ffffff08}.slider::-moz-range-thumb{width:18px;height:18px;border-radius:50%;background:var(--channel);border:3px solid #182027;box-shadow:0 0 0 1px var(--channel)}
.program{margin-top:22px}.programintro{font-size:11px;color:var(--muted);margin:4px 0 17px;max-width:580px}.textbtn{border:0;background:transparent;color:var(--muted);font-size:11px;padding:0 4px;white-space:nowrap}.textbtn:hover{color:var(--text)}.tablehead,.pointrow{display:grid;grid-template-columns:89px minmax(0,1fr) minmax(0,1fr) 44px;gap:9px;align-items:center}.tablehead{font-size:10px;color:var(--muted);margin:0 0 8px;padding:0 1px}.tablehead span:nth-child(2){color:var(--warm)}.tablehead span:nth-child(3){color:var(--cool)}.pointrow{margin-bottom:9px}.pointrow input{width:100%;min-width:0;border-radius:10px;border:1px solid var(--line);height:44px;padding:9px 10px;background:#0d151c;color:var(--text);font-size:13px;font-variant-numeric:tabular-nums}.pointrow input[type="time"]{padding:7px 6px;letter-spacing:-.4px;min-height:44px}.pointrow input[type="number"]{-moz-appearance:textfield;padding-right:20px}.pointrow input::-webkit-inner-spin-button{appearance:none}.numberwrap{position:relative}.numberwrap:after{content:"%";position:absolute;right:9px;top:12px;font-size:11px;pointer-events:none;color:#6e838f}.remove{background:transparent;border:1px solid var(--line);border-radius:10px;color:var(--muted);width:44px;height:44px;display:grid;place-items:center;padding:0}.remove svg{width:17px;height:17px;stroke:currentColor}.remove:hover{border-color:#ff9e9860;color:var(--danger)}.editmessage{font-size:11px;color:var(--muted);min-height:18px;margin:11px 0}.editmessage.error{color:var(--danger)}.programactions{display:flex;align-items:center;gap:10px}.btn{border:1px solid var(--line);background:#ffffff04;color:var(--text);padding:10px 14px;border-radius:11px;font-size:12px;font-weight:550;display:inline-flex;align-items:center;justify-content:center;gap:7px}.btn svg{width:15px;height:15px;stroke:currentColor}.btn.primary{background:#d4e8e8;color:#122027;border-color:#d4e8e8;box-shadow:0 3px 15px #00000014}.btn.primary:hover{background:#ecf7f7}.programactions .btn:first-child{flex:1}.programactions .primary{flex:1.1}.settings{margin-top:19px;border:1px solid var(--line);border-radius:18px;background:#111b2280;overflow:hidden}.settings summary{list-style:none;cursor:pointer;min-height:58px;padding:16px 18px;display:flex;align-items:center;justify-content:space-between;gap:14px;font-size:13px;font-weight:550}.settings summary::-webkit-details-marker{display:none}.settings summary svg{width:16px;height:16px;stroke:var(--muted);transition:transform .2s}.settings[open] summary svg{transform:rotate(180deg)}.settingsbody{padding:0 18px 19px}.settingsection{border-top:1px solid var(--line);padding-top:16px;margin-top:1px}.settingsection+.settingsection{margin-top:20px}.settingsection h3{margin:0 0 6px;font-size:12px;font-weight:600}.settingsection p{font-size:11px;color:var(--muted);margin:5px 0 13px}.networkinfo{display:grid;grid-template-columns:90px minmax(0,1fr);gap:7px 10px;font-size:11px;margin:12px 0 16px}.networkinfo dt{color:var(--muted)}.networkinfo dd{margin:0;overflow-wrap:anywhere}.wifiform{display:grid;grid-template-columns:1fr 1fr;gap:11px}.field{display:flex;flex-direction:column;gap:5px;font-size:11px;color:var(--muted);min-width:0}.field input{background:#0c151b;border:1px solid var(--line);border-radius:10px;min-width:0;width:100%;height:44px;padding:10px;color:var(--text);font-size:13px}.wifiform .btn{grid-column:1/-1;justify-self:start;margin-top:2px}.footer{font-size:10px;color:#6b818e;text-align:center;padding-top:20px}.toast{position:fixed;z-index:10;bottom:max(22px,env(safe-area-inset-bottom));left:50%;transform:translate(-50%,20px);opacity:0;pointer-events:none;width:max-content;max-width:min(420px,calc(100% - 32px));border:1px solid #80c6ad55;background:#19342e;color:#d9f3e9;border-radius:13px;padding:12px 17px;box-shadow:0 10px 40px #0009;font-size:12px;transition:opacity .2s,transform .2s}.toast.show{opacity:1;transform:translate(-50%,0)}.toast.error{background:#3b2528;border-color:#ff9e9860;color:#ffd1ce}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(min-width:700px){.app{padding-top:31px}.topbar{margin-bottom:34px}.heading{margin-bottom:23px}.sensors{gap:16px}.sensor{padding:17px 20px}.sensor-value{font-size:32px}.modebar{max-width:440px}.panel{padding:23px}.chart,.chart svg{height:260px}.tablehead,.pointrow{grid-template-columns:120px minmax(0,1fr) minmax(0,1fr) 44px;gap:14px}.pointrow input{font-size:14px}.pointrow input[type="time"]{padding-left:13px}.programactions{justify-content:flex-end}.programactions .btn:first-child,.programactions .primary{flex:none;min-width:155px}.channels{gap:16px}.channel{padding:21px}.settingsbody{padding:0 22px 23px}.settings summary{padding:17px 22px}.wifiform{grid-template-columns:1fr 1fr auto}.wifiform .btn{grid-column:auto;align-self:end;margin:0;white-space:nowrap}}
@media(max-width:360px){.app{width:calc(100% - 24px)}.connection{font-size:10px}.panel{padding:15px}.tablehead,.pointrow{grid-template-columns:80px minmax(0,1fr) minmax(0,1fr) 44px;gap:6px}.channel{padding:15px}.heading{gap:8px}.clock{font-size:23px}}@media(max-width:599px){.field input,.pointrow input{font-size:16px}.channels{grid-template-columns:1fr}.wifiform{grid-template-columns:1fr}.graphlegend{gap:15px}.clocklabel{max-width:100px}.modebar{margin-bottom:16px}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}

.modebar{grid-template-columns:1.45fr 1fr}.graphlegend{gap:9px;margin-top:16px}.curvebtn{--curve:var(--warm);display:flex;align-items:center;gap:8px;text-align:left;flex:1;min-width:0;min-height:54px;border:1px solid var(--line);border-radius:12px;padding:8px 10px;background:#0e171d;color:var(--muted);font-size:12px;font-weight:550;line-height:1.3}.curvebtn.cool{--curve:var(--cool)}.curvebtn small{display:block;font-size:10px;font-weight:400;margin-top:2px;opacity:.75}.curvebtn[aria-pressed="true"]{border-color:var(--curve);background:color-mix(in srgb,var(--curve) 8%,#101a21);color:var(--text)}.curvebtn .swatch{width:9px;height:9px;border-radius:50%;flex:none}.curvecount{font-size:10px;font-weight:400;margin-left:auto;color:var(--muted);white-space:nowrap}.chart,.chart svg{height:300px;touch-action:none;user-select:none;-webkit-user-select:none}.point-handle{cursor:grab;outline:none}.point-handle:active{cursor:grabbing}.point-handle:focus-visible .point-knob{stroke:#fff;stroke-width:3}.point-handle .point-hit{pointer-events:all}.pointinspector{--curve:var(--warm);display:flex;align-items:center;justify-content:space-between;gap:15px;margin-top:15px;padding:12px 13px;background:#0d171d;border:1px solid var(--line);border-radius:13px}.inspectlabel{display:block;color:var(--curve);font-size:10px}.pointvalue{font-size:15px;font-variant-numeric:tabular-nums;margin-top:2px}.graphhint{color:var(--muted);font-size:11px;line-height:1.6;margin:12px 1px 0}.manualbody{padding:0 17px 18px}.manualbody .sectionhead{margin:0 0 15px;align-items:center}.manualbody .modebtn{font-size:11px;max-width:190px}.manualbody .subtle{font-size:10px;text-align:right}.precision{--curve:var(--warm)}.precision .tablehead,.precision .pointrow{grid-template-columns:105px minmax(0,1fr) 44px;gap:12px}.precision .tablehead span:nth-child(2){color:var(--curve)}.precision .btn{margin-top:5px}.precision .settingsection p{margin-bottom:17px}.precision .pointrow.selected input{border-color:var(--curve)}
@media(min-width:700px){.chart,.chart svg{height:330px}.curvebtn{padding:10px 14px}.manualbody{padding:0 22px 23px}.precision .tablehead,.precision .pointrow{grid-template-columns:150px minmax(0,1fr) 44px}}@media(max-width:360px){.curvebtn{padding:8px;font-size:11px;gap:6px}.curvecount{font-size:9px}.precision .tablehead,.precision .pointrow{grid-template-columns:100px minmax(0,1fr) 44px;gap:8px}}
.manualactions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}.manualactions .modebtn{max-width:none;min-height:48px;font-size:11px;line-height:1.4}.manualstatus{min-height:18px;font-size:11px;color:var(--muted);margin:0 0 15px}
</style>
</head>
<body>
<main class="app">
  <header class="topbar">
    <div class="brand"><span class="brandmark" aria-hidden="true"><svg viewBox="0 0 30 30" fill="none"><path d="M7 14a8 8 0 0 1 16 0" stroke="#f3ba72" stroke-width="1.7"/><path d="M4 19c4-4 7 4 11 0s7 4 11 0M7 24c3-3 5 3 8 0s5 3 8 0" stroke="#81cdec" stroke-width="1.7" stroke-linecap="round"/></svg></span>AquaLight</div>
    <div class="topright"><span id="connection" class="connection" role="status"><span class="dot"></span><span id="connectionText">Kapcsolódás</span></span><button class="iconbtn" id="settingsShortcut" aria-label="Beállítások megnyitása" title="Beállítások"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 7h16M4 17h16M8 4v6M16 14v6" stroke-linecap="round"/></svg></button></div>
  </header>
  <div class="heading"><div><p class="eyebrow">A fény napi ritmusa</p><h1>Akváriumvilágítás</h1></div><div class="clockblock"><div id="clock" class="clock">--:--</div><div id="clockLabel" class="clocklabel">Az eszköz ideje</div></div></div>
  <section class="sensors" aria-label="Élő mérések">
    <div class="sensor" id="temperatureCard"><div class="sensor-label"><svg viewBox="0 0 20 20" fill="none" stroke-width="1.5"><path d="M7 11.5V5a3 3 0 0 1 6 0v6.5a5 5 0 1 1-6 0Z"/><path d="M10 7v7" stroke-linecap="round"/></svg>Hőmérséklet</div><div class="sensor-value"><span id="temperature">—</span><small>°C</small></div><div class="sensor-note" id="temperatureNote">Várakozás a mérésre</div></div>
    <div class="sensor" id="humidityCard"><div class="sensor-label"><svg viewBox="0 0 20 20" fill="none" stroke-width="1.5"><path d="M10 2S4 8.5 4 12a6 6 0 0 0 12 0c0-3.5-6-10-6-10Z"/><path d="M7 12a3 3 0 0 0 3 3" stroke-linecap="round"/></svg>Páratartalom</div><div class="sensor-value"><span id="humidity">—</span><small>%</small></div><div class="sensor-note" id="humidityNote">Várakozás a mérésre</div></div>
  </section>
  <div class="notices" aria-live="polite"><div id="offlineNotice" class="notice danger" hidden>Az eszköz nem elérhető. Újrapróbálkozás folyamatban; a kijelzett értékek nem frissek.</div><div id="pwmNotice" class="notice danger" hidden>A fénykimenetek hibát jeleznek. A vezérlő kikapcsolta a világítást.</div><div id="storageNotice" class="notice danger" hidden>A beállítások mentése nem sikerült. Újraindításkor a módosítások elveszhetnek.</div><div id="clockNotice" class="notice" hidden>Az automata program pontos időre vár, addig a fények kikapcsolva maradnak. <button id="quickClock">Óra beállítása</button></div></div>
  <nav class="modebar" aria-label="Világítási mód">
    <button type="button" class="modebtn" data-mode="auto" aria-pressed="false"><svg viewBox="0 0 20 20" fill="none" stroke-width="1.5"><circle cx="10" cy="10" r="7"/><path d="M10 5.5V10l3 2" stroke-linecap="round"/></svg>Program indítása</button>
    <button type="button" class="modebtn" data-mode="off" aria-pressed="false"><svg viewBox="0 0 20 20" fill="none" stroke-width="1.5"><path d="M10 2v8M6 4.5a7 7 0 1 0 8 0" stroke-linecap="round"/></svg>Ki</button>
  </nav>
  <section class="panel graphpanel" aria-labelledby="graphTitle">
    <div class="panelhead"><h2 id="graphTitle">A nap fényprogramja</h2><span id="modeBadge" class="badge">Várakozás</span></div>
    <div class="graphlegend" aria-label="Szerkesztendő fénycsatorna">
      <button type="button" class="curvebtn warm" data-curve="warm" aria-pressed="true"><span class="swatch"></span><span>Meleg fehér<small>2700 K</small></span><span class="curvecount" id="warmCount">— pont</span></button>
      <button type="button" class="curvebtn cool" data-curve="cool" aria-pressed="false"><span class="swatch cool"></span><span>Hideg fehér<small>6500 K</small></span><span class="curvecount" id="coolCount">— pont</span></button>
    </div>
    <div class="chart" id="chart"><svg id="graph" role="group" aria-label="Szerkeszthető 24 órás fényprogram" aria-describedby="graphHint"></svg></div>
    <div class="graphfoot"><span id="graphCaption">Naponta ismétlődő fényív</span><span class="liveoutputs"><span class="warmtext"><span class="sr-only">Jelenlegi meleg fényerő: </span><strong id="actualWarm">—</strong>%</span><span class="cooltext"><span class="sr-only">Jelenlegi hideg fényerő: </span><strong id="actualCool">—</strong>%</span></span></div>
    <div class="pointinspector" id="pointInspector"><div><span class="inspectlabel" id="selectedChannel">Meleg fehér</span><div class="pointvalue" id="selectedValue">Válassz egy pontot</div></div><button class="remove" type="button" id="removeSelected" aria-label="Kijelölt időpont törlése" disabled><svg viewBox="0 0 20 20" fill="none" stroke-width="1.5"><path d="M4 5h12M8 5V3h4v2M6 5l1 12h6l1-12M9 8v6M11 8v6" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div>
    <p class="graphhint" id="graphHint">Húzd a pontokat az idő és a fényerő állításához. Dupla koppintással új pontot adhatsz a kiválasztott görbéhez.</p>
    <p id="editMessage" class="editmessage" role="status">A program betöltése…</p>
    <div class="programactions"><button type="button" class="btn" id="preset">Mintaprogram</button><button type="button" class="btn primary" id="saveProgram" disabled>Program mentése</button></div>
  </section>
  <details id="manualPanel" class="settings manualpanel"><summary>Kézi vezérlés<svg viewBox="0 0 20 20" fill="none" stroke-width="1.5"><path d="m5 8 5 5 5-5" stroke-linecap="round" stroke-linejoin="round"/></svg></summary><div class="manualbody">
  <div class="manualactions"><button type="button" id="manualEnable" class="btn modebtn" data-mode="manual" aria-pressed="false">Kézi mód bekapcsolása</button><button type="button" id="manualExit" class="btn modebtn" data-mode="auto" aria-pressed="false">Kézi mód kikapcsolása → Automata</button></div><p id="manualStatus" class="manualstatus" aria-live="polite">Előbb kapcsold be a kézi módot.</p>
  <section class="channels" aria-label="Kézi fényerő-szabályozás">
    <div class="channel"><div class="channeltop"><div class="channelsymbol"><svg viewBox="0 0 24 24" fill="none" stroke-width="1.5"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5" stroke-linecap="round"/></svg><div><div class="channelname">Meleg fehér</div><div class="channelmeta">2700 K · lágy, arany fény</div></div></div><output class="percent" for="warmSlider"><span id="warmPercent">0</span><small>%</small></output></div><div class="sliderrow"><button class="stepbtn" data-channel="warm" data-step="-1" aria-label="Meleg fényerő csökkentése">−</button><label class="sr-only" for="warmSlider">Meleg fehér fényereje százalékban</label><input id="warmSlider" class="slider" type="range" min="0" max="100" step="1" value="0"><button class="stepbtn" data-channel="warm" data-step="1" aria-label="Meleg fényerő növelése">+</button></div></div>
    <div class="channel cool"><div class="channeltop"><div class="channelsymbol"><svg viewBox="0 0 24 24" fill="none" stroke-width="1.5"><path d="M12 2v20M3.3 7l17.4 10M3.3 17 20.7 7M8.5 4 12 7.5 15.5 4M8.5 20 12 16.5 15.5 20M3.5 11l5-1.3L7.2 5M20.5 13l-5 1.3 1.3 4.7M7.2 19l1.3-4.7-5-1.3M16.8 5l-1.3 4.7 5 1.3" stroke-linecap="round" stroke-linejoin="round"/></svg><div><div class="channelname">Hideg fehér</div><div class="channelmeta">6500 K · tiszta nappali fény</div></div></div><output class="percent" for="coolSlider"><span id="coolPercent">0</span><small>%</small></output></div><div class="sliderrow"><button class="stepbtn" data-channel="cool" data-step="-1" aria-label="Hideg fényerő csökkentése">−</button><label class="sr-only" for="coolSlider">Hideg fehér fényereje százalékban</label><input id="coolSlider" class="slider" type="range" min="0" max="100" step="1" value="0"><button class="stepbtn" data-channel="cool" data-step="1" aria-label="Hideg fényerő növelése">+</button></div></div>
  </section>
  </div></details>
  <details id="precision" class="settings precision"><summary>Pontos értékek<svg viewBox="0 0 20 20" fill="none" stroke-width="1.5"><path d="m5 8 5 5 5-5" stroke-linecap="round" stroke-linejoin="round"/></svg></summary><div class="settingsbody">
    <div class="settingsection"><h3 id="precisionTitle">Meleg fehér · 2700 K</h3><p>A grafikonon kiválasztott csatorna időpontjai. Itt percre és százalékra pontos értékeket adhatsz meg.</p><div class="tablehead" aria-hidden="true"><span>Időpont</span><span>Fényerő</span><span></span></div><div id="points"></div><button type="button" class="btn" id="addPoint">Időpont hozzáadása</button></div>
  </div></details>
  <details id="settings" class="settings"><summary>Hálózat és időbeállítás<svg viewBox="0 0 20 20" fill="none" stroke-width="1.5"><path d="m5 8 5 5 5-5" stroke-linecap="round" stroke-linejoin="round"/></svg></summary><div class="settingsbody">
    <section class="settingsection"><h3>A vezérlő órája</h3><p id="timeInfo">Automatikus internetes időszinkron Wi-Fi-kapcsolaton. Magyar téli–nyári időszámítás.</p><button type="button" class="btn" id="syncClock">Beállítás erről az eszközről</button><p>Teljes áramtalanítás után a program az idő újrabeállításáig várakozik. Kézi módban ilyenkor is szabályozhatsz.</p></section>
    <section class="settingsection"><h3>Wi-Fi-kapcsolat</h3><dl class="networkinfo"><dt>Otthoni hálózat</dt><dd id="wifiStatus">Betöltés…</dd><dt>Webcím</dt><dd id="wifiAddress">—</dd><dt>Saját hálózat</dt><dd id="apName">—</dd><dt>Közvetlen cím</dt><dd id="apAddress">192.168.4.1</dd></dl><form id="wifiForm" class="wifiform"><label class="field" for="wifiSsid">Hálózat neve<input id="wifiSsid" name="ssid" maxlength="32" autocomplete="off" required placeholder="Wi-Fi neve"></label><label class="field" for="wifiPassword">Hálózati jelszó<input id="wifiPassword" name="password" type="password" maxlength="63" autocomplete="new-password" placeholder="Wi-Fi-jelszó"></label><button type="submit" class="btn" id="saveWifi">Mentés és csatlakozás</button></form><p>A vezérlő saját Wi-Fi-hálózata akkor is elérhető marad, ha az otthoni csatlakozás nem sikerül. A saját hálózat jelszavát nem módosítja ez az űrlap.</p></section>
  </div></details>
  <footer class="footer">AquaLight · meleg és hideg fehér fény, a te ritmusodban</footer>
</main>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>

'use strict';
const $ = id => document.getElementById(id);
const names = {warm: 'Meleg fehér', cool: 'Hideg fehér'};
const kelvin = {warm: '2700 K', cool: '6500 K'};
const colors = {warm: '#f3ba72', cool: '#81cdec'};
const modeNames = {off: 'Kikapcsolva', manual: 'Kézi vezérlés', auto: 'Automata program'};
const presetCurve = [{minute: 480, level: 0}, {minute: 540, level: 50}, {minute: 1020, level: 50}, {minute: 1080, level: 0}];
const formatter = new Intl.NumberFormat('hu-HU', {maximumFractionDigits: 1, minimumFractionDigits: 1});
let latest = null, draft = {warm: [], cool: []}, dirty = false, draftRevision = 0, savedSignature = '';
let online = false, refreshPromise = null, toastTimer, saving = false, pointCounter = 0;
let activeCurve = 'warm', selected = {warm: null, cool: null}, geometry = null;
let drag = null, tapCandidate = null, lastTap = null, suppressDoubleUntil = 0;
let writeChain = Promise.resolve(), manualTimer, manualQueued = null, manualSending = false;
let manualEditing = false, manualGuardUntil = 0, modePending = 0, modeTarget = null, controlRevision = 0;
const finite = value => typeof value === 'number' && Number.isFinite(value);
const clamp = (value, min = 0, max = 100) => Math.max(min, Math.min(max, Math.round(Number(value) || 0)));
const hhmm = minute => String(Math.floor(minute / 60)).padStart(2, '0') + ':' + String(minute % 60).padStart(2, '0');
const text = (id, value) => { $(id).textContent = value; };

function notify(message, error = false) {
  const toast = $('toast');
  toast.textContent = message;
  toast.className = 'toast show' + (error ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 4200);
}

async function request(path, data) {
  const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 6500);
  try {
    const options = {signal: abort.signal, cache: 'no-store'};
    if (data !== undefined) {
      options.method = 'POST';
      options.headers = {'Content-Type': 'application/x-www-form-urlencoded', 'X-Aqua-Request': '1'};
      options.body = new URLSearchParams(data).toString();
    }
    const response = await fetch(path, options);
    let result;
    try { result = await response.json(); }
    catch { throw new Error('Az eszköz válasza nem olvasható.'); }
    if (!response.ok || result.ok === false) throw new Error(result.error || 'A művelet nem sikerült.');
    return result;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('A vezérlő nem válaszolt időben.');
    throw error;
  } finally { clearTimeout(timer); }
}

function mutate(path, data, allowed = null) {
  const task = writeChain.catch(() => {}).then(() => !allowed || allowed() ? request(path, data) : null);
  writeChain = task.catch(() => {});
  return task;
}
function canControlManually() {
  return online && latest && latest.mode === 'manual' && latest.pwmOk && modePending === 0;
}

function connection(isOnline) {
  online = isOnline;
  $('connection').className = 'connection ' + (isOnline ? 'online' : 'offline');
  text('connectionText', isOnline ? 'Kapcsolódva' : 'Nincs kapcsolat');
  $('offlineNotice').hidden = isOnline;
  if (!isOnline) {
    for (const item of ['temperature', 'humidity']) {
      text(item + 'Note', 'Nincs friss kapcsolat');
      $(item + 'Card').classList.add('invalid');
    }
  }
  updateControls();
}

function updateControls() {
  document.querySelectorAll('.modebtn').forEach(button => {
    button.disabled = !online || (modePending > 0 && button.dataset.mode !== 'off');
    if (button.id === 'manualEnable' && latest && latest.mode === 'manual') button.disabled = true;
    if (button.id === 'manualExit' && (!latest || latest.mode !== 'manual')) button.disabled = true;
    button.classList.toggle('pending', modePending > 0);
  });
  document.querySelectorAll('.stepbtn,.slider').forEach(element => {
    element.disabled = !canControlManually();
  });
  $('addPoint').disabled = !latest || draft[activeCurve].length >= 16;
  $('preset').disabled = !latest;
  $('saveProgram').disabled = !online || !dirty || saving;
  $('syncClock').disabled = !online;
  $('quickClock').disabled = !online;
  $('saveWifi').disabled = !online;
  $('removeSelected').disabled = !selectedPoint() || draft[activeCurve].length <= 2;
  if (latest) text('modeBadge', modePending ? 'Módváltás…' : modeNames[latest.mode] || 'Ismeretlen mód');
}

function setSlider(channel, value) {
  const range = $(channel + 'Slider'), level = clamp(value);
  range.value = level;
  range.style.setProperty('--fill', level + '%');
  text(channel + 'Percent', level);
  range.setAttribute('aria-valuetext', level + ' százalék');
}

function validCurve(points) {
  return Array.isArray(points) && points.length >= 2 && points.length <= 16 && points.every((point, index) =>
    Number.isInteger(point.minute) && point.minute >= 0 && point.minute < 1440 &&
    Number.isInteger(point.level) && point.level >= 0 && point.level <= 100 &&
    (!index || points[index - 1].minute < point.minute));
}

function validSchedule(schedule) {
  return schedule && validCurve(schedule.warm) && validCurve(schedule.cool);
}

function toDraft(points) {
  return points.map(point => ({id: String(++pointCounter), time: hhmm(point.minute), level: String(point.level)}));
}

function render(state) {
  latest = state;
  connection(true);
  const sensorValid = state.sensorOk && finite(state.temperature) && finite(state.humidity) && finite(state.sensorAgeSec) && state.sensorAgeSec <= 15;
  for (const item of ['temperature', 'humidity']) {
    $(item + 'Card').classList.toggle('invalid', !sensorValid);
    text(item, sensorValid ? formatter.format(state[item]) : '—');
    text(item + 'Note', sensorValid ? 'Élő mérés · ' + Math.floor(state.sensorAgeSec) + ' mp' : 'A szenzor nem ad friss mérést');
  }
  text('clock', state.clockValid && typeof state.localTime === 'string' ? state.localTime.slice(11, 16) : '--:--');
  text('clockLabel', state.clockValid ? 'Budapesti idő' : 'Óra nincs beállítva');
  text('modeBadge', modeNames[state.mode] || 'Ismeretlen mód');
  document.querySelectorAll('.modebtn').forEach(button => button.setAttribute('aria-pressed', String(state.mode === button.dataset.mode)));
  $('pwmNotice').hidden = state.pwmOk;
  $('storageNotice').hidden = state.storageOk;
  $('clockNotice').hidden = state.mode !== 'auto' || state.clockValid;
  text('actualWarm', finite(state.warm) ? Math.round(state.warm) : '—');
  text('actualCool', finite(state.cool) ? Math.round(state.cool) : '—');
  if (!manualSending && !manualQueued && !manualEditing && Date.now() > manualGuardUntil) {
    setSlider('warm', state.mode === 'manual' ? state.manualWarm : state.warm);
    setSlider('cool', state.mode === 'manual' ? state.manualCool : state.cool);
  }
  if (state.mode !== 'manual') {
    clearTimeout(manualTimer); manualQueued = null; manualEditing = false;
  }
  if (modePending) text('manualStatus', modeTarget === 'manual' ? 'Kézi mód bekapcsolása…' : 'Kilépés a kézi módból…');
  else if (state.mode !== 'manual') text('manualStatus', 'A kézi mód ki van kapcsolva. Bekapcsolás után állíthatsz fényerőt.');
  else if (!manualSending && !manualQueued) text('manualStatus', state.savePending ? 'Beállítások mentése…' : 'Kézi mód aktív');
  if (!dirty && !drag && validSchedule(state.schedule)) {
    const signature = JSON.stringify(state.schedule);
    if (signature !== savedSignature) {
      savedSignature = signature;
      draft = {warm: toDraft(state.schedule.warm), cool: toDraft(state.schedule.cool)};
      selected = {warm: null, cool: null};
      renderRows();
    }
  }
  const network = state.network || {};
  text('wifiStatus', network.connected ? (network.ssid || 'Csatlakoztatva') : (network.ssid ? 'Csatlakozás: ' + network.ssid : 'Nincs beállítva'));
  text('wifiAddress', network.connected && network.ip ? network.ip + ' · akvarium.local' : 'Csatlakozz a saját hálózathoz');
  text('apName', network.apActive ? (network.apSsid || 'Elérhető') : 'Nem aktív');
  text('apAddress', network.apIp || '192.168.4.1');
  text('timeInfo', !state.clockValid ? 'Az óra még nincs beállítva. Internetes kapcsolat vagy az alábbi gomb segítségével szinkronizálhatod.' : state.timeSource === 'ntp' ? 'Az idő interneten szinkronizálva. Magyar téli–nyári időszámítás.' : 'Az idő erről az eszközről beállítva. Magyar téli–nyári időszámítás.');
  drawGraph();
  updateInspector();
  updateControls();
}

function refresh(force = false) {
  if (refreshPromise) return force ? refreshPromise.then(() => refresh(true)) : refreshPromise;
  const revision = controlRevision;
  refreshPromise = request('/api/state').then(state => {
    if (revision !== controlRevision) return null; // Ignore a poll started before a mode change.
    render(state);
    return state;
  }).catch(() => {
    if (revision === controlRevision) connection(false);
    return null;
  }).finally(() => { refreshPromise = null; });
  return refreshPromise;
}
async function poll() { await refresh(); setTimeout(poll, 2000); }

function queueManual() {
  if (!canControlManually()) return;
  manualGuardUntil = Date.now() + 1500;
  for (const channel of ['warm', 'cool']) setSlider(channel, $(channel + 'Slider').value);
  manualQueued = {warm: $('warmSlider').value, cool: $('coolSlider').value, revision: controlRevision};
  text('manualStatus', 'Fényerő küldése…');
  clearTimeout(manualTimer);
  manualTimer = setTimeout(sendManual, 200);
}
async function sendManual() {
  if (manualSending || !manualQueued) return;
  const job = manualQueued;
  manualQueued = null;
  const isCurrent = () => job.revision === controlRevision && canControlManually();
  if (!isCurrent()) return;
  manualSending = true;
  let sent = false;
  try {
    sent = !!await mutate('/api/manual', {warm: job.warm, cool: job.cool}, isCurrent);
  } catch (error) {
    if (job.revision === controlRevision) notify(error.message, true);
  } finally {
    manualSending = false;
    if (job.revision === controlRevision) manualGuardUntil = 0;
    if (manualQueued) { clearTimeout(manualTimer); manualTimer = setTimeout(sendManual, 200); }
    else if (job.revision === controlRevision && modePending === 0) {
      const state = await refresh(true);
      if (sent && job.revision === controlRevision && state && state.mode !== 'manual') notify('A kézi mód már nem aktív.', true);
    }
  }
}
async function changeMode(mode) {
  const revision = ++controlRevision;
  clearTimeout(manualTimer);
  manualQueued = null; manualEditing = false; manualGuardUntil = 0;
  if (mode === 'manual') $('manualPanel').open = true;
  modeTarget = mode;
  modePending++;
  text('manualStatus', mode === 'manual' ? 'Kézi mód bekapcsolása…' : 'Kilépés a kézi módból…');
  updateControls();
  try {
    await mutate('/api/mode', {mode});
    const state = await refresh(true);
    if (revision !== controlRevision) return;
    if (!state) throw new Error('A módváltás még nem igazolható. Ellenőrizd a kapcsolatot.');
    if (state.mode !== mode) throw new Error('A vezérlő nem a kért módot jelzi. Próbáld újra.');
    if (mode === 'auto') notify(state.clockValid ? 'Automata program aktív.' : 'Automata mód aktív, az óra beállítására vár.');
  } catch (error) {
    if (revision === controlRevision) { notify(error.message, true); text('manualStatus', error.message); }
  } finally {
    modePending--;
    if (revision === controlRevision) modeTarget = null;
    updateControls();
    if (modePending === 0 && latest && latest.mode === mode) {
      text('manualStatus', latest.mode === 'manual' ? 'Kézi mód aktív' : 'A kézi mód ki van kapcsolva.');
    }
  }
}

function parseDraftCurve(channel) {
  const points = draft[channel];
  if (points.length < 2 || points.length > 16) throw new Error(names[channel] + ': legalább 2, legfeljebb 16 időpontot adj meg.');
  const result = points.map((point, index) => {
    if (!/^\d{2}:\d{2}$/.test(point.time)) throw new Error(names[channel] + ', ' + (index + 1) + '. sor: adj meg érvényes időpontot.');
    const [hours, minutes] = point.time.split(':').map(Number);
    if (hours > 23 || minutes > 59) throw new Error(names[channel] + ': hibás időpont.');
    if (!/^\d{1,3}$/.test(point.level) || Number(point.level) > 100) throw new Error(names[channel] + ': a fényerő 0 és 100% közé essen.');
    return {id: point.id, minute: hours * 60 + minutes, level: Number(point.level)};
  }).sort((a, b) => a.minute - b.minute);
  if (result.some((point, index) => index && point.minute === result[index - 1].minute)) throw new Error(names[channel] + ': ugyanaz az időpont csak egyszer szerepelhet.');
  return result;
}
function parseDraft() { return {warm: parseDraftCurve('warm'), cool: parseDraftCurve('cool')}; }

function updateEditorMessage() {
  let message = dirty ? 'Módosított előnézet · a mentésig nem változik a világítás programja.' : 'A két csatorna időpontjai egymástól függetlenek.', error = false;
  try { parseDraft(); }
  catch (problem) { message = problem.message; error = true; }
  text('editMessage', message);
  $('editMessage').classList.toggle('error', error);
  text('graphCaption', dirty ? 'Előnézet · még nincs mentve' : 'Naponta ismétlődő fényív');
  $('modeBadge').classList.toggle('draft', dirty);
  for (const channel of ['warm', 'cool']) text(channel + 'Count', draft[channel].length + ' pont');
  updateControls();
}
function markDirty() { dirty = true; draftRevision++; updateEditorMessage(); drawGraph(); updateInspector(); }

function selectedPoint() { return draft[activeCurve].find(point => point.id === selected[activeCurve]) || null; }
function updateInspector() {
  const point = selectedPoint();
  text('selectedChannel', names[activeCurve] + ' · ' + kelvin[activeCurve]);
  text('selectedValue', point ? point.time + '  ·  ' + point.level + '%' : 'Válassz egy pontot');
  $('pointInspector').style.setProperty('--curve', colors[activeCurve]);
  $('precision').style.setProperty('--curve', colors[activeCurve]);
  text('precisionTitle', names[activeCurve] + ' · ' + kelvin[activeCurve]);
  updateControls();
}
function activateCurve(channel) {
  if (!['warm', 'cool'].includes(channel)) return;
  activeCurve = channel;
  lastTap = null;
  document.querySelectorAll('.curvebtn').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.curve === channel)));
  renderRows();
  drawGraph();
  updateInspector();
}

function renderRows() {
  const container = $('points');
  container.replaceChildren();
  draft[activeCurve].forEach((point, index) => {
    const row = document.createElement('div');
    row.className = 'pointrow' + (point.id === selected[activeCurve] ? ' selected' : '');
    for (const field of ['time', 'level']) {
      const input = document.createElement('input');
      input.type = field === 'time' ? 'time' : 'number';
      input.value = point[field];
      input.id = 'point-' + point.id + '-' + field;
      input.setAttribute('aria-label', names[activeCurve] + ', ' + (index + 1) + '. időpont · ' + (field === 'time' ? 'idő' : 'fényerő százalékban'));
      if (field === 'time') input.step = '60';
      else { input.min = '0'; input.max = '100'; input.step = '1'; input.inputMode = 'numeric'; }
      input.addEventListener('focus', () => { selected[activeCurve] = point.id; updateInspector(); drawGraph(); });
      input.addEventListener('input', () => { point[field] = input.value; markDirty(); });
      if (field === 'time') row.append(input);
      else { const wrap = document.createElement('div'); wrap.className = 'numberwrap'; wrap.append(input); row.append(wrap); }
    }
    const remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'remove'; remove.disabled = draft[activeCurve].length <= 2;
    remove.setAttribute('aria-label', names[activeCurve] + ', ' + (index + 1) + '. időpont törlése');
    remove.innerHTML = '<svg viewBox="0 0 20 20" fill="none" stroke-width="1.5"><path d="M4 5h12M8 5V3h4v2M6 5l1 12h6l1-12M9 8v6M11 8v6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    remove.addEventListener('click', () => removePoint(point.id));
    row.append(remove); container.append(row);
  });
  updateEditorMessage();
}
function removePoint(id) {
  if (draft[activeCurve].length <= 2) { notify('Legalább két pontot tarts meg ezen a görbén.', true); return false; }
  const index = draft[activeCurve].findIndex(point => point.id === id);
  if (index < 0) return false;
  draft[activeCurve].splice(index, 1);
  if (selected[activeCurve] === id) selected[activeCurve] = null;
  markDirty(); renderRows();
  return true;
}
function nearestFreeMinute(channel, desired, excludeId = null) {
  const used = new Set(draft[channel].filter(point => point.id !== excludeId && /^\d{2}:\d{2}$/.test(point.time)).map(point => Number(point.time.slice(0, 2)) * 60 + Number(point.time.slice(3))));
  desired = clamp(desired, 0, 1439);
  if (!used.has(desired)) return desired;
  for (let distance = 1; distance < 1440; distance++) {
    if (desired + distance < 1440 && !used.has(desired + distance)) return desired + distance;
    if (desired - distance >= 0 && !used.has(desired - distance)) return desired - distance;
  }
  return null;
}
function insertPoint(channel, minute, level) {
  if (!latest || draft[channel].length >= 16) { notify('Egy görbéhez legfeljebb 16 pont adható.', true); return false; }
  try { parseDraftCurve(channel); }
  catch (error) { notify('Előbb javítsd a kiválasztott görbe pontos értékeit.', true); return false; }
  const freeMinute = nearestFreeMinute(channel, minute);
  if (freeMinute === null) return false;
  const point = {id: String(++pointCounter), time: hhmm(freeMinute), level: String(clamp(level))};
  draft[channel].push(point);
  draft[channel].sort((a, b) => a.time.localeCompare(b.time));
  selected[channel] = point.id;
  markDirty(); renderRows();
  return true;
}
function addPoint() {
  const points = draft[activeCurve];
  const last = points.length ? points[points.length - 1] : null;
  const minute = last && /^\d{2}:\d{2}$/.test(last.time) ? (Number(last.time.slice(0, 2)) * 60 + Number(last.time.slice(3)) + 60) % 1440 : 480;
  if (insertPoint(activeCurve, minute, 0)) $('point-' + selected[activeCurve] + '-time').focus();
}
async function saveProgram() {
  let points;
  try { points = parseDraft(); }
  catch (error) { notify(error.message, true); updateEditorMessage(); return; }
  const revision = draftRevision;
  saving = true; text('saveProgram', 'Mentés…'); updateControls();
  try {
    const encode = curve => curve.map(point => point.minute + ',' + point.level).join(';');
    await mutate('/api/program', {warmPoints: encode(points.warm), coolPoints: encode(points.cool)});
    if (revision === draftRevision) { dirty = false; savedSignature = ''; }
    notify('Mindkét csatorna napi fényprogramja elmentve.');
    await refresh();
  } catch (error) { notify(error.message, true); }
  finally { saving = false; text('saveProgram', 'Program mentése'); updateEditorMessage(); }
}

function interpolate(points, minute) {
  if (!points.length) return 0;
  let left = points[points.length - 1], right = points[0], leftMinute = left.minute - 1440, rightMinute = right.minute;
  for (let index = 0; index < points.length; index++) {
    if (points[index].minute > minute) break;
    left = points[index]; leftMinute = left.minute;
    right = points[(index + 1) % points.length];
    rightMinute = right.minute + (index === points.length - 1 ? 1440 : 0);
  }
  return left.level + (right.level - left.level) * (minute - leftMinute) / (rightMinute - leftMinute);
}
function graphCurve(channel) {
  try { return {points: parseDraftCurve(channel), editable: true}; }
  catch { return {points: latest && validSchedule(latest.schedule) ? latest.schedule[channel] : presetCurve, editable: false}; }
}
function drawGraph() {
  const svg = $('graph'), chart = $('chart');
  const focused = document.activeElement;
  const focusedHandle = focused && svg.contains(focused) ? focused.closest('[data-point-id]') : null;
  const focusedId = focusedHandle ? focusedHandle.dataset.pointId : null;
  const width = Math.max(260, Math.round(chart.getBoundingClientRect().width)), height = chart.clientHeight || 300;
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
  geometry = {width, height, left: 30, right: 10, top: 23, bottom: 28};
  geometry.plotWidth = width - geometry.left - geometry.right;
  geometry.plotHeight = height - geometry.top - geometry.bottom;
  const x = minute => geometry.left + minute / 1440 * geometry.plotWidth;
  const y = level => geometry.top + (100 - level) / 100 * geometry.plotHeight;
  let out = '<defs><linearGradient id="warmFill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#f3ba72" stop-opacity=".12"/><stop offset="1" stop-color="#f3ba72" stop-opacity="0"/></linearGradient><linearGradient id="coolFill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#81cdec" stop-opacity=".1"/><stop offset="1" stop-color="#81cdec" stop-opacity="0"/></linearGradient></defs>';
  for (let level = 0; level <= 100; level += 25) {
    out += '<line x1="' + x(0) + '" x2="' + x(1440) + '" y1="' + y(level) + '" y2="' + y(level) + '" stroke="#91a4af" stroke-opacity="' + (level === 0 ? '.22' : '.1') + '"/><text x="' + (geometry.left - 8) + '" y="' + (y(level) + 3.5) + '" text-anchor="end" fill="#8095a1" font-size="10">' + level + '</text>';
  }
  for (let hour = 0; hour <= 24; hour += 2) {
    out += '<line x1="' + x(hour * 60) + '" x2="' + x(hour * 60) + '" y1="' + y(0) + '" y2="' + y(100) + '" stroke="#91a4af" stroke-opacity="' + (hour % 6 === 0 ? '.14' : '.06') + '"/>';
    if (hour % 6 === 0) out += '<text x="' + x(hour * 60) + '" y="' + (height - 8) + '" text-anchor="' + (hour === 0 ? 'start' : hour === 24 ? 'end' : 'middle') + '" fill="#8095a1" font-size="10">' + String(hour).padStart(2, '0') + ':00</text>';
  }
  for (const channel of [activeCurve === 'warm' ? 'cool' : 'warm', activeCurve]) {
    const {points, editable} = graphCurve(channel), atZero = interpolate(points, 0);
    const vertices = [{minute: 0, level: atZero}, ...points.filter(point => point.minute > 0), {minute: 1440, level: atZero}];
    const path = vertices.map((point, index) => (index ? 'L' : 'M') + x(point.minute).toFixed(2) + ' ' + y(point.level).toFixed(2)).join(' ');
    const isActive = channel === activeCurve;
    out += '<path d="' + path + ' L' + x(1440) + ' ' + y(0) + ' L' + x(0) + ' ' + y(0) + ' Z" fill="url(#' + channel + 'Fill)" opacity="' + (isActive ? 1 : .35) + '"/><path d="' + path + '" fill="none" stroke="' + colors[channel] + '" stroke-opacity="' + (isActive ? 1 : .38) + '" stroke-width="' + (isActive ? 2.5 : 1.7) + '" stroke-linecap="round" stroke-linejoin="round"/>';
    if (isActive && editable) {
      for (const point of points) {
        const isSelected = selected[channel] === point.id;
        out += '<g class="point-handle" data-point-id="' + point.id + '" tabindex="0" role="button" aria-pressed="' + isSelected + '" aria-label="' + names[channel] + ', ' + hhmm(point.minute) + ', ' + point.level + ' százalék. Nyilakkal mozgatható, Delete billentyűvel törölhető."><circle class="point-hit" cx="' + x(point.minute) + '" cy="' + y(point.level) + '" r="22" fill="transparent"/><circle class="point-knob" cx="' + x(point.minute) + '" cy="' + y(point.level) + '" r="' + (isSelected ? 7 : 5) + '" fill="' + (isSelected ? colors[channel] : '#15212a') + '" stroke="' + colors[channel] + '" stroke-width="2" pointer-events="none"/></g>';
      }
    }
  }
  if (latest && latest.clockValid && online) {
    const minute = clamp(latest.minuteOfDay, 0, 1439), markerX = x(minute), labelX = Math.max(geometry.left + 22, Math.min(width - geometry.right - 22, markerX));
    out += '<g pointer-events="none"><line x1="' + markerX + '" x2="' + markerX + '" y1="' + (geometry.top - 1) + '" y2="' + y(0) + '" stroke="#d5e5ea" stroke-opacity=".5" stroke-width="1" stroke-dasharray="3 4"/><rect x="' + (labelX - 22) + '" y="1" width="44" height="17" rx="5" fill="#31444e"/><text x="' + labelX + '" y="13" text-anchor="middle" fill="#deedf1" font-size="10">' + hhmm(minute) + '</text></g>';
  }
  svg.innerHTML = out;
  // Polling and resizing replace the SVG children; keep an existing keyboard focus.
  if (focusedId) {
    const replacement = svg.querySelector('[data-point-id="' + focusedId + '"]');
    if (replacement) replacement.focus({preventScroll: true});
  }
}

// Coordinates use the rendered SVG bounds, so dragging remains correct after resize or zoom.
function plotPosition(event) {
  if (!geometry) return null;
  const bounds = $('graph').getBoundingClientRect();
  if (!bounds.width || !bounds.height) return null;
  const x = (event.clientX - bounds.left) * geometry.width / bounds.width;
  const y = (event.clientY - bounds.top) * geometry.height / bounds.height;
  return {x, y, minute: clamp((x - geometry.left) / geometry.plotWidth * 1440, 0, 1439), level: clamp((geometry.top + geometry.plotHeight - y) / geometry.plotHeight * 100), inside: x >= geometry.left && x <= geometry.width - geometry.right && y >= geometry.top && y <= geometry.height - geometry.bottom};
}
function movePoint(point, minute, level) {
  const freeMinute = nearestFreeMinute(activeCurve, minute, point.id);
  if (freeMinute === null) return;
  const nextTime = hhmm(freeMinute), nextLevel = String(clamp(level));
  if (point.time === nextTime && point.level === nextLevel) return;
  point.time = nextTime; point.level = nextLevel;
  draft[activeCurve].sort((a, b) => a.time.localeCompare(b.time));
  markDirty();
}
function graphPointerDown(event) {
  if (!latest || event.button > 0 || drag) return;
  const handle = event.target.closest('[data-point-id]');
  const position = plotPosition(event);
  if (!position) return;
  if (handle) {
    const point = draft[activeCurve].find(item => item.id === handle.dataset.pointId);
    if (!point) return;
    event.preventDefault();
    selected[activeCurve] = point.id;
    suppressDoubleUntil = Date.now() + 700;
    drag = {pointerId: event.pointerId, point, startX: event.clientX, startY: event.clientY, moved: false};
    lastTap = null; tapCandidate = null;
    $('graph').setPointerCapture(event.pointerId);
    drawGraph(); updateInspector();
  } else if (position.inside) {
    tapCandidate = {pointerId: event.pointerId, type: event.pointerType, x: event.clientX, y: event.clientY, at: Date.now(), moved: false};
    $('graph').setPointerCapture(event.pointerId);
  }
}
function graphPointerMove(event) {
  if (drag && drag.pointerId === event.pointerId) {
    event.preventDefault();
    if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 3) drag.moved = true;
    if (drag.moved) { const position = plotPosition(event); if (position) movePoint(drag.point, position.minute, position.level); }
  } else if (tapCandidate && tapCandidate.pointerId === event.pointerId && Math.hypot(event.clientX - tapCandidate.x, event.clientY - tapCandidate.y) > 10) {
    tapCandidate.moved = true;
  }
}
function releaseCapture(pointerId) {
  const graph = $('graph');
  if (graph.hasPointerCapture(pointerId)) graph.releasePointerCapture(pointerId);
}
function graphPointerUp(event) {
  if (drag && drag.pointerId === event.pointerId) {
    drag = null; tapCandidate = null; lastTap = null;
    suppressDoubleUntil = Date.now() + 700;
    releaseCapture(event.pointerId);
    renderRows(); drawGraph();
    return;
  }
  const tap = tapCandidate;
  tapCandidate = null;
  releaseCapture(event.pointerId);
  if (!tap || tap.pointerId !== event.pointerId || tap.moved || Date.now() - tap.at > 350 || !['touch', 'pen'].includes(tap.type)) return;
  const position = plotPosition(event);
  if (!position || !position.inside) return;
  const now = Date.now();
  if (lastTap && now - lastTap.at < 350 && Math.hypot(event.clientX - lastTap.x, event.clientY - lastTap.y) < 28) {
    lastTap = null;
    suppressDoubleUntil = now + 700;
    insertPoint(activeCurve, position.minute, position.level);
  } else lastTap = {at: now, x: event.clientX, y: event.clientY};
}
function graphPointerCancel(event) {
  if (drag && drag.pointerId === event.pointerId) { drag = null; renderRows(); }
  tapCandidate = null; lastTap = null;
  releaseCapture(event.pointerId);
}
function graphDoubleClick(event) {
  event.preventDefault();
  if (Date.now() < suppressDoubleUntil || event.target.closest('[data-point-id]')) return;
  const position = plotPosition(event);
  if (position && position.inside) insertPoint(activeCurve, position.minute, position.level);
}
function graphKeyDown(event) {
  const handle = event.target.closest('[data-point-id]');
  if (!handle) return;
  const point = draft[activeCurve].find(item => item.id === handle.dataset.pointId);
  if (!point) return;
  const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Delete', 'Backspace', 'Enter', ' '];
  if (!keys.includes(event.key)) return;
  event.preventDefault();
  selected[activeCurve] = point.id;
  if (event.key === 'Delete' || event.key === 'Backspace') { removePoint(point.id); return; }
  const minute = Number(point.time.slice(0, 2)) * 60 + Number(point.time.slice(3));
  const timeStep = event.shiftKey ? 15 : 1, levelStep = event.shiftKey ? 10 : 1;
  movePoint(point, minute + (event.key === 'ArrowRight' ? timeStep : event.key === 'ArrowLeft' ? -timeStep : 0), Number(point.level) + (event.key === 'ArrowUp' ? levelStep : event.key === 'ArrowDown' ? -levelStep : 0));
  renderRows(); drawGraph(); updateInspector();
  const replacement = $('graph').querySelector('[data-point-id="' + point.id + '"]');
  if (replacement) replacement.focus();
}

async function syncClock(button) {
  button.disabled = true;
  try { await mutate('/api/time', {epoch: String(Math.floor(Date.now() / 1000))}); notify('A vezérlő órája beállítva.'); await refresh(); }
  catch (error) { notify(error.message, true); }
  finally { updateControls(); }
}

document.querySelectorAll('.modebtn').forEach(button => button.addEventListener('click', () => changeMode(button.dataset.mode)));
for (const channel of ['warm', 'cool']) {
  const slider = $(channel + 'Slider');
  slider.addEventListener('pointerdown', () => { if (canControlManually()) manualEditing = true; });
  slider.addEventListener('input', queueManual);
  slider.addEventListener('change', () => { manualEditing = false; });
  slider.addEventListener('blur', () => manualEditing = false);
}
window.addEventListener('pointerup', () => manualEditing = false);
window.addEventListener('pointercancel', () => manualEditing = false);
document.querySelectorAll('.stepbtn').forEach(button => button.addEventListener('click', () => {
  const slider = $(button.dataset.channel + 'Slider');
  slider.value = clamp(Number(slider.value) + Number(button.dataset.step));
  queueManual();
}));
document.querySelectorAll('.curvebtn').forEach(button => button.addEventListener('click', () => activateCurve(button.dataset.curve)));
const graph = $('graph');
graph.addEventListener('pointerdown', graphPointerDown);
graph.addEventListener('pointermove', graphPointerMove);
graph.addEventListener('pointerup', graphPointerUp);
graph.addEventListener('pointercancel', graphPointerCancel);
graph.addEventListener('dblclick', graphDoubleClick);
graph.addEventListener('keydown', graphKeyDown);
$('removeSelected').addEventListener('click', () => { const point = selectedPoint(); if (point) removePoint(point.id); });
$('addPoint').addEventListener('click', addPoint);
$('saveProgram').addEventListener('click', saveProgram);
$('preset').addEventListener('click', () => {
  draft = {warm: toDraft(presetCurve), cool: toDraft(presetCurve)};
  selected = {warm: null, cool: null};
  markDirty(); renderRows();
  notify('A mintaprogram az előnézetbe került. Mentéssel alkalmazhatod.');
});
$('settingsShortcut').addEventListener('click', () => { $('settings').open = true; $('settings').scrollIntoView({behavior: 'smooth', block: 'start'}); });
$('syncClock').addEventListener('click', () => syncClock($('syncClock')));
$('quickClock').addEventListener('click', () => syncClock($('quickClock')));
$('wifiForm').addEventListener('submit', async event => {
  event.preventDefault();
  const ssid = $('wifiSsid').value, password = $('wifiPassword').value, bytes = new TextEncoder();
  if (!ssid || bytes.encode(ssid).length > 32) { notify('A hálózat neve 1–32 bájt hosszú lehet.', true); return; }
  if (password && (bytes.encode(password).length < 8 || bytes.encode(password).length > 63)) { notify('A Wi-Fi-jelszó 8–63 bájt legyen, vagy nyitott hálózatnál maradjon üres.', true); return; }
  $('saveWifi').disabled = true;
  try { await mutate('/api/wifi', {ssid, password}); $('wifiPassword').value = ''; notify('Hálózat elmentve. A vezérlő megpróbál csatlakozni.'); await refresh(); }
  catch (error) { notify(error.message, true); }
  finally { updateControls(); }
});
window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
if (typeof ResizeObserver !== 'undefined') new ResizeObserver(drawGraph).observe($('chart'));
else window.addEventListener('resize', drawGraph);
updateControls(); drawGraph(); updateInspector(); poll();

</script>
</body>
</html>)AQUAHTML";
