import os
import json
import base64
from flask import Flask, request
from flask_cors import CORS
from datetime import timedelta

import cv2
import numpy as np
import insightface
from insightface.app import FaceAnalysis

import google.auth
from google.cloud import storage
from google.cloud import pubsub_v1

# --------------------------------------------------
# APP
# --------------------------------------------------

app = Flask(__name__)
CORS(app)

# --------------------------------------------------
# AUTH GCP
# --------------------------------------------------

credentials, project_id = google.auth.default()

KNOWN_BUCKET = os.environ.get("KNOWN_FACES_BUCKET")
TOPIC_ID = os.environ.get("ALERTS_TOPIC")
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
SERVICE_ACCOUNT_EMAIL = os.environ.get("SERVICE_ACCOUNT_EMAIL")

storage_client = storage.Client(credentials=credentials)
publisher = pubsub_v1.PublisherClient(credentials=credentials)

# --------------------------------------------------
# INSIGHTFACE (GLOBAL, SE CARGA 1 VEZ)
# --------------------------------------------------

face_app = FaceAnalysis(
    name="buffalo_l",
    providers=["CPUExecutionProvider"]
)
face_app.prepare(ctx_id=0, det_size=(640, 640))

# --------------------------------------------------
# UTILIDADES
# --------------------------------------------------

def cosine_distance(a, b):
    return 1 - np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def get_signed_url(bucket_name, object_name):
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_name)

        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=1),
            method="GET",
            service_account_email=SERVICE_ACCOUNT_EMAIL,
        )
    except Exception as e:
        print(f"❌ Error firmando URL: {e}", flush=True)
        return None


def download_image(bucket_name, blob_name, dest):
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(dest)


def load_known_faces():
    known = []
    bucket = storage_client.bucket(KNOWN_BUCKET)

    for blob in bucket.list_blobs():
        if not blob.name.lower().endswith((".jpg", ".jpeg", ".png")):
            continue

        tmp = f"/tmp/{os.path.basename(blob.name)}"
        blob.download_to_filename(tmp)

        img = cv2.imread(tmp)
        faces = face_app.get(img)

        if faces:
            known.append({
                "name": blob.name,
                "embedding": faces[0].embedding
            })

    return known


def publish_alert(message: dict):
    if not TOPIC_ID or not PROJECT_ID:
        print("⚠️ TOPIC_ID o PROJECT_ID faltantes", flush=True)
        return

    topic_path = f"projects/{PROJECT_ID}/topics/{TOPIC_ID}"

    try:
        data = json.dumps(message)
        publisher.publish(topic_path, data.encode("utf-8"))
        print(f"🚨 Alerta publicada: {data}", flush=True)
    except Exception as e:
        print(f"❌ Error PubSub: {e}", flush=True)

# --------------------------------------------------
# HANDLER
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

    print(f"📥 Imagen recibida: gs://{bucket_name}/{object_name}", flush=True)

    new_image_path = f"/tmp/new_{os.path.basename(object_name)}"
    download_image(bucket_name, object_name, new_image_path)

    img = cv2.imread(new_image_path)
    faces = face_app.get(img)

    if not faces:
        print("😕 No se detectaron rostros", flush=True)
        return "", 204

    unknown_embedding = faces[0].embedding
    known_faces = load_known_faces()

    THRESHOLD = 0.45  # recomendado InsightFace

    for known in known_faces:
        distance = cosine_distance(known["embedding"], unknown_embedding)

        if distance < THRESHOLD:
            signed_url = get_signed_url(bucket_name, object_name)

            publish_alert({
                "status": "MATCH",
                "matched_with": known["name"],
                "distance": float(distance),
                "image": object_name,
                "image_url": signed_url
            })
            return "", 204

    # Caso: intruso
    publish_alert({
        "status": "UNKNOWN",
        "image": object_name,
        "image_url": get_signed_url(bucket_name, object_name)
    })

    return "", 204


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
