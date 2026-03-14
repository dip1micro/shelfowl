from flask import Flask, render_template, request, jsonify
import cv2
import numpy as np
import base64
import os
import datetime
import uuid
import requests
from pathlib import Path

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024
UPLOAD_FOLDER = '/tmp/shelfowl_uploads'
Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)

all_sessions = []

# ── Supabase Config ────────────────────────────────────────────
SUPABASE_URL  = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY  = os.environ.get('SUPABASE_SERVICE_KEY', '')
STORE_ID      = os.environ.get('STORE_ID', '')

# ── YOLO Model ─────────────────────────────────────────────────
model = None

def load_model():
    global model
    if model is None:
        try:
            from ultralytics import YOLO
            model = YOLO('yolov8n.pt')
            print('[YOLO] yolov8n loaded!')
        except Exception as e:
            print(f'[YOLO] Failed: {e}')
    return model

# ── Risk Zones ─────────────────────────────────────────────────
DEFAULT_ZONES = [
    {
        'id'      : 1,
        'name'    : 'Main Aisle',
        'type'    : 'loitering',
        'coords'  : (0, 400, 1080, 1400),
        'color'   : (0, 165, 255),
        'severity': 'medium',
    },
    {
        'id'      : 2,
        'name'    : 'Register Area',
        'type'    : 'high_risk',
        'coords'  : (0, 0, 1080, 400),
        'color'   : (0, 0, 255),
        'severity': 'high',
    },
    {
        'id'      : 3,
        'name'    : 'Exit Zone',
        'type'    : 'exit_monitor',
        'coords'  : (0, 1400, 1080, 1920),
        'color'   : (255, 0, 0),
        'severity': 'low',
    },
]

PERSON_CLASS_ID = 0

# ── Helpers ────────────────────────────────────────────────────
def frame_to_base64(frame):
    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buffer).decode('utf-8')

def is_in_zone(bbox, zone_coords):
    px1, py1, px2, py2 = bbox
    zx1, zy1, zx2, zy2 = zone_coords
    cx = (px1 + px2) // 2
    cy = (py1 + py2) // 2
    return zx1 <= cx <= zx2 and zy1 <= cy <= zy2

def detect_persons(frame, confidence_threshold=0.4):
    yolo = load_model()
    if yolo is None:
        return []
    results = yolo(frame, verbose=False)
    persons = []
    for box in results[0].boxes:
        if int(box.cls[0]) == PERSON_CLASS_ID and float(box.conf[0]) >= confidence_threshold:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            persons.append({'bbox': (x1, y1, x2, y2), 'conf': round(float(box.conf[0]), 2)})
    return persons

