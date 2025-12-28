import os
import json
import base64
from flask import Flask, request
from flask_cors import CORS

import face_recognition
import numpy as np

from google.cloud import storage
from google.cloud import pubsub_v1

app = Flask(__name__)
CORS(app)

# Variables de entorno
KNOWN_BUCKET = os.environ.get("KNOWN_FACES_BUCKET")
TOPIC_ID = os.environ.get("ALERTS_TOPIC")   # 👈 FIX AQUÍ
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")

storage_client = storage.Client()
publisher = pubsub_v1.PublisherClient()

# --------------------------------------------------
# Utilidades
# --------------------------------------------------

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
    if not TOPIC_ID:
        print("⚠️ ALERTS_TOPIC no configurado")
        return

    topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)
    publisher.publish(topic_path, json.dumps(message).encode("utf-8"))
    print("🚨 Alerta publicada:", message)

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

    data = base64.b64decode(envelope["message"]["data"])
    event = json.loads(data)

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
            publish_alert({
                "status": "MATCH",
                "matched_with": known["name"],
                "distance": float(distance),
                "image": object_name
            })
            return "", 204

    publish_alert({
        "status": "UNKNOWN",
        "image": object_name
    })

    return "", 204


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
