#!/usr/bin/env python3
"""
Finity Sync Server + PaušalPro Live Dashboard
Prima podatke iz Finity aplikacije i prikazuje ih u realnom vremenu.

Pokretanje: python3 finity_sync.py
Dashboard:  http://localhost:5001
Sync URL:   http://localhost:5001/finity-sync
Token:      pausalpro2026
"""
from flask import Flask, request, jsonify, render_template_string, g
import sqlite3, json, os
from datetime import datetime

app = Flask(__name__)
SYNC_TOKEN = os.environ.get('FINITY_TOKEN', 'pausalpro2026')
DB_PATH = os.path.join(os.path.dirname(__file__), 'finity_sync.db')

# ─── BAZA ───
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db: db.close()

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS finity_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id TEXT NOT NULL DEFAULT 'default',
            firma_naziv TEXT,
            data_json TEXT NOT NULL,
            velicina INTEGER DEFAULT 0,
            sacuvano TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            akcija TEXT,
            firma_id TEXT,
            ip TEXT,
            vreme TEXT DEFAULT (datetime('now'))
        );
        """)
    print("✅ Baza inicijalizovana:", DB_PATH)

# ─── CORS ───
@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type,X-Finity-Token'
    r.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return r

@app.route('/finity-sync', methods=['OPTIONS'])
def sync_options(): return '', 200

# ─── GLAVNI SYNC ENDPOINT ───
@app.route('/finity-sync', methods=['POST'])
def finity_sync():
    try:
        data = request.get_json(force=True) or {}
    except:
        return jsonify({'ok': False, 'poruka': 'Neispravan JSON.'}), 400

    token = data.get('token') or request.headers.get('X-Finity-Token', '')
    if token != SYNC_TOKEN:
        return jsonify({'ok': False, 'poruka': 'Pogresna lozinka/token.'}), 403

    akcija = data.get('akcija', '')
    ip = request.remote_addr
    db = get_db()

    db.execute("INSERT INTO sync_log (akcija, ip) VALUES (?,?)", (akcija, ip))
    db.commit()

    if akcija == 'ping':
        print(f"[{datetime.now().strftime('%H:%M:%S')}] PING od {ip}")
        return jsonify({'ok': True, 'poruka': 'Veza radi. PaušalPro Sync aktivan.'})

    if akcija == 'save':
        finity_data = data.get('data', {})
        if not finity_data:
            return jsonify({'ok': False, 'poruka': 'Nema podataka.'}), 400

        data_str = json.dumps(finity_data, ensure_ascii=False)
        velicina = len(data_str.encode('utf-8'))

        firma_naziv = None
        if isinstance(finity_data, dict):
            settings = finity_data.get('settings', {})
            if isinstance(settings, dict):
                firma_naziv = settings.get('naziv') or settings.get('nazivPun')
        firma_id = firma_naziv or 'default'

        existing = db.execute("SELECT id FROM finity_data WHERE firma_id=?", (firma_id,)).fetchone()
        if existing:
            db.execute("UPDATE finity_data SET data_json=?, velicina=?, firma_naziv=?, sacuvano=datetime('now') WHERE firma_id=?",
                (data_str, velicina, firma_naziv, firma_id))
        else:
            db.execute("INSERT INTO finity_data (firma_id, firma_naziv, data_json, velicina) VALUES (?,?,?,?)",
                (firma_id, firma_naziv, data_str, velicina))
        db.commit()

        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAVE od {ip} — {firma_naziv or 'default'} ({round(velicina/1024,1)} KB)")
        return jsonify({'ok': True, 'poruka': 'Sacuvano.', 'velicina': velicina,
                       'vreme': datetime.now().strftime('%d.%m.%Y %H:%M:%S')})

    if akcija == 'load':
        row = db.execute("SELECT * FROM finity_data ORDER BY sacuvano DESC LIMIT 1").fetchone()
        if not row:
            return jsonify({'ok': False, 'poruka': 'Nema sacuvanih podataka na serveru.'}), 404
        print(f"[{datetime.now().strftime('%H:%M:%S')}] LOAD od {ip}")
        return jsonify({'ok': True, 'data': json.loads(row['data_json']),
                       'fajl': row['firma_naziv'] or 'backup', 'sacuvano': row['sacuvano']})

    return jsonify({'ok': False, 'poruka': f'Nepoznata akcija: {akcija}'}), 400

# ─── API ZA DASHBOARD ───
@app.route('/api/finity/podaci')
def api_podaci():
    db = get_db()
    latest = db.execute("SELECT * FROM finity_data ORDER BY sacuvano DESC LIMIT 1").fetchone()
    if not latest:
        return jsonify({'ima_podataka': False})

    finity = json.loads(latest['data_json'])

    def safe_list(key):
        v = finity.get(key, [])
        return v if isinstance(v, list) else []

    def safe_dict(key):
        v = finity.get(key, {})
        return v if isinstance(v, dict) else {}

    settings = safe_dict('settings')
    fakture = safe_list('fakture')
    kpo = safe_list('kpo')
    komitenti = safe_list('komitenti')

    # Prihodi iz KPO knjige
    ukupni_prihod_kpo = 0
    for k in kpo:
        try:
            iznos = float(k.get('iznos', 0) or 0)
            tip = str(k.get('tip', '') or '').lower()
            if tip in ['p', 'prihod', 'u', 'uplata', '']:
                ukupni_prihod_kpo += iznos
        except: pass

    # Fakture
    fakt_placene = 0
    fakt_iznos = 0.0
    for f in fakture:
        try:
            status = str(f.get('status', '') or '').lower()
            if any(s in status for s in ['placen', 'plac', 'paid']):
                fakt_placene += 1
            iznos = float(f.get('ukupno', f.get('iznos', f.get('total', 0))) or 0)
            fakt_iznos += iznos
        except: pass

    log = db.execute("SELECT * FROM sync_log ORDER BY vreme DESC LIMIT 20").fetchall()

    # Poslednje fakture
    poslednje_fakture = []
    for f in fakture[-10:]:
        try:
            poslednje_fakture.append({
                'broj': f.get('broj', f.get('br', '—')),
                'klijent': f.get('klijent', f.get('kupac', f.get('naziv', '—'))),
                'iznos': float(f.get('ukupno', f.get('iznos', 0)) or 0),
                'datum': f.get('datum', f.get('datumIzdavanja', '—')),
                'status': f.get('status', '—'),
            })
        except: pass
    poslednje_fakture.reverse()

    return jsonify({
        'ima_podataka': True,
        'firma': {
            'naziv': settings.get('naziv') or settings.get('nazivPun', 'Nepoznato'),
            'pib': settings.get('pib', ''),
            'forma': settings.get('forma', ''),
            'adresa': settings.get('adresa', ''),
            'email': settings.get('email', ''),
        },
        'sacuvano': latest['sacuvano'],
        'velicina_kb': round(latest['velicina'] / 1024, 1),
        'statistike': {
            'fakture_ukupno': len(fakture),
            'fakture_placene': fakt_placene,
            'fakture_neplacene': len(fakture) - fakt_placene,
            'fakture_iznos': round(fakt_iznos, 2),
            'kpo_zapisa': len(kpo),
            'ukupni_prihod_kpo': round(ukupni_prihod_kpo, 2),
            'komitenti': len(komitenti),
        },
        'poslednje_fakture': poslednje_fakture,
        'sync_log': [{'akcija': r['akcija'], 'vreme': r['vreme'], 'ip': r['ip']} for r in log],
    })

# ─── LIVE DASHBOARD HTML ───
DASHBOARD = '''<!DOCTYPE html>
<html lang="sr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PaušalPro Live</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--navy:#1B2B4B;--gold:#C9A84C;--slate:#F3F4F8;--brd:#E4E6EE;--green:#27AE60;--red:#E74C3C;--orange:#F39C12}
body{font-family:'Inter',sans-serif;background:var(--slate);color:var(--navy)}
.hdr{background:var(--navy);padding:16px 28px;display:flex;align-items:center;justify-content:space-between}
.logo{display:flex;align-items:center;gap:10px}
.lic{width:36px;height:36px;background:var(--gold);border-radius:9px;display:flex;align-items:center;justify-content:center;font-weight:800;color:var(--navy);font-size:16px}
.lnm{font-size:17px;font-weight:800;color:#fff}.lnm span{color:var(--gold)}
.live{display:flex;align-items:center;gap:7px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);border-radius:20px;padding:6px 14px;font-size:12px;color:rgba(255,255,255,.8);font-weight:600}
.ldot{width:7px;height:7px;border-radius:50%;background:#4DD996;animation:bl 1.5s ease infinite}
@keyframes bl{0%,100%{opacity:1}50%{opacity:.3}}
.sbar{background:#fff;border-bottom:1px solid var(--brd);padding:9px 28px;display:flex;align-items:center;gap:12px;font-size:12.5px}
.sok{color:var(--green);font-weight:700}.serr{color:var(--red);font-weight:700}.swait{color:var(--orange);font-weight:600}
.stime{color:#96A3B5;margin-left:4px}
.rbtn{margin-left:auto;padding:6px 14px;background:var(--navy);color:#fff;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer}
.rbtn:hover{opacity:.85}
.wrap{padding:24px 28px}
.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:18px}
.card{background:#fff;border-radius:14px;padding:20px;box-shadow:0 1px 8px rgba(27,43,75,.08)}
.card.nv{background:var(--navy)}
.sic{width:40px;height:40px;border-radius:10px;background:var(--slate);display:flex;align-items:center;justify-content:center;font-size:19px;margin-bottom:12px}
.card.nv .sic{background:rgba(255,255,255,.1)}
.sv{font-size:26px;font-weight:800;color:var(--navy);letter-spacing:-.5px}
.card.nv .sv{color:#fff}
.sl{font-size:11.5px;color:#96A3B5;margin-top:4px}
.card.nv .sl{color:rgba(255,255,255,.45)}
.sbg{float:right;background:#E8F8F1;color:var(--green);font-size:10.5px;font-weight:700;padding:3px 9px;border-radius:20px;margin-top:-36px}
.sbr{float:right;background:#FDEDEC;color:var(--red);font-size:10.5px;font-weight:700;padding:3px 9px;border-radius:20px;margin-top:-36px}
.sbo{float:right;background:#FEF9E7;color:var(--orange);font-size:10.5px;font-weight:700;padding:3px 9px;border-radius:20px;margin-top:-36px}
.pt{font-size:14px;font-weight:700;color:var(--navy);margin-bottom:14px}
.ir{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--slate)}
.ir:last-child{border:none}
.il{font-size:12.5px;color:#5A6880}
.iv{font-size:13px;font-weight:700;color:var(--navy)}
.iv.g{color:var(--green)}.iv.r{color:var(--red)}.iv.o{color:var(--orange)}
table{width:100%;border-collapse:collapse}
th{padding:9px 12px;text-align:left;font-size:11px;font-weight:700;color:#96A3B5;text-transform:uppercase;letter-spacing:.4px;background:var(--slate);border-bottom:1px solid var(--brd)}
td{padding:11px 12px;border-bottom:1px solid var(--slate);font-size:13px;color:var(--navy)}
tr:last-child td{border:none}
tr:hover td{background:#FAFBFE}
.ch{display:inline-flex;align-items:center;gap:4px;font-size:10.5px;font-weight:600;padding:3px 8px;border-radius:20px}
.chg{background:#E8F8F1;color:var(--green)}.chr{background:#FDEDEC;color:var(--red)}.cho{background:#FEF9E7;color:var(--orange)}
.lg{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--slate);font-size:12px}
.lg:last-child{border:none}
.lgic{width:26px;height:26px;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0}
.lgs{background:#E8F8F1}.lgp{background:#EEF2FF}.lgl{background:#FEF9E7}
.nd{text-align:center;padding:60px 20px}
.nd .ni{font-size:56px;margin-bottom:14px}
.nd h2{font-size:20px;font-weight:800;color:var(--navy);margin-bottom:8px}
.nd p{font-size:13.5px;color:#5A6880;line-height:1.7;margin-bottom:20px}
.cbox{background:var(--navy);border-radius:12px;padding:16px 20px;text-align:left;max-width:440px;margin:0 auto}
.cbox code{font-size:12.5px;color:#4DD996;font-family:monospace;line-height:1.9}
.cbox .cm{color:rgba(255,255,255,.3)}
</style>
</head>
<body>
<div class="hdr">
  <div class="logo"><div class="lic">П</div><div class="lnm">Paušal<span>Pro</span> &nbsp;·&nbsp; Live Dashboard</div></div>
  <div class="live"><div class="ldot"></div>Osvežavam svakih 8 sekundi</div>
</div>
<div class="sbar">
  <span id="ss">Učitavam...</span><span id="st" class="stime"></span>
  <button class="rbtn" onclick="load()">🔄 Osveži</button>
</div>
<div class="wrap" id="wrap"><div style="text-align:center;padding:80px;color:#96A3B5">Učitavam podatke iz Finity...</div></div>

<script>
const fmt = n => Math.round(n||0).toLocaleString('sr-RS');
const pct = (a,b) => b ? Math.round(a/b*100)+'%' : '0%';

async function load(){
  try{
    const d = await fetch('/api/finity/podaci').then(r=>r.json());
    render(d);
    document.getElementById('st').textContent = 'Poslednje: '+new Date().toLocaleTimeString('sr-RS');
  } catch(e){
    document.getElementById('ss').innerHTML='<span class="serr">❌ Greška pri učitavanju</span>';
  }
}

function statusChip(s){
  if(!s) return '<span class="ch cho">—</span>';
  const l=s.toLowerCase();
  if(l.includes('placen')||l.includes('paid')) return '<span class="ch chg">✅ Plaćena</span>';
  if(l.includes('kasni')||l.includes('overdue')) return '<span class="ch chr">⏰ Kasni</span>';
  return '<span class="ch cho">📤 Poslata</span>';
}

function render(d){
  const w=document.getElementById('wrap');
  if(!d.ima_podataka){
    document.getElementById('ss').innerHTML='<span class="swait">⏳ Čekam podatke iz Finity...</span>';
    w.innerHTML=`<div class="card nd">
      <div class="ni">🔌</div>
      <h2>Finity još nije poslao podatke</h2>
      <p>Otvorite <b>Finity aplikaciju</b>, idite na<br><b>Podešavanja → Cloud sinhronizacija</b><br>i unesite ove podatke:</p>
      <div class="cbox"><code>
        <span class="cm">Adresa servera:</span><br>
        http://localhost:5001/finity-sync<br><br>
        <span class="cm">Lozinka / token:</span><br>
        pausalpro2026
      </code></div>
    </div>`;
    return;
  }

  document.getElementById('ss').innerHTML='<span class="sok">✅ Sinhrono sa Finity</span>';
  const f=d.firma, s=d.statistike, log=d.sync_log||[], fak=d.poslednje_fakture||[];
  const sac=new Date(d.sacuvano).toLocaleString('sr-RS');

  w.innerHTML=`
  <div class="g4">
    <div class="card nv">
      <div class="sic">🏢</div>
      <div class="sv" style="font-size:${f.naziv&&f.naziv.length>16?'16px':'22px'}">${f.naziv||'—'}</div>
      <div class="sl">${f.forma||''}${f.pib?' · PIB '+f.pib:''}</div>
    </div>
    <div class="card">
      <div class="sbg">📄 ${s.fakture_ukupno}</div>
      <div class="sic">💰</div>
      <div class="sv">${fmt(s.fakture_iznos)}</div>
      <div class="sl">Ukupno fakturisano (RSD)</div>
    </div>
    <div class="card">
      <div class="sbg">✅ ${s.fakture_placene}</div>
      <div class="sic">📖</div>
      <div class="sv">${s.kpo_zapisa}</div>
      <div class="sl">KPO knjiga — zapisa</div>
    </div>
    <div class="card">
      <div class="sbo">👥 ${s.komitenti}</div>
      <div class="sic">💼</div>
      <div class="sv">${fmt(s.ukupni_prihod_kpo)}</div>
      <div class="sl">Prihod iz KPO (RSD)</div>
    </div>
  </div>

  <div class="g3">
    <div class="card">
      <div class="pt">🏢 Firma</div>
      <div class="ir"><span class="il">Naziv</span><span class="iv">${f.naziv||'—'}</span></div>
      <div class="ir"><span class="il">PIB</span><span class="iv">${f.pib||'—'}</span></div>
      <div class="ir"><span class="il">Forma</span><span class="iv">${f.forma||'—'}</span></div>
      <div class="ir"><span class="il">Adresa</span><span class="iv" style="font-size:11.5px">${f.adresa||'—'}</span></div>
      <div class="ir"><span class="il">Email</span><span class="iv" style="font-size:11.5px">${f.email||'—'}</span></div>
    </div>
    <div class="card">
      <div class="pt">📊 Fakture</div>
      <div class="ir"><span class="il">Ukupno</span><span class="iv">${s.fakture_ukupno}</span></div>
      <div class="ir"><span class="il">Plaćene</span><span class="iv g">${s.fakture_placene}</span></div>
      <div class="ir"><span class="il">Neplaćene</span><span class="iv r">${s.fakture_neplacene}</span></div>
      <div class="ir"><span class="il">Ukupan iznos</span><span class="iv">${fmt(s.fakture_iznos)} RSD</span></div>
      <div class="ir"><span class="il">Naplata</span><span class="iv g">${pct(s.fakture_placene,s.fakture_ukupno)}</span></div>
    </div>
    <div class="card">
      <div class="pt">🔄 Sync info</div>
      <div class="ir"><span class="il">Poslednji sync</span><span class="iv" style="font-size:11px">${sac}</span></div>
      <div class="ir"><span class="il">Veličina podataka</span><span class="iv">${d.velicina_kb} KB</span></div>
      <div class="ir"><span class="il">KPO zapisa</span><span class="iv">${s.kpo_zapisa}</span></div>
      <div class="ir"><span class="il">Komitenti</span><span class="iv">${s.komitenti}</span></div>
      <div class="ir"><span class="il">Prihod KPO</span><span class="iv g">${fmt(s.ukupni_prihod_kpo)} RSD</span></div>
    </div>
  </div>

  <div class="g2">
    <div class="card">
      <div class="pt">📄 Poslednje fakture</div>
      ${fak.length===0?'<div style="color:#96A3B5;text-align:center;padding:16px">Nema faktura.</div>':
      `<table>
        <tr><th>Broj</th><th>Klijent</th><th>Iznos</th><th>Status</th></tr>
        ${fak.map(f=>`<tr>
          <td style="font-weight:600">${f.broj||'—'}</td>
          <td>${f.klijent||'—'}</td>
          <td style="font-weight:700">${fmt(f.iznos)} RSD</td>
          <td>${statusChip(f.status)}</td>
        </tr>`).join('')}
      </table>`}
    </div>
    <div class="card">
      <div class="pt">⚡ Sync log</div>
      ${log.length===0?'<div style="color:#96A3B5;text-align:center;padding:16px">Nema sync aktivnosti.</div>':
      log.slice(0,12).map(l=>`
      <div class="lg">
        <div class="lgic ${l.akcija==='save'?'lgs':l.akcija==='ping'?'lgp':'lgl'}">
          ${l.akcija==='save'?'💾':l.akcija==='ping'?'📡':'📥'}
        </div>
        <div style="flex:1"><strong>${l.akcija.toUpperCase()}</strong> <span style="color:#96A3B5">${l.ip}</span></div>
        <div style="color:#96A3B5;font-size:11px">${new Date(l.vreme).toLocaleTimeString('sr-RS')}</div>
      </div>`).join('')}
    </div>
  </div>`;
}

load();
setInterval(load, 8000);
</script>
</body>
</html>'''

@app.route('/')
@app.route('/dashboard')
def dashboard():
    return render_template_string(DASHBOARD)

if __name__ == '__main__':
    init_db()
    print("""
  ╔══════════════════════════════════════════════╗
  ║   Finity Sync + PaušalPro Live Dashboard    ║
  ║                                              ║
  ║   Dashboard: http://localhost:5001           ║
  ║   Sync URL:  http://localhost:5001/finity-sync
  ║   Token:     pausalpro2026                  ║
  ║                                              ║
  ║   U Finity → Podešavanja → Cloud sync:      ║
  ║   URL: http://localhost:5001/finity-sync    ║
  ║   Lozinka: pausalpro2026                    ║
  ╚══════════════════════════════════════════════╝
    """)
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
