#!/usr/bin/env python3
"""
PaušalPro — Finity Sync Server
Kompatibilan sa Finity aplikacijom (akcije: ping, save, load)
Railway hosting — čuva podatke u JSON fajlovima
"""
from flask import Flask, request, jsonify, render_template_string
import json, os, requests
from datetime import datetime, timedelta

app = Flask(__name__)

SYNC_TOKEN = os.environ.get('FINITY_TOKEN', 'pausalpro2026')

# ─── SKLADIŠTE PODATAKA ───
# Ako je povezan Railway Volume, RAILWAY_VOLUME_MOUNT_PATH je automatski dostupan
# i svi podaci se čuvaju TRAJNO (preživljava redeploy). Bez Volume-a, pada nazad
# na /tmp koji Railway briše pri svakom redeploy-u (privremeno rešenje).
DATA_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/tmp')
os.makedirs(DATA_DIR, exist_ok=True)

# ─── EMAIL PODEŠAVANJA (Resend — HTTPS API, ne SMTP jer Railway blokira SMTP portove) ───
GMAIL_USER = os.environ.get('GMAIL_USER', 'finity.knjigovodstvo@gmail.com')
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')

# ─── PUSH NOTIFIKACIJE (Web Push / VAPID) ───
VAPID_PUBLIC_KEY  = os.environ.get('VAPID_PUBLIC_KEY',  'BGhZWYu2LsRdwfTk5qpnrWqJWWCNY5rPHlbKQtI0Dp8EnyGjMF-YC5asmX2J-I2xD1ERyKcf2ValR4NujlGWALU')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', 'yaUvKIKPNy3t8o2wPBk15aOUi1cssLWmEcDz_1EDOwE')
VAPID_CLAIMS = {'sub': f'mailto:{GMAIL_USER}'}
PUSH_SUBS_FAJL = os.path.join(DATA_DIR, 'push_subs.json')

def ucitaj_push_subs():
    if not os.path.exists(PUSH_SUBS_FAJL): return {}
    try: return json.loads(open(PUSH_SUBS_FAJL, encoding='utf-8').read())
    except: return {}

def sacuvaj_push_subs(subs):
    open(PUSH_SUBS_FAJL, 'w', encoding='utf-8').write(json.dumps(subs, ensure_ascii=False))

def posalji_push_firmi(firma_id, naslov, telo, url='/portal', tag='finity-notif'):
    """Šalje push notifikaciju svim uređajima pretplaćenim za datu firmu. Briše nevažeće pretplate."""
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print('pywebpush nije instaliran — push notifikacije preskočene')
        return

    subs = ucitaj_push_subs()
    lista = subs.get(firma_id, [])
    if not lista: return

    jos_vazece = []
    for sub in lista:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps({'naslov': naslov, 'telo': telo, 'url': url, 'tag': tag}, ensure_ascii=False),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=dict(VAPID_CLAIMS),
            )
            jos_vazece.append(sub)
        except WebPushException as e:
            # 410 Gone / 404 = pretplata više ne postoji na uređaju, izbaci je
            if e.response is not None and e.response.status_code in (404, 410):
                print(f'Push pretplata istekla za {firma_id}, uklanjam.')
            else:
                print('Push greška:', e)
                jos_vazece.append(sub)  # zadrži za sledeći pokušaj ako je privremena greška
        except Exception as e:
            print('Push nepoznata greška:', e)
            jos_vazece.append(sub)

    subs[firma_id] = jos_vazece
    sacuvaj_push_subs(subs)
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

        # ── PROVERA NOVIH FAKTURA (pre upisa, dok još imamo pristup starim podacima) ──
        try:
            obradi_nove_fakture_i_posalji_push(finity_data)
        except Exception as e:
            print('Greska pri detekciji novih faktura:', e)

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

def _parsiraj(v):
    """Parsira vrednost koja može biti JSON string ili već parsiran objekat."""
    if isinstance(v, str):
        try: return json.loads(v)
        except: return None
    return v

def izvuci_fakture(fd):
    v = _parsiraj((fd or {}).get('fakture', []))
    return v if isinstance(v, list) else []

def izvuci_firme_mapu(keys):
    v = _parsiraj((keys or {}).get('knjigo_firme', []))
    if not isinstance(v, list): return {}
    return {f.get('id'): f.get('naziv','Nepoznato') for f in v if isinstance(f, dict)}

