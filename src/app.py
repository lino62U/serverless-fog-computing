import os
import json
import base64
from flask import Flask, request
from flask_cors import CORS
from datetime import timedelta

import face_recognition
import numpy as np

import google.auth
from google.cloud import storage
from google.cloud import pubsub_v1

app = Flask(__name__)
CORS(app)

# --- CONFIGURACIÓN Y CLIENTES ---
# Usamos google.auth para obtener las credenciales de la identidad de Cloud Run
credentials, project_id = google.auth.default()

KNOWN_BUCKET = os.environ.get("KNOWN_FACES_BUCKET")
TOPIC_ID = os.environ.get("ALERTS_TOPIC")
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
# Necesitaremos el email de la SA para firmar la URL
SERVICE_ACCOUNT_EMAIL = os.environ.get("SERVICE_ACCOUNT_EMAIL")

storage_client = storage.Client(credentials=credentials)
publisher = pubsub_v1.PublisherClient(credentials=credentials)

# --------------------------------------------------
# Utilidades
# --------------------------------------------------

def get_signed_url(bucket_name, object_name):
    """Genera una URL firmada usando la identidad de la Service Account"""
    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(object_name)

        # Es vital usar version='v4' y proveer el service_account_email en Cloud Run
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=1),
            method="GET",
            service_account_email=SERVICE_ACCOUNT_EMAIL
        )
    except Exception as e:
        print(f"❌ Error al generar URL firmada: {e}")
        return None

def download_image(bucket_name, blob_name, dest):
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(dest)

def load_known_encodings():
    encodings = []
    bucket = storage_client.bucket(KNOWN_BUCKET)
    
    for blob in bucket.list_blobs():
        if not blob.name.lower().endswith((".jpg", ".jpeg", ".png")):
            continue

        tmp = f"/tmp/{os.path.basename(blob.name)}"
        blob.download_to_filename(tmp)

        img = face_recognition.load_image_file(tmp)
        faces = face_recognition.face_encodings(img)

        if faces:
            encodings.append({
                "name": blob.name,
                "encoding": faces[0]
            })
    return encodings

def publish_alert(message: dict):
    if not TOPIC_ID or not PROJECT_ID:
        print(f"⚠️ Error: TOPIC_ID o PROJECT_ID faltantes", flush=True)
        return

    topic_path = f"projects/{PROJECT_ID}/topics/{TOPIC_ID}"
    
    try:
        # Usamos json.dumps para que el log y el mensaje sean consistentes
        data = json.dumps(message)
        publisher.publish(topic_path, data.encode("utf-8"))
        print(f"🚨 Alerta publicada: {data}", flush=True)
    except Exception as e:
        print(f"❌ Error al publicar en PubSub: {e}", flush=True)

# --------------------------------------------------
# HANDLER PRINCIPAL
# --------------------------------------------------

@app.route("/", methods=["POST", "OPTIONS"])
def handler():
    if request.method == "OPTIONS":
        return "", 204

    envelope = request.get_json(silent=True)
    if not envelope or "message" not in envelope:
        return "Bad Request", 400

    data_payload = base64.b64decode(envelope["message"]["data"])
    event = json.loads(data_payload)

    bucket_name = event["bucket"]
    object_name = event["name"]

    print(f"📥 Imagen recibida: gs://{bucket_name}/{object_name}")

    new_image_path = f"/tmp/new_{os.path.basename(object_name)}"
    download_image(bucket_name, object_name, new_image_path)

    unknown_img = face_recognition.load_image_file(new_image_path)
    unknown_faces = face_recognition.face_encodings(unknown_img)

    if not unknown_faces:
        print("😕 No se detectaron rostros")
        return "", 204

    unknown_encoding = unknown_faces[0]
    known_faces = load_known_encodings()

    for known in known_faces:
        distance = np.linalg.norm(known["encoding"] - unknown_encoding)
        if distance < 0.45:
            # Generar el link para el correo
            signed_url = get_signed_url(bucket_name, object_name)
            
            publish_alert({
                "status": "MATCH",
                "matched_with": known["name"],
                "distance": float(distance),
                "image": object_name,
                "image_url": signed_url
            })
            return "", 204

    # Caso: Rostro no reconocido
    publish_alert({
        "status": "UNKNOWN",
        "image": object_name,
        "image_url": get_signed_url(bucket_name, object_name)
    })

    return "", 204

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)