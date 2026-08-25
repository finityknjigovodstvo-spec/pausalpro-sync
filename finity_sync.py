#!/usr/bin/env python3
"""
PaušalPro — Finity Sync Server
Kompatibilan sa Finity aplikacijom (akcije: ping, save, load)
Railway hosting — čuva podatke u JSON fajlovima
"""
from flask import Flask, request, jsonify, render_template_string
import json, os
from datetime import datetime

app = Flask(__name__)

SYNC_TOKEN = os.environ.get('FINITY_TOKEN', 'pausalpro2026')
DATA_DIR   = '/tmp'  # Railway dozvoljava pisanje u /tmp
DATA_FAJL  = os.path.join(DATA_DIR, 'finity_data.json')
LOG_FAJL   = os.path.join(DATA_DIR, 'sync_log.json')

# ─── CORS ───
@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type,X-Finity-Token'
    r.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return r

@app.route('/finity-sync', methods=['OPTIONS'])
def opt(): return '', 200

# ─── HELPER ───
def log_akciju(akcija, ip):
    try:
        log = json.loads(open(LOG_FAJL).read()) if os.path.exists(LOG_FAJL) else []
    except: log = []
    log.insert(0, {'akcija': akcija, 'ip': ip, 'vreme': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    log = log[:50]
    open(LOG_FAJL, 'w').write(json.dumps(log, ensure_ascii=False))

# ─── SYNC ENDPOINT — Finity ga zove ───
@app.route('/finity-sync', methods=['POST', 'GET'])
def finity_sync():
    # GET — brzi health check
    if request.method == 'GET':
        return jsonify({'ok': True, 'poruka': 'PaušalPro Sync Server radi!'})

    try:
        data = request.get_json(force=True, silent=True) or {}
    except:
        return jsonify({'ok': False, 'poruka': 'Neispravan JSON.'}), 400

    # Provjera tokena
    token = data.get('token') or request.headers.get('X-Finity-Token', '')
    if token != SYNC_TOKEN:
        return jsonify({'ok': False, 'poruka': 'Pogresna lozinka. Proverite token u Finity podesavanjima.'}), 403

    akcija = data.get('akcija', '')
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()
    log_akciju(akcija, ip)

    # ── PING ──
    if akcija == 'ping':
        return jsonify({'ok': True, 'poruka': 'Veza radi. PaushalPro Sync aktivan.'})

    # ── SAVE ──
    if akcija == 'save':
        finity_data = data.get('data')
        if not finity_data:
            return jsonify({'ok': False, 'poruka': 'Nema podataka za cuvanje.'}), 400

        data_str = json.dumps(finity_data, ensure_ascii=False)
        velicina = len(data_str.encode('utf-8'))

        firma_naziv = 'Nepoznato'
        try:
            s = finity_data.get('settings', {})
            firma_naziv = s.get('naziv') or s.get('nazivPun') or 'Nepoznato'
        except: pass

        # Sačuvaj podatke
        open(DATA_FAJL, 'w', encoding='utf-8').write(data_str)

        # Sačuvaj meta
        meta = {'sacuvano': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'velicina': velicina, 'firma': firma_naziv, 'ip': ip}
        open(DATA_FAJL + '.meta', 'w').write(json.dumps(meta, ensure_ascii=False))

        print(f"[SAVE] {firma_naziv} — {round(velicina/1024,1)} KB od {ip}")
        return jsonify({'ok': True, 'poruka': 'Sacuvano.', 'velicina': velicina,
                       'vreme': meta['sacuvano']})

    # ── LOAD ──
    if akcija == 'load':
        if not os.path.exists(DATA_FAJL):
            return jsonify({'ok': False, 'poruka': 'Nema sacuvanih podataka na serveru.'}), 404
        try:
            finity_data = json.loads(open(DATA_FAJL, encoding='utf-8').read())
            return jsonify({'ok': True, 'data': finity_data, 'fajl': 'finity_data.json'})
        except Exception as e:
            return jsonify({'ok': False, 'poruka': f'Greska pri citanju: {str(e)}'}), 500

    return jsonify({'ok': False, 'poruka': f'Nepoznata akcija: {akcija}'}), 400

# ─── POREZI/DOPRINOSI ENDPOINT — ručni unos računovođe, po firmi ───
POREZI_TOKEN = os.environ.get('POREZI_TOKEN', 'racunovodja2026')

@app.route('/porezi', methods=['OPTIONS'])
def porezi_opt(): return '', 200

@app.route('/porezi', methods=['POST'])
def porezi():
    data = request.get_json(force=True, silent=True) or {}
    akcija = data.get('akcija', '')
    firma_id = data.get('firma_id', '')
    if not firma_id:
        return jsonify({'ok': False, 'poruka': 'Nedostaje firma_id.'}), 400

    porezi_fajl = os.path.join(DATA_DIR, f'porezi_{firma_id}.json')

    if akcija == 'load':
        if not os.path.exists(porezi_fajl):
            return jsonify({'ok': True, 'data': None})
        return jsonify({'ok': True, 'data': json.loads(open(porezi_fajl, encoding='utf-8').read())})

    if akcija == 'save':
        token = data.get('token', '')
        if token != POREZI_TOKEN:
            return jsonify({'ok': False, 'poruka': 'Pogresna admin lozinka.'}), 403
        payload = data.get('data', {})
        payload['azurirano'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        open(porezi_fajl, 'w', encoding='utf-8').write(json.dumps(payload, ensure_ascii=False))
        return jsonify({'ok': True, 'poruka': 'Sacuvano.'})

    return jsonify({'ok': False, 'poruka': f'Nepoznata akcija: {akcija}'}), 400

# ─── API ZA DASHBOARD ───
@app.route('/api/podaci')
def api_podaci():
    if not os.path.exists(DATA_FAJL):
        return jsonify({'ima_podataka': False})

    try:
        finity = json.loads(open(DATA_FAJL, encoding='utf-8').read())
        meta   = json.loads(open(DATA_FAJL+'.meta').read()) if os.path.exists(DATA_FAJL+'.meta') else {}
        log    = json.loads(open(LOG_FAJL).read()) if os.path.exists(LOG_FAJL) else []
    except Exception as e:
        return jsonify({'ima_podataka': False, 'greska': str(e)})

    settings  = finity.get('settings', {})  if isinstance(finity, dict) else {}
    fakture   = list(finity.get('fakture',   {}).values()) if isinstance(finity.get('fakture'), dict)   else finity.get('fakture',   [])
    kpo       = list(finity.get('kpo',       {}).values()) if isinstance(finity.get('kpo'), dict)       else finity.get('kpo',       [])
    komitenti = list(finity.get('komitenti', {}).values()) if isinstance(finity.get('komitenti'), dict) else finity.get('komitenti', [])

    # KPO prihodi
    prihod_kpo = 0
    for k in kpo:
        try:
            tip = str(k.get('tip','') or '').lower()
            if tip in ['p','prihod','u','uplata','']:
                prihod_kpo += float(k.get('iznos',0) or 0)
        except: pass

    # Fakture
    fakt_placene = 0
    fakt_iznos   = 0.0
    poslednje    = []
    for f in reversed(fakture):
        try:
            status = str(f.get('status','') or '').lower()
            if 'placen' in status or 'paid' in status: fakt_placene += 1
            iznos = float(f.get('ukupno', f.get('iznos', f.get('total', 0))) or 0)
            fakt_iznos += iznos
            if len(poslednje) < 8:
                poslednje.append({
                    'broj':    f.get('broj', f.get('br', '—')),
                    'klijent': f.get('klijent', f.get('kupac', f.get('naziv', '—'))),
                    'iznos':   iznos,
                    'datum':   f.get('datum', f.get('datumIzdavanja', '—')),
                    'status':  f.get('status', '—'),
                })
        except: pass

    return jsonify({
        'ima_podataka': True,
        'firma': {
            'naziv':  settings.get('naziv') or settings.get('nazivPun', 'Nepoznato'),
            'pib':    settings.get('pib', ''),
            'forma':  settings.get('forma', ''),
            'adresa': settings.get('adresa', ''),
            'email':  settings.get('email', ''),
        },
        'sacuvano':    meta.get('sacuvano', '—'),
        'velicina_kb': round(os.path.getsize(DATA_FAJL) / 1024, 1),
        'statistike': {
            'fakture_ukupno':    len(fakture),
            'fakture_placene':   fakt_placene,
            'fakture_neplacene': len(fakture) - fakt_placene,
            'fakture_iznos':     round(fakt_iznos, 2),
            'kpo_zapisa':        len(kpo),
            'ukupni_prihod_kpo': round(prihod_kpo, 2),
            'komitenti':         len(komitenti),
        },
        'poslednje_fakture': poslednje,
        'sync_log': log[:15],
    })

# ─── LIVE DASHBOARD ───
DASHBOARD = '''<!DOCTYPE html>
<html lang="sr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>PaušalPro Live</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--navy:#1B2B4B;--gold:#C9A84C;--slate:#F3F4F8;--brd:#E4E6EE;--green:#27AE60;--red:#E74C3C;--orange:#F39C12}
body{font-family:'Inter',sans-serif;background:var(--slate);color:var(--navy);min-height:100vh}
.hdr{background:var(--navy);padding:14px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:50}
.logo{display:flex;align-items:center;gap:9px}
.lic{width:34px;height:34px;background:var(--gold);border-radius:9px;display:flex;align-items:center;justify-content:center;font-weight:800;color:var(--navy);font-size:15px}
.lnm{font-size:16px;font-weight:800;color:#fff}.lnm span{color:var(--gold)}
.live{display:flex;align-items:center;gap:6px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);border-radius:20px;padding:5px 12px;font-size:11px;color:rgba(255,255,255,.8);font-weight:600}
.ldot{width:7px;height:7px;border-radius:50%;background:#4DD996;animation:bl 1.5s ease infinite}
@keyframes bl{0%,100%{opacity:1}50%{opacity:.3}}
.sbar{background:#fff;border-bottom:1px solid var(--brd);padding:8px 20px;display:flex;align-items:center;gap:10px;font-size:12.5px;flex-wrap:wrap}
.sok{color:var(--green);font-weight:700}.serr{color:var(--red);font-weight:700}.swait{color:var(--orange);font-weight:600}
.stime{color:#96A3B5}
.rbtn{margin-left:auto;padding:6px 14px;background:var(--navy);color:#fff;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer}
.wrap{padding:16px}
.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}
@media(max-width:700px){.g4{grid-template-columns:1fr 1fr}.g2{grid-template-columns:1fr}}
.card{background:#fff;border-radius:14px;padding:16px;box-shadow:0 1px 8px rgba(27,43,75,.08)}
.card.nv{background:var(--navy)}
.sic{width:38px;height:38px;border-radius:10px;background:var(--slate);display:flex;align-items:center;justify-content:center;font-size:18px;margin-bottom:10px}
.card.nv .sic{background:rgba(255,255,255,.1)}
.sv{font-size:22px;font-weight:800;color:var(--navy);letter-spacing:-.3px;line-height:1.1}
.card.nv .sv{color:#fff}
.sl{font-size:11px;color:#96A3B5;margin-top:4px}
.card.nv .sl{color:rgba(255,255,255,.45)}
.sbg{float:right;background:#E8F8F1;color:var(--green);font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;margin-top:-32px}
.sbr{float:right;background:#FDEDEC;color:var(--red);font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;margin-top:-32px}
.sbo{float:right;background:#FEF9E7;color:var(--orange);font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;margin-top:-32px}
.pt{font-size:13.5px;font-weight:700;color:var(--navy);margin-bottom:12px}
.ir{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--slate)}
.ir:last-child{border:none}
.il{font-size:12px;color:#5A6880}.iv{font-size:12.5px;font-weight:700;color:var(--navy);text-align:right;max-width:55%}
.iv.g{color:var(--green)}.iv.r{color:var(--red)}
table{width:100%;border-collapse:collapse;font-size:12px}
th{padding:8px 10px;text-align:left;font-size:10px;font-weight:700;color:#96A3B5;text-transform:uppercase;background:var(--slate)}
td{padding:9px 10px;border-bottom:1px solid var(--slate);color:var(--navy)}
tr:last-child td{border:none}
tr:hover td{background:#fafbfe}
.ch{display:inline-flex;font-size:10px;font-weight:600;padding:2px 7px;border-radius:20px}
.chg{background:#E8F8F1;color:var(--green)}.chr{background:#FDEDEC;color:var(--red)}.cho{background:#FEF9E7;color:var(--orange)}
.lg{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--slate);font-size:11.5px}
.lg:last-child{border:none}
.lgic{width:24px;height:24px;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:11px;flex-shrink:0}
.lgs{background:#E8F8F1}.lgp{background:#EEF2FF}.lgl{background:#FEF9E7}
.nd{text-align:center;padding:50px 20px}
.ni{font-size:50px;margin-bottom:12px}
.cbox{background:var(--navy);border-radius:12px;padding:14px 18px;text-align:left;max-width:420px;margin:0 auto}
.cbox code{font-size:12px;color:#4DD996;font-family:monospace;line-height:2.2;display:block;word-break:break-all}
.cbox .cm{color:rgba(255,255,255,.35)}
</style>
</head>
<body>
<div class="hdr">
  <div class="logo"><div class="lic">П</div><div class="lnm">Paušal<span>Pro</span> Live</div></div>
  <div class="live"><div class="ldot"></div>Osvežava svakih 8s</div>
</div>
<div class="sbar">
  <span id="ss">Učitavam...</span>
  <span id="st" class="stime"></span>
  <button class="rbtn" onclick="load()">🔄</button>
</div>
<div class="wrap" id="wrap">
  <div style="text-align:center;padding:60px;color:#96A3B5">Učitavam...</div>
</div>
<script>
const BASE = window.location.origin;
const fmt = n => Math.round(n||0).toLocaleString('sr-RS');
function chip(s){
  if(!s) return '<span class="ch cho">—</span>';
  const l=s.toLowerCase();
  if(l.includes('placen')||l.includes('paid')) return '<span class="ch chg">✅ Plaćena</span>';
  if(l.includes('kasni')) return '<span class="ch chr">⏰ Kasni</span>';
  return '<span class="ch cho">📤 Poslata</span>';
}
async function load(){
  try{
    const d=await fetch(BASE+'/api/podaci').then(r=>r.json());
    render(d);
    document.getElementById('st').textContent=new Date().toLocaleTimeString('sr-RS');
  }catch(e){
    document.getElementById('ss').innerHTML='<span class="serr">❌ Greška</span>';
  }
}
function render(d){
  const w=document.getElementById('wrap');
  if(!d.ima_podataka){
    document.getElementById('ss').innerHTML='<span class="swait">⏳ Čekam podatke iz Finity...</span>';
    w.innerHTML=`<div class="card nd">
      <div class="ni">🔌</div>
      <h2 style="font-size:18px;font-weight:800;margin-bottom:8px">Finity još nije poslao podatke</h2>
      <p style="font-size:13px;color:#5A6880;line-height:1.7;margin-bottom:16px">
        U Finity → Podešavanja → Cloud sinhronizacija unesite:
      </p>
      <div class="cbox">
        <code><span class="cm">Adresa servera:</span>
${BASE}/finity-sync

<span class="cm">Lozinka:</span>
pausalpro2026</code>
      </div>
    </div>`;
    return;
  }
  document.getElementById('ss').innerHTML='<span class="sok">✅ Sinhrono sa Finity</span>';
  const f=d.firma,s=d.statistike,log=d.sync_log||[],fak=d.poslednje_fakture||[];
  w.innerHTML=`
  <div class="g4">
    <div class="card nv">
      <div class="sic">🏢</div>
      <div class="sv" style="font-size:${(f.naziv||'').length>16?'14px':'20px'}">${f.naziv||'—'}</div>
      <div class="sl">${f.forma||''}${f.pib?' · '+f.pib:''}</div>
    </div>
    <div class="card">
      <div class="sbg">📄 ${s.fakture_ukupno}</div>
      <div class="sic">💰</div>
      <div class="sv">${fmt(s.fakture_iznos)}</div>
      <div class="sl">Fakturisano RSD</div>
    </div>
    <div class="card">
      <div class="sbg">✅ ${s.fakture_placene}</div>
      <div class="sic">📖</div>
      <div class="sv">${s.kpo_zapisa}</div>
      <div class="sl">KPO zapisa</div>
    </div>
    <div class="card">
      <div class="sbo">👥 ${s.komitenti}</div>
      <div class="sic">💼</div>
      <div class="sv">${fmt(s.ukupni_prihod_kpo)}</div>
      <div class="sl">Prihod KPO (RSD)</div>
    </div>
  </div>
  <div class="g2">
    <div class="card">
      <div class="pt">🏢 Firma</div>
      <div class="ir"><span class="il">Naziv</span><span class="iv">${f.naziv||'—'}</span></div>
      <div class="ir"><span class="il">PIB</span><span class="iv">${f.pib||'—'}</span></div>
      <div class="ir"><span class="il">Forma</span><span class="iv">${f.forma||'—'}</span></div>
      <div class="ir"><span class="il">Email</span><span class="iv" style="font-size:11px">${f.email||'—'}</span></div>
      <div class="ir"><span class="il">Poslednji sync</span><span class="iv" style="font-size:10.5px">${d.sacuvano}</span></div>
      <div class="ir"><span class="il">Veličina</span><span class="iv">${d.velicina_kb} KB</span></div>
    </div>
    <div class="card">
      <div class="pt">📊 Fakture</div>
      <div class="ir"><span class="il">Ukupno</span><span class="iv">${s.fakture_ukupno}</span></div>
      <div class="ir"><span class="il">Plaćene</span><span class="iv g">${s.fakture_placene}</span></div>
      <div class="ir"><span class="il">Neplaćene</span><span class="iv r">${s.fakture_neplacene}</span></div>
      <div class="ir"><span class="il">Ukupan iznos</span><span class="iv">${fmt(s.fakture_iznos)} RSD</span></div>
      <div class="ir"><span class="il">Komitenti</span><span class="iv">${s.komitenti}</span></div>
      <div class="ir"><span class="il">Procenat naplaćeno</span><span class="iv g">${s.fakture_ukupno?Math.round(s.fakture_placene/s.fakture_ukupno*100)+'%':'0%'}</span></div>
    </div>
  </div>
  <div class="g2">
    <div class="card">
      <div class="pt">📄 Poslednje fakture</div>
      ${fak.length===0?'<div style="color:#96A3B5;text-align:center;padding:12px">Nema faktura.</div>':`
      <table>
        <tr><th>Broj</th><th>Klijent</th><th>Iznos RSD</th><th>Status</th></tr>
        ${fak.map(f=>`<tr>
          <td style="font-weight:600">${f.broj}</td>
          <td>${f.klijent}</td>
          <td style="font-weight:700">${fmt(f.iznos)}</td>
          <td>${chip(f.status)}</td>
        </tr>`).join('')}
      </table>`}
    </div>
    <div class="card">
      <div class="pt">⚡ Sync log</div>
      ${log.length===0?'<div style="color:#96A3B5;text-align:center;padding:12px">Nema aktivnosti.</div>':
      log.map(l=>`<div class="lg">
        <div class="lgic ${l.akcija==='save'?'lgs':l.akcija==='ping'?'lgp':'lgl'}">${l.akcija==='save'?'💾':l.akcija==='ping'?'📡':'📥'}</div>
        <div style="flex:1"><strong>${l.akcija.toUpperCase()}</strong> <span style="color:#96A3B5;font-size:10.5px">${l.ip}</span></div>
        <div style="color:#96A3B5;font-size:10.5px;white-space:nowrap">${l.vreme.split(' ')[1]||l.vreme}</div>
      </div>`).join('')}
    </div>
  </div>`;
}
load();
setInterval(load,8000);
</script>
</body>
</html>'''

@app.route('/')
@app.route('/dashboard')
def dashboard():
    return render_template_string(DASHBOARD)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    print(f"\n  PaušalPro Sync — http://localhost:{port}")
    print(f"  Sync URL: http://localhost:{port}/finity-sync")
    print(f"  Token: {SYNC_TOKEN}\n")
    app.run(host='0.0.0.0', port=port, debug=False)