def _normalizuj_naziv(s):
    s = (s or '').strip().lower()
    for a, b in [('š','s'),('đ','dj'),('č','c'),('ć','c'),('ž','z')]:
        s = s.replace(a, b)
    return s

def _rok_za_klijenta(naziv, klijenti):
    nn = _normalizuj_naziv(naziv)
    if not nn: return 60
    for k in klijenti:
        if isinstance(k, dict) and _normalizuj_naziv(k.get('naziv')) == nn:
            try:
                r = int(k.get('rok'))
                if r >= 0: return r
            except: pass
    return 60  # podrazumevano, isto kao Finity i portal

def _izracunaj_stornirane_brojeve(fd):
    kpo = fd.get('kpo') or []
    fakture = fd.get('fakture') or []
    stornirano = set()
    for k in kpo:
        if not isinstance(k, dict): continue
        if (k.get('vrsta') or '') == 'Storno fakture':
            od = str(k.get('stornoOd') or '').strip()
            if od: stornirano.add(od)
        if k.get('stornirano') or k.get('stornirana'):
            if k.get('br'): stornirano.add(str(k.get('br')).strip())
    for f in fakture:
        if not isinstance(f, dict): continue
        if (f.get('status') or '').lower() == 'stornirana':
            stornirano.add(str(f.get('br') or '').strip())
        od = str(f.get('stornoOd') or '').strip()
        if od: stornirano.add(od)
    return stornirano

def _neizmirene_fakture_sa_rokom(fd, danas):
    """Server-side ekvivalent portalove izracunajOcekivaneProlive() — vraća listu neizmirenih
    faktura/KPO stavki sa izračunatim rokom dospeća (datum izdavanja + rok klijenta)."""
    kpo = fd.get('kpo') or []
    fakture = fd.get('fakture') or []
    klijenti = fd.get('klijenti') or []
    naplate = fd.get('naplate') or []

    stornirano = _izracunaj_stornirane_brojeve(fd)
    nema_prati = {str(k.get('br')).strip() for k in kpo if isinstance(k, dict) and k.get('nePrati') and k.get('br')}

    naplaceno = {}
    for n in naplate:
        if not isinstance(n, dict): continue
        br = n.get('faktBr') or ''
        if not br: continue
        try: naplaceno[br] = naplaceno.get(br, 0) + float(n.get('iznos') or 0)
        except: pass

    promet = {}
    for k in kpo:
        if not isinstance(k, dict): continue
        if (k.get('vrsta') or '') == 'Storno fakture' or float(k.get('iz') or k.get('uk') or 0) < 0: continue
        br = k.get('br') or ''
        if not br or br in stornirano: continue
        promet[br] = {'br': br, 'klijent': k.get('kl') or '—', 'iznos': float(k.get('iz') or k.get('uk') or 0), 'datum': k.get('dat') or ''}
    for f in fakture:
        if not isinstance(f, dict): continue
        if (f.get('status') or '').lower() == 'storno': continue
        br = f.get('br') or ''
        if not br or br in stornirano or br in promet: continue
        promet[br] = {'br': br, 'klijent': f.get('klijentNaziv') or '—', 'iznos': float(f.get('ukupno') or 0), 'datum': f.get('datum') or ''}

    rezultat = []
    for br, p in promet.items():
        if br in nema_prati: continue  # "ne prati se" — isključeno, isto kao portal
        placeno = naplaceno.get(br, 0)
        ostatak = round(p['iznos'] - placeno, 2)
        if ostatak <= 0.5: continue  # izmireno

        rok_dana = _rok_za_klijenta(p['klijent'], klijenti)
        rok_datum = None
        if p['datum']:
            try:
                rok_datum = (datetime.strptime(p['datum'], '%Y-%m-%d') + timedelta(days=rok_dana)).date()
            except: pass
        if not rok_datum: continue

        dana = (rok_datum - danas).days
        rezultat.append({'br': br, 'klijent': p['klijent'], 'ostatak': ostatak, 'rok_datum': rok_datum, 'dana': dana})
    return rezultat