def annotate_frame(frame, label, severity='high'):
    annotated = frame.copy()
    h, w = annotated.shape[:2]
    color = (0, 0, 255) if severity == 'high' else (0, 165, 255)
    cv2.rectangle(annotated, (0, 0), (w, 52), (0, 0, 0), -1)
    cv2.putText(annotated, f'SHELFOWL ALERT: {label}',
                (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2)
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cv2.putText(annotated, ts, (w - 230, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
    return annotated

# ── Supabase Push ──────────────────────────────────────────────
def upload_snapshot(snapshot_b64, alert_id):
    if not SUPABASE_KEY or not snapshot_b64:
        return None
    try:
        img_bytes = base64.b64decode(snapshot_b64)
        filename  = f'{alert_id}_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.jpg'
        headers   = {
            'apikey':        SUPABASE_KEY,
            'Authorization': f'Bearer {SUPABASE_KEY}',
            'Content-Type':  'image/jpeg',
            'x-upsert':      'true',
        }
        resp = requests.post(
            f'{SUPABASE_URL}/storage/v1/object/shelf-snapshots/{filename}',
            data=img_bytes, headers=headers, timeout=10
        )
        if resp.status_code in (200, 201):
            url = f'{SUPABASE_URL}/storage/v1/object/public/shelf-snapshots/{filename}'
            print(f'[Supabase] Snapshot: {url}')
            return url
        return None
    except Exception as e:
        print(f'[Supabase] Snapshot error: {e}')
        return None

def push_alert(alert, snapshot_b64=None):
    if not SUPABASE_KEY:
        return False
    snapshot_url = upload_snapshot(snapshot_b64, alert['id'])
    payload = {
        'store_id':     STORE_ID,
        'severity':     alert['severity'],
        'type':         alert['alert_type'].replace(' ', '_'),
        'message':      alert['message'],
        'zone_name':    alert.get('zone_name', ''),
        'camera_name':  'Video Upload',
        'confidence':   alert.get('confidence', 0),
        'duration_sec': alert.get('duration_sec', 0),
        'is_resolved':  False,
        'snapshot_url': snapshot_url,
    }
    headers = {
        'apikey':        SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type':  'application/json',
        'Prefer':        'return=minimal',
    }
    try:
        resp = requests.post(
            f'{SUPABASE_URL}/rest/v1/shelf_alerts',
            json=payload, headers=headers, timeout=5
        )
        if resp.status_code in (200, 201):
            print(f'[Supabase] Alert pushed: {alert["alert_type"]}')
            return True
        print(f'[Supabase] Push failed: {resp.status_code} {resp.text}')
        return False
    except Exception as e:
        print(f'[Supabase] Error: {e}')
        return False

# ── Video Analysis ─────────────────────────────────────────────
def analyze_video(video_path, settings):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {'error': 'Cannot open video'}

    fps          = cap.get(cv2.CAP_PROP_FPS) or 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width        = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration     = total_frames / fps

    loiter_seconds       = float(settings.get('loiter_seconds', 3))
    confidence_threshold = float(settings.get('confidence_threshold', 0.4))
    what_happened        = settings.get('what_happened', '').strip()

    # Scale zones to actual video resolution
    zones = []
    for z in DEFAULT_ZONES:
        zx1, zy1, zx2, zy2 = z['coords']
        scaled = (
            int(zx1 * width / 1080),
            int(zy1 * height / 1920),
            int(zx2 * width / 1080),
            int(zy2 * height / 1920),
        )
        zones.append({**z, 'scaled_coords': scaled})

    zone_entry_frame = {}
    zone_alert_fired = {}
    session_alerts   = []
    frame_log        = []
    frame_count      = 0
    skip             = max(1, int(fps / 5))

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_count += 1

        if frame_count % skip != 0:
            continue

        timestamp = round(frame_count / fps, 2)
        persons   = detect_persons(frame, confidence_threshold)
        active_zone_ids = set()

        for person in persons:
            bbox = person['bbox']
            conf = person['conf']
            for zone in zones:
                zid = zone['id']
                if is_in_zone(bbox, zone['scaled_coords']):
                    active_zone_ids.add(zid)
                    if zid not in zone_entry_frame:
                        zone_entry_frame[zid] = frame_count
                        zone_alert_fired[zid] = False

                    seconds_in_zone = (frame_count - zone_entry_frame[zid]) / fps

                    if seconds_in_zone >= loiter_seconds and not zone_alert_fired.get(zid):
                        zone_alert_fired[zid] = True

                        ann = annotate_frame(frame.copy(),
                            f'{zone["type"].upper()} — {zone["name"]}', zone['severity'])
                        px1, py1, px2, py2 = bbox
                        cv2.rectangle(ann, (px1, py1), (px2, py2), (0, 255, 0), 2)
                        cv2.putText(ann, f'Person {conf:.0%}', (px1, py1 - 8),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                        snapshot_b64 = frame_to_base64(ann)

                        alert = {
                            'id':           str(uuid.uuid4())[:8],
                            'alert_type':   zone['type'],
                            'severity':     zone['severity'],
                            'zone_name':    zone['name'],
                            'camera_name':  'Video Upload',
                            'confidence':   conf,
                            'duration_sec': round(seconds_in_zone, 1),
                            'message':      f'{zone["name"]}: Person loitered {seconds_in_zone:.1f}s (threshold: {loiter_seconds}s)',
                            'timestamp':    datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'video_time':   f'{timestamp}s',
                            'snapshot_b64': snapshot_b64,
                        }
                        session_alerts.append(alert)
                        push_alert(alert, snapshot_b64)

        for zid in list(zone_entry_frame.keys()):
            if zid not in active_zone_ids:
                del zone_entry_frame[zid]
                zone_alert_fired.pop(zid, None)

        if frame_count % int(fps) == 0:
            thumb = cv2.resize(frame, (120, 68))
            frame_log.append({
                'time':      f'{timestamp}s',
                'persons':   len(persons),
                'thumb_b64': frame_to_base64(thumb),
            })

    cap.release()

    result = {
        'session_id':  str(uuid.uuid4())[:8],
        'timestamp':   datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'video':       os.path.basename(video_path),
        'video_info':  {
            'duration_sec': round(duration, 1),
            'fps':          round(fps, 1),
            'resolution':   f'{width}x{height}',
            'frames':       len(frame_log),
        },
        'settings': {
            'loiter_seconds':       loiter_seconds,
            'confidence_threshold': confidence_threshold,
        },
        'what_happened': what_happened,
        'summary': {
            'total_alerts': len(session_alerts),
            'high':   sum(1 for a in session_alerts if a['severity'] == 'high'),
            'medium': sum(1 for a in session_alerts if a['severity'] == 'medium'),
            'low':    sum(1 for a in session_alerts if a['severity'] == 'low'),
        },
        'alerts':    session_alerts,
        'frame_log': frame_log,
    }

    all_sessions.insert(0, result)
    if len(all_sessions) > 20:
        all_sessions.pop()

    return result

# ── Routes ─────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    if 'video' not in request.files:
        return jsonify({'error': 'No video uploaded'}), 400
    file = request.files['video']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400

    filename = f'{uuid.uuid4().hex[:8]}_{file.filename}'
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    settings = {
        'loiter_seconds':       request.form.get('loiter_seconds', 3),
        'confidence_threshold': request.form.get('confidence_threshold', 0.4),
        'what_happened':        request.form.get('what_happened', ''),
    }

    result = analyze_video(filepath, settings)
    try:
        os.remove(filepath)
    except:
        pass
    return jsonify(result)

@app.route('/sessions')
def get_sessions():
    return jsonify([{
        'session_id':    s['session_id'],
        'timestamp':     s['timestamp'],
        'video':         s['video'],
        'total_alerts':  s['summary']['total_alerts'],
        'what_happened': s['what_happened'],
    } for s in all_sessions])

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'sessions': len(all_sessions)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
