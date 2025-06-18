import os
import datetime
import json
import uuid
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename # Still useful for filename cleaning, though less critical with S3
import traceback # To print full error tracebacks

# --- S3 Specific Imports and Configuration ---
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv # For loading environment variables locally

# Load environment variables from .env file for local testing
# On AWS Elastic Beanstalk, these variables will be set directly in the EB console
load_dotenv()

# Flag to switch between local file saving simulation and actual S3 interaction
# Set to False for local testing without AWS credentials, True when deploying to AWS
USE_S3 = os.environ.get("USE_S3", "False").lower() == "true" # Check if "true" (case-insensitive)

S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
AWS_REGION = os.environ.get("AWS_REGION")

# Initialize S3 client only if USE_S3 is true
if USE_S3:
    if not S3_BUCKET_NAME or not AWS_REGION:
        print("ERROR: S3_BUCKET_NAME or AWS_REGION environment variables not set. S3 features will not work.")
    else:
        try:
            s3_client = boto3.client(
                's3',
                region_name=AWS_REGION,
                # credentials will be picked up from IAM role on EB, or local AWS config/env vars
            )
            print(f"DEBUG: S3 client initialized for bucket '{S3_BUCKET_NAME}' in region '{AWS_REGION}'.")
        except Exception as e:
            print(f"ERROR: Could not initialize S3 client: {e}")
            traceback.print_exc()
            s3_client = None # Ensure s3_client is None if initialization fails
else:
    print("DEBUG: USE_S3 is False. S3 features are disabled. Simulating local operations.")
    s3_client = None # Ensure s3_client is None when not using S3

# --- Flask App Setup ---
app = Flask(__name__)
# Enable CORS for all origins for local testing. For production, specify your frontend domain(s).
CORS(app)

# Define base directory (where app.py is located)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Define the new main folder for all data (e.g., 'data_storage' inside 'backend')
DATA_BASE_FOLDER = os.path.join(BASE_DIR, 'data_storage')

# Define specific subfolders for audio and demographics within DATA_BASE_FOLDER
# Note: AUDIO_STORAGE_FOLDER will only be used if USE_S3 is False
AUDIO_STORAGE_FOLDER = os.path.join(DATA_BASE_FOLDER, 'audio_recordings_local_simulated')
DEMOGRAPHICS_STORAGE_FOLDER = os.path.join(DATA_BASE_FOLDER, 'demographics_data')

# Ensure these new folders exist at startup
os.makedirs(AUDIO_STORAGE_FOLDER, exist_ok=True)
os.makedirs(DEMOGRAPHICS_STORAGE_FOLDER, exist_ok=True)

# Store these paths in app.config for easy access within routes
app.config['AUDIO_STORAGE_FOLDER'] = AUDIO_STORAGE_FOLDER
app.config['DEMOGRAPHICS_STORAGE_FOLDER'] = DEMOGRAPHICS_STORAGE_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024 # Increased to 32MB for potentially longer audio

# --- Routes ---

# Basic route to check if the server is running
@app.route('/')
def index():
    status = "Backend is running!"
    if USE_S3:
        status += " (S3 integration active)"
    else:
        status += " (Local storage simulation active)"
    return status