def obradi_nove_fakture_i_posalji_push(nova_data):
    """Uporedi novo-poslate podatke sa prethodno sačuvanim i pošalji push za svaku novu fakturu."""
    stare_keys = {}
    if os.path.exists(DATA_FAJL):
        stari = _parsiraj(open(DATA_FAJL, encoding='utf-8').read())
        if isinstance(stari, dict):
            stare_keys = stari.get('keys', stari)

    nove_keys = nova_data.get('keys', nova_data) if isinstance(nova_data, dict) else {}
    if not isinstance(nove_keys, dict): return

    firme_mapa = izvuci_firme_mapu(nove_keys)

    for k, v in nove_keys.items():
        if not isinstance(k, str) or not k.startswith('knjigo_rs_v41_'): continue
        fid = k.replace('knjigo_rs_v41_', '')
        nova_fd = _parsiraj(v)
        if not isinstance(nova_fd, dict): continue
        nove_fakture = izvuci_fakture(nova_fd)

        stara_fd = _parsiraj(stare_keys.get(k)) if stare_keys.get(k) is not None else {}
        stare_fakture = izvuci_fakture(stara_fd if isinstance(stara_fd, dict) else {})

        if len(nove_fakture) <= len(stare_fakture): continue

        stari_brojevi = {f.get('br') for f in stare_fakture if isinstance(f, dict)}
        nove_stavke = [f for f in nove_fakture if isinstance(f, dict) and f.get('br') not in stari_brojevi]

        for nf in nove_stavke[:3]:  # ograniči da ne zaspemo notifikacijama pri masovnom uvozu
            try:
                iznos = int(float(nf.get('ukupno', 0) or 0))
            except: iznos = 0
            posalji_push_firmi(
                fid,
                '📄 Nova faktura izdata',
                f"{nf.get('br','')} — {nf.get('klijentNaziv','')} — {iznos:,} RSD".replace(',', '.'),
                url='/portal', tag='nova-faktura'
            )

# ─── PRETPLATA NA PUSH NOTIFIKACIJE (poziva portal.html jednom po uređaju) ───
@app.route('/push-subscribe', methods=['OPTIONS'])
def push_subscribe_opt(): return '', 200

@app.route('/push-subscribe', methods=['POST'])
def push_subscribe():
    data = request.get_json(force=True, silent=True) or {}
    firma_id = data.get('firma_id', '')
    subscription = data.get('subscription')
    if not firma_id or not subscription:
        return jsonify({'ok': False, 'poruka': 'Nedostaju podaci.'}), 400

    subs = ucitaj_push_subs()
    lista = subs.get(firma_id, [])
    endpoint = subscription.get('endpoint')
    lista = [s for s in lista if s.get('endpoint') != endpoint]  # izbegni duplikate
    lista.append(subscription)
    subs[firma_id] = lista
    sacuvaj_push_subs(subs)
    return jsonify({'ok': True, 'poruka': 'Pretplata sacuvana.'})

