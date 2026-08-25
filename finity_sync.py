#!/usr/bin/env python3
from flask import Flask, request, jsonify, render_template_string
import json, os
from datetime import datetime

app = Flask(__name__)
SYNC_TOKEN = os.environ.get('FINITY_TOKEN', 'pausalpro2026')
DATA_FAJL  = '/tmp/finity_data.json'
LOG_FAJL   = '/tmp/sync_log.json'

@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type,X-Finity-Token'
    r.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return r

@app.route('/finity-sync', methods=['OPTIONS'])
def opt(): return '', 200

def log_akciju(akcija, ip):
    try:
        log = json.loads(open(LOG_FAJL).read()) if os.path.exists(LOG_FAJL) else []
    except: log = []
    log.insert(0, {'akcija': akcija, 'ip': ip, 'vreme': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    open(LOG_FAJL, 'w').write(json.dumps(log[:50], ensure_ascii=False))

@app.route('/finity-sync', methods=['POST', 'GET'])
def finity_sync():
    if request.method == 'GET':
        return jsonify({'ok': True, 'poruka': 'PausalPro Sync Server radi!'})
    try:
        data = request.get_json(force=True, silent=True) or {}
    except:
        return jsonify({'ok': False, 'poruka': 'Neispravan JSON.'}), 400

    token = data.get('token') or request.headers.get('X-Finity-Token', '')
    if token != SYNC_TOKEN:
        return jsonify({'ok': False, 'poruka': 'Pogresna lozinka.'}), 403

    akcija = data.get('akcija', '')
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    log_akciju(akcija, ip)

    if akcija == 'ping':
        return jsonify({'ok': True, 'poruka': 'Veza radi. PaushalPro Sync aktivan.'})

    if akcija == 'save':
        finity_data = data.get('data')
        if not finity_data:
            return jsonify({'ok': False, 'poruka': 'Nema podataka.'}), 400
        data_str = json.dumps(finity_data, ensure_ascii=False)
        velicina = len(data_str.encode('utf-8'))
        firma_naziv = 'Nepoznato'
        try:
            s = finity_data.get('settings', {})
            firma_naziv = s.get('naziv') or s.get('nazivPun') or 'Nepoznato'
        except: pass
        open(DATA_FAJL, 'w', encoding='utf-8').write(data_str)
        meta = {'sacuvano': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'velicina': velicina, 'firma': firma_naziv, 'ip': ip}
        open(DATA_FAJL + '.meta', 'w').write(json.dumps(meta, ensure_ascii=False))
        return jsonify({'ok': True, 'poruka': 'Sacuvano.', 'velicina': velicina,
                       'vreme': meta['sacuvano']})

    if akcija == 'load':
        if not os.path.exists(DATA_FAJL):
            return jsonify({'ok': False, 'poruka': 'Nema podataka na serveru.'}), 404
        finity_data = json.loads(open(DATA_FAJL, encoding='utf-8').read())
        return jsonify({'ok': True, 'data': finity_data, 'fajl': 'finity_data.json'})

    return jsonify({'ok': False, 'poruka': f'Nepoznata akcija: {akcija}'}), 400

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

    settings  = finity.get('settings', {}) if isinstance(finity, dict) else {}
    fakture   = list(finity.get('fakture', {}).values()) if isinstance(finity.get('fakture'), dict) else finity.get('fakture', [])
    kpo       = list(finity.get('kpo', {}).values()) if isinstance(finity.get('kpo'), dict) else finity.get('kpo', [])
    komitenti = list(finity.get('komitenti', {}).values()) if isinstance(finity.get('komitenti'), dict) else finity.get('komitenti', [])

    prihod_kpo = sum(float(k.get('iznos',0) or 0) for k in kpo
                     if str(k.get('tip','') or '').lower() in ['p','prihod','u','uplata',''])

    fakt_placene, fakt_iznos, poslednje = 0, 0.0, []
    for f in reversed(fakture):
        status = str(f.get('status','') or '').lower()
        if 'placen' in status or 'paid' in status: fakt_placene += 1
        iznos = float(f.get('ukupno', f.get('iznos', 0)) or 0)
        fakt_iznos += iznos
        if len(poslednje) < 8:
            poslednje.append({'broj': f.get('broj', f.get('br','—')),
                              'klijent': f.get('klijent', f.get('kupac','—')),
                              'iznos': iznos, 'datum': f.get('datum','—'),
                              'status': f.get('status','—')})

    return jsonify({'ima_podataka': True,
        'firma': {'naziv': settings.get('naziv') or settings.get('nazivPun','—'),
                  'pib': settings.get('pib',''), 'forma': settings.get('forma',''),
                  'adresa': settings.get('adresa',''), 'email': settings.get('email','')},
        'sacuvano': meta.get('sacuvano','—'),
        'velicina_kb': round(os.path.getsize(DATA_FAJL)/1024, 1),
        'statistike': {'fakture_ukupno': len(fakture), 'fakture_placene': fakt_placene,
                       'fakture_neplacene': len(fakture)-fakt_placene,
                       'fakture_iznos': round(fakt_iznos,2), 'kpo_zapisa': len(kpo),
                       'ukupni_prihod_kpo': round(prihod_kpo,2), 'komitenti': len(komitenti)},
        'poslednje_fakture': poslednje, 'sync_log': log[:15]})

@app.route('/')
def dashboard():
    return '<h2>PaushalPro Sync radi!</h2><p>Endpoint: /finity-sync</p>'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