# Route to handle demographic data submission
@app.route('/save_demographics', methods=['POST'])
def save_demographics():
    print("\n--- DEBUG: save_demographics route hit ---")
    print(f"DEBUG: request.is_json is {request.is_json}")

    if not request.is_json:
        print("DEBUG: Request is NOT JSON, returning 400.")
        return jsonify({'error': 'Request must be JSON'}), 400

    try:
        data = request.json
        print(f"DEBUG: Received data: {data}")
        participant_id = data.get('prolific_id')

        if not participant_id or participant_id == 'debug_participant_id':
            participant_id = f"debug_participant_{uuid.uuid4().hex[:8]}"
            print(f"DEBUG: No Prolific ID or debug ID found, using generated ID: {participant_id}")
            data['prolific_id'] = participant_id

        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        demographics_filename = f"{participant_id}_demographics_{timestamp}.json"
        demographics_filepath = os.path.join(app.config['DEMOGRAPHICS_STORAGE_FOLDER'], demographics_filename)
        print(f"DEBUG: Demographics filepath: {demographics_filepath}")

        with open(demographics_filepath, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Demographics saved for {participant_id} to: {demographics_filepath}")
        print("--- DEBUG: save_demographics route finished successfully ---")
        return jsonify({'message': 'Demographics saved successfully!', 'participant_id': participant_id}), 200
    except Exception as e:
        print(f"--- ERROR: An unexpected error occurred in save_demographics: {e} ---")
        traceback.print_exc()
        return jsonify({'error': f'Failed to save demographics: {str(e)}'}), 500


# NEW ROUTE: For frontend to request a pre-signed URL to upload audio directly to S3
@app.route('/get-presigned-url', methods=['POST'])
def get_presigned_url():
    print("\n--- DEBUG: get-presigned-url route hit ---")
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    file_type = request.json.get('fileType')
    participant_id = request.json.get('participantId', 'unknown_participant')
    task_type = request.json.get('taskType', 'unknown_task')
    duration_seconds = request.json.get('durationSeconds', 'unknown_duration')

    if not file_type:
        return jsonify({"error": "fileType is required"}), 400

    # Ensure participant_id is clean and safe for filename/S3 key
    safe_participant_id = secure_filename(participant_id)
    safe_task_type = secure_filename(task_type)

    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f") # Add microseconds for high uniqueness

    # Define the S3 key (path within the bucket)
    # Example: speech_recordings/participant_ABC/free_speech_20250617170000123456.webm
    s3_key = f"speech_recordings/{safe_participant_id}/{safe_task_type}_{timestamp}.{file_type.split('/')[-1]}"
    print(f"DEBUG: Proposed S3 Key / Local Filename: {s3_key}")

    if USE_S3 and s3_client:
        try:
            presigned_url = s3_client.generate_presigned_url(
                'put_object',
                Params={
                    'Bucket': S3_BUCKET_NAME,
                    'Key': s3_key,
                    'ContentType': file_type, # Important for S3 to recognize the file type
                },
                ExpiresIn=300 # URL valid for 5 minutes (adjust as needed)
            )
            print(f"DEBUG: Generated pre-signed URL for {s3_key}")
            return jsonify({
                "presignedUrl": presigned_url,
                "s3Key": s3_key, # Return the S3 key so frontend knows where it was uploaded
                "message": "Upload directly to this URL."
            })
        except ClientError as e:
            error_message = f"Error generating pre-signed URL: {e}"
            print(f"ERROR: {error_message}")
            traceback.print_exc()
            return jsonify({"error": error_message}), 500
        except Exception as e:
            error_message = f"An unexpected error occurred during S3 presigned URL generation: {e}"
            print(f"ERROR: {error_message}")
            traceback.print_exc()
            return jsonify({"error": error_message}), 500
    else:
        # --- Local Simulation for Audio Upload (if USE_S3 is False) ---
        # In this mode, we're not actually getting a presigned URL,
        # but returning a dummy path that the frontend might use for local simulation
        # or simply acknowledging the request.
        # The frontend's upload_audio function needs to handle this gracefully.
        print("DEBUG: USE_S3 is False. Simulating pre-signed URL generation for local audio storage.")
        # For local testing, the frontend will still send the file to a *different* route,
        # or this route would return a placeholder URL.
        # However, the best practice is for frontend to use the same logic (call /get-presigned-url)
        # then if this route returns a dummy URL, frontend knows it's a local test.
        local_audio_filepath = os.path.join(app.config['AUDIO_STORAGE_FOLDER'], os.path.basename(s3_key))
        return jsonify({
            "presignedUrl": "http://localhost:5000/simulate_local_audio_upload", # Dummy URL for frontend to target locally
            "s3Key": s3_key, # Still return the S3 key structure for consistency
            "localFilePath": local_audio_filepath, # Indicate where it would save locally
            "message": "Local simulation: Backend generated dummy URL for local upload."
        })


# NEW ROUTE: Frontend calls this *after* it has successfully uploaded audio to S3 (or simulated locally)
@app.route('/upload-complete', methods=['POST'])
def upload_complete():
    print("\n--- DEBUG: upload-complete route hit ---")
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.json
    s3_key = data.get('s3Key')
    participant_id = data.get('participantId')
    task_type = data.get('taskType')
    duration_seconds = data.get('durationSeconds')
    local_filepath = data.get('localFilePath') # Only present in local simulation

    if not s3_key or not participant_id:
        return jsonify({"error": "Missing s3Key or participantId"}), 400

    print(f"DEBUG: Confirmed upload for participant {participant_id}, task {task_type}, S3 Key/Local Path: {s3_key or local_filepath}. Duration: {duration_seconds}s")

    # --- IMPORTANT: Future Step ---
    # In a real application, this is where you would:
    # 1. Store the s3_key, participant_id, task_type, duration_seconds, and a timestamp
    #    in a DATABASE (e.g., PostgreSQL via SQLAlchemy, or MongoDB) for easy querying and management.
    # 2. You might also trigger other processes (e.g., a background job to process the audio).
    # Currently, it just prints a confirmation.
    # --- End IMPORTANT ---

    return jsonify({"message": "Upload confirmation received and processed."}), 200


# --- Local Audio Upload Simulation Route (Only used if USE_S3 is False and frontend targets this) ---
# This route is a fallback for local testing where frontend would directly send audio to Flask.
# In the S3 direct upload model, this route ideally would not be used.
# The frontend's JavaScript needs to be adjusted to either call /get-presigned-url and then upload directly
# or, if it detects local mode, upload to this route.
@app.route('/simulate_local_audio_upload', methods=['PUT', 'POST'])
def simulate_local_audio_upload():
    print("\n--- DEBUG: simulate_local_audio_upload route hit (LOCAL MODE) ---")
    if not request.data:
        print("ERROR: No data received for local audio simulation.")
        return jsonify({'error': 'No audio data received'}), 400

    # For local simulation, the frontend would directly PUT/POST the audio data here.
    # We'll just save it to the local_simulated folder.
    try:
        # Determine filename based on headers or generate one
        # This is simplified; in a real scenario, you'd get metadata from headers or frontend
        content_type = request.headers.get('Content-Type', 'application/octet-stream')
        extension = content_type.split('/')[-1] if '/' in content_type else 'bin'
        filename = f"local_sim_{uuid.uuid4().hex}.{extension}"
        file_path = os.path.join(app.config['AUDIO_STORAGE_FOLDER'], filename)

        with open(file_path, 'wb') as f:
            f.write(request.data)

        print(f"DEBUG: Simulated local audio saved to: {file_path}")
        return jsonify({'message': 'Local audio simulation successful!', 'filepath': file_path}), 200
    except Exception as e:
        print(f"--- ERROR: An unexpected error occurred in simulate_local_audio_upload: {e} ---")
        traceback.print_exc()
        return jsonify({'error': f'Failed to simulate local audio save: {str(e)}'}), 500


# --- Main Run Block ---
if __name__ == '__main__':
    # When running locally, Flask will pick up .env variables.
    # For deployment to Elastic Beanstalk, you'll set these directly in EB config.
    app.run(host='0.0.0.0', port=5000, debug=True) # debug=True is good for local testing