# ─── DNEVNA PROVERA ROKOVA (poziva se preko besplatnog eksternog cron servisa, npr. cron-job.org) ───
@app.route('/proveri-podsetnike', methods=['GET', 'POST'])
def proveri_podsetnike():
    if not os.path.exists(DATA_FAJL):
        return jsonify({'ok': True, 'poruka': 'Nema podataka.', 'poslato': 0})

    sve = _parsiraj(open(DATA_FAJL, encoding='utf-8').read())
    keys = sve.get('keys', sve) if isinstance(sve, dict) else {}
    if not isinstance(keys, dict):
        return jsonify({'ok': False, 'poruka': 'Neispravan format podataka.'}), 500

    danas = datetime.now().date()
    poslato = 0
    PRAG_DANA = (3, 1, 0)  # podseti 3 dana pre, 1 dan pre, i na sam dan

    for k, v in keys.items():
        if not isinstance(k, str) or not k.startswith('knjigo_rs_v41_'): continue
        fid = k.replace('knjigo_rs_v41_', '')
        fd = _parsiraj(v)
        if not isinstance(fd, dict): continue

        # Poreske obaveze
        porezi = fd.get('poreskeObaveze') or {}
        if isinstance(porezi, dict):
            for kat, info in porezi.items():
                if not isinstance(info, dict): continue
                rok = info.get('rok')
                if not rok: continue
                try: rok_datum = datetime.strptime(rok, '%Y-%m-%d').date()
                except: continue
                dana = (rok_datum - danas).days
                if dana in PRAG_DANA:
                    naslov = '🚨 Rok je danas' if dana == 0 else f'⏰ Rok za {dana} dana'
                    posalji_push_firmi(fid, naslov, f'{kat.upper()} — plaćanje do {rok}', tag=f'porez-{kat}-{rok}')
                    poslato += 1

        # Ručne kalendar stavke
        for stavka in (fd.get('kalendarStavke') or []):
            if not isinstance(stavka, dict): continue
            datum = stavka.get('datum')
            if not datum: continue
            try: d = datetime.strptime(datum, '%Y-%m-%d').date()
            except: continue
            dana = (d - danas).days
            if dana in PRAG_DANA:
                naslov = '🔔 Podsetnik za danas' if dana == 0 else f'📅 Podsetnik za {dana} dana'
                posalji_push_firmi(fid, naslov, stavka.get('naziv', 'Obaveza'), tag=f"kal-{stavka.get('id','x')}")
                poslato += 1

        # Dospeća neizmirenih faktura (rok = datum izdavanja + rok klijenta, isto kao portal)
        for x in _neizmirene_fakture_sa_rokom(fd, danas):
            if x['dana'] in PRAG_DANA:
                naslov = '🚨 Faktura dospeva danas' if x['dana'] == 0 else f"⏰ Faktura dospeva za {x['dana']} dana"
                iznos_txt = f"{int(x['ostatak']):,} RSD".replace(',', '.')
                posalji_push_firmi(fid, naslov, f"{x['br']} — {x['klijent']} — {iznos_txt}",
                    tag=f"fakt-{x['br']}-{x['rok_datum']}")
                poslato += 1

    return jsonify({'ok': True, 'poslato': poslato})

@app.route('/portal')
def portal():
    """Klijentski portal — link koji dajete klijentima."""
    try:
        with open(os.path.join(os.path.dirname(__file__), 'portal.html'), encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return '<h2>Portal fajl nije pronađen. Uploadujte portal.html zajedno sa finity_sync.py na GitHub.</h2>', 404

# ─── IKONICE APLIKACIJE (PNG, ugrađene kao base64 — pouzdanije od data-URI manifesta) ───
_ICON_192_B64 = "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"
_ICON_512_B64 = "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"
_ICON_180_B64 = "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"

@app.route('/icon-192.png')
def icon_192():
    import base64
    return base64.b64decode(_ICON_192_B64), 200, {'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=86400'}

@app.route('/icon-512.png')
def icon_512():
    import base64
    return base64.b64decode(_ICON_512_B64), 200, {'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=86400'}

@app.route('/icon-180.png')
def icon_180():
    import base64
    return base64.b64decode(_ICON_180_B64), 200, {'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=86400'}

@app.route('/manifest.json')
def manifest_json():
    manifest = {
        "name": "Finity Portal",
        "short_name": "Finity",
        "start_url": "/portal",
        "scope": "/",
        "display": "standalone",
        "background_color": "#F3F4F8",
        "theme_color": "#1B2B4B",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    }
    return jsonify(manifest)

@app.route('/sw.js')
def service_worker():
    """Service Worker — mora biti servisan sa root putanje da bi imao pun opseg (scope)."""
    try:
        with open(os.path.join(os.path.dirname(__file__), 'sw.js'), encoding='utf-8') as f:
            return f.read(), 200, {'Content-Type': 'application/javascript'}
    except FileNotFoundError:
        return '// sw.js nije pronađen na serveru', 404, {'Content-Type': 'application/javascript'}

# ─── DVOSMERNI CHAT — trajno čuvanje poruka + email obaveštenje ───
CHAT_TOKEN = os.environ.get('ADMIN_TOKEN', 'racunovodja2026')
CHAT_FAJL = os.path.join(DATA_DIR, 'chat_poruke.json')

def ucitaj_chat():
    if not os.path.exists(CHAT_FAJL): return {}
    try: return json.loads(open(CHAT_FAJL, encoding='utf-8').read())
    except: return {}

def sacuvaj_chat(sve):
    open(CHAT_FAJL, 'w', encoding='utf-8').write(json.dumps(sve, ensure_ascii=False))

def posalji_email_o_poruci(firma_naziv, firma_pib, tekst):
    if not RESEND_API_KEY:
        print(f"[CHAT — EMAIL NIJE PODESEN] {firma_naziv} ({firma_pib}): {tekst}")
        return
    try:
        telo = f"Firma: {firma_naziv}<br>PIB: {firma_pib}<br>Vreme: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br><br>Poruka:<br>{tekst}"
        r = requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
            json={'from': 'Finity Portal <onboarding@resend.dev>', 'to': [GMAIL_USER],
                  'subject': f'Finity Portal — zahtev od {firma_naziv}', 'html': telo},
            timeout=10,
        )
        if r.status_code >= 300:
            print('RESEND GRESKA:', r.status_code, r.text)
        else:
            print(f"[CHAT] Email poslat preko Resend — {firma_naziv}")
    except requests.exceptions.RequestException as e:
        print('RESEND GRESKA (mreza):', e)

