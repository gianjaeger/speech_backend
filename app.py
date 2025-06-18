import os
import datetime
import json
import uuid
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
import traceback

# --- Firebase Storage Setup ---
import firebase_admin
from firebase_admin import credentials, storage

USE_FIREBASE = True  # Set this to False for local development/testing

# Initialize Firebase app once
if USE_FIREBASE and not firebase_admin._apps:
    cred = credentials.Certificate("/etc/secrets/serviceAccountKey.json")
    firebase_admin.initialize_app(cred, {
        'storageBucket': 'speech-recorder-ff0e4'
    })
    bucket = storage.bucket()
else:
    bucket = None

# --- Flask App Setup ---
app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_BASE_FOLDER = os.path.join(BASE_DIR, 'data_storage')
AUDIO_STORAGE_FOLDER = os.path.join(DATA_BASE_FOLDER, 'audio_recordings_local_simulated')
DEMOGRAPHICS_STORAGE_FOLDER = os.path.join(DATA_BASE_FOLDER, 'demographics_data')

os.makedirs(AUDIO_STORAGE_FOLDER, exist_ok=True)
os.makedirs(DEMOGRAPHICS_STORAGE_FOLDER, exist_ok=True)

app.config['AUDIO_STORAGE_FOLDER'] = AUDIO_STORAGE_FOLDER
app.config['DEMOGRAPHICS_STORAGE_FOLDER'] = DEMOGRAPHICS_STORAGE_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024

@app.route('/')
def index():
    return "Backend is running! (Firebase integration active)" if USE_FIREBASE else "Backend is running! (Local simulation mode)"

@app.route('/save_demographics', methods=['POST'])
def save_demographics():
    try:
        data = request.json
        participant_id = data.get('prolific_id') or f"debug_participant_{uuid.uuid4().hex[:8]}"
        data['prolific_id'] = participant_id

        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{participant_id}_demographics_{timestamp}.json"

        json_str = json.dumps(data, indent=4)

        if USE_FIREBASE and bucket:
            blob = bucket.blob(f"{participant_id}/{filename}")
            blob.upload_from_string(json_str, content_type='application/json')
            return jsonify({'message': 'Demographics saved to Firebase!', 'participant_id': participant_id}), 200
        else:
            local_path = os.path.join(app.config['DEMOGRAPHICS_STORAGE_FOLDER'], filename)
            with open(local_path, 'w') as f:
                f.write(json_str)
            return jsonify({'message': 'Demographics saved locally!', 'participant_id': participant_id}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': f'Failed to save demographics: {str(e)}'}), 500

@app.route('/upload_audio', methods=['POST'])
def upload_audio():
    try:
        file = request.files['audio_data']
        participant_id = request.form['participant_id']
        task_type = request.form['task_type']
        timestamp = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%S')
        filename = f"{participant_id}/{task_type}_{timestamp}.webm"

        if USE_FIREBASE and bucket:
            blob = bucket.blob(filename)
            blob.upload_from_file(file, content_type='audio/webm')
            return jsonify({'message': 'Audio uploaded to Firebase', 'path': filename}), 200
        else:
            local_path = os.path.join(AUDIO_STORAGE_FOLDER, secure_filename(filename))
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            file.save(local_path)
            return jsonify({'message': 'Audio saved locally', 'path': local_path}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': f'Failed to upload audio: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