@app.route('/chat-poruka', methods=['OPTIONS'])
def chat_poruka_opt(): return '', 200

@app.route('/chat-poruka', methods=['POST'])
def chat_poruka():
    """Klijent šalje poruku (iz portala). Čuva se trajno + šalje email obaveštenje."""
    data = request.get_json(force=True, silent=True) or {}
    firma_id = data.get('firma_id', '')
    firma_naziv = data.get('firma_naziv', 'Nepoznata firma')
    firma_pib = data.get('firma_pib', '')
    poruka = data.get('poruka', '')

    if not poruka or not firma_id:
        return jsonify({'ok': False, 'poruka': 'Nedostaju podaci.'}), 400

    sve = ucitaj_chat()
    nit = sve.get(firma_id, {'firma_naziv': firma_naziv, 'poruke': []})
    nit['firma_naziv'] = firma_naziv
    nit['poruke'].append({'od': 'klijent', 'tekst': poruka, 'vreme': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    sve[firma_id] = nit
    sacuvaj_chat(sve)

    posalji_email_o_poruci(firma_naziv, firma_pib, poruka)
    return jsonify({'ok': True, 'poruka': 'Poslato!'})

@app.route('/chat-poruke', methods=['GET'])
def chat_poruke_get():
    """Portal periodično proverava ima li novih poruka (uključujući odgovore računovođe)."""
    firma_id = request.args.get('firma_id', '')
    if not firma_id:
        return jsonify({'ok': False, 'poruka': 'Nedostaje firma_id.'}), 400
    sve = ucitaj_chat()
    nit = sve.get(firma_id, {'poruke': []})
    return jsonify({'ok': True, 'poruke': nit.get('poruke', [])})

@app.route('/chat-odgovori', methods=['OPTIONS'])
def chat_odgovori_opt(): return '', 200

@app.route('/chat-odgovori', methods=['POST'])
def chat_odgovori():
    """Računovođa odgovara kroz admin panel — šalje push notifikaciju klijentu."""
    data = request.get_json(force=True, silent=True) or {}
    if data.get('token') != CHAT_TOKEN:
        return jsonify({'ok': False, 'poruka': 'Pogresna admin lozinka.'}), 403

    firma_id = data.get('firma_id', '')
    tekst = data.get('tekst', '')
    if not firma_id or not tekst:
        return jsonify({'ok': False, 'poruka': 'Nedostaju podaci.'}), 400

    sve = ucitaj_chat()
    nit = sve.get(firma_id, {'firma_naziv': 'Firma', 'poruke': []})
    nit['poruke'].append({'od': 'racunovodja', 'tekst': tekst, 'vreme': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    sve[firma_id] = nit
    sacuvaj_chat(sve)

    posalji_push_firmi(firma_id, '💬 Novi odgovor od računovođe', tekst, url='/portal', tag='chat-odgovor')
    return jsonify({'ok': True, 'poruka': 'Odgovor poslat.'})

@app.route('/admin')
def admin_chat():
    """Prosta admin stranica — pregled svih razgovora i odgovaranje. Zaštićena lozinkom u URL-u."""
    token = request.args.get('token', '')
    if token != CHAT_TOKEN:
        return '<h2 style="font-family:sans-serif">Pogrešan token. Dodajte ?token=VASA_LOZINKA u adresu.</h2>', 403

    sve = ucitaj_chat()
    firme_html = ''
    for fid, nit in sorted(sve.items(), key=lambda x: (x[1].get('poruke') or [{}])[-1].get('vreme',''), reverse=True):
        poruke = nit.get('poruke', [])
        poslednja = poruke[-1]['tekst'] if poruke else ''
        firme_html += f'''<div class="firma" onclick="izaberi('{fid}','{nit.get('firma_naziv','Firma')}')">
            <div class="fnaziv">{nit.get('firma_naziv','Firma')}</div>
            <div class="fposlednja">{poslednja[:50]}</div>
        </div>'''

    return f'''<!DOCTYPE html><html lang="sr"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Finity Admin — Chat</title>
    <style>
    *{{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,sans-serif}}
    body{{display:flex;height:100vh;background:#F3F4F8}}
    .sidebar{{width:280px;background:#fff;border-right:1px solid #E4E6EE;overflow-y:auto}}
    .sidebar h2{{padding:16px;font-size:16px;background:#1B2B4B;color:#fff}}
    .firma{{padding:12px 16px;border-bottom:1px solid #F3F4F8;cursor:pointer}}
    .firma:hover{{background:#F3F4F8}}
    .fnaziv{{font-weight:700;font-size:13px;color:#1B2B4B}}
    .fposlednja{{font-size:11.5px;color:#96A3B5;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .main{{flex:1;display:flex;flex-direction:column}}
    .main-hdr{{padding:14px 20px;background:#fff;border-bottom:1px solid #E4E6EE;font-weight:700;color:#1B2B4B}}
    .msgs{{flex:1;overflow-y:auto;padding:20px}}
    .msg{{max-width:60%;padding:10px 14px;border-radius:14px;margin-bottom:10px;font-size:13.5px}}
    .msg.klijent{{background:#fff;box-shadow:0 1px 6px rgba(27,43,75,.08)}}
    .msg.racunovodja{{background:#1B2B4B;color:#fff;margin-left:auto}}
    .vreme{{font-size:10px;color:#96A3B5;margin-top:4px}}
    .input-row{{display:flex;gap:10px;padding:16px;background:#fff;border-top:1px solid #E4E6EE}}
    .input-row input{{flex:1;padding:12px;border:1.5px solid #E4E6EE;border-radius:10px;font-size:14px}}
    .input-row button{{padding:12px 20px;background:#1B2B4B;color:#fff;border:none;border-radius:10px;cursor:pointer;font-weight:700}}
    </style></head><body>
    <div class="sidebar"><h2>💬 Razgovori</h2>{firme_html or '<div style="padding:16px;color:#96A3B5">Nema poruka</div>'}</div>
    <div class="main">
      <div class="main-hdr" id="hdr">Izaberite razgovor</div>
      <div class="msgs" id="msgs"></div>
      <div class="input-row"><input id="inp" placeholder="Napišite odgovor..." onkeypress="if(event.key==='Enter')posalji()"><button onclick="posalji()">Pošalji</button></div>
    </div>
    <script>
    const TOKEN = "{token}";
    let aktivnaFirma = null, aktivniNaziv = null;

    function izaberi(fid, naziv){{
      aktivnaFirma = fid; aktivniNaziv = naziv;
      document.getElementById('hdr').textContent = naziv;
      ucitaj();
    }}

    async function ucitaj(){{
      if(!aktivnaFirma) return;
      const r = await fetch('/chat-poruke?firma_id='+encodeURIComponent(aktivnaFirma));
      const j = await r.json();
      const poruke = j.poruke || [];
      document.getElementById('msgs').innerHTML = poruke.map(p =>
        `<div class="msg ${{p.od}}">${{p.tekst}}<div class="vreme">${{p.vreme}}</div></div>`
      ).join('');
      document.getElementById('msgs').scrollTop = 999999;
    }}

    async function posalji(){{
      const inp = document.getElementById('inp');
      const tekst = inp.value.trim();
      if(!tekst || !aktivnaFirma) return;
      inp.value = '';
      await fetch('/chat-odgovori', {{method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{token: TOKEN, firma_id: aktivnaFirma, tekst}})}});
      ucitaj();
    }}

    setInterval(ucitaj, 5000);
    </script></body></html>'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    print(f"\n  PaušalPro Sync — http://localhost:{port}")
    print(f"  Sync URL: http://localhost:{port}/finity-sync")
    print(f"  Portal:   http://localhost:{port}/portal")
    print(f"  Token: {SYNC_TOKEN}\n")
    app.run(host='0.0.0.0', port=port, debug=False)
