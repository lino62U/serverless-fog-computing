import base64
import json
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def send_email_notification(event, context):
    # Decodificar el mensaje de Pub/Sub
    pubsub_message = base64.b64decode(event['data']).decode('utf-8')
    data = json.loads(pubsub_message)

    if data.get('status') == 'MATCH':
        api_key = os.environ.get('SENDGRID_API_KEY')
        sender = os.environ.get('SENDER_EMAIL')
        to_email = os.environ.get('EMAIL_TO')

        html_content = f"""
            <h3>🚨 Alerta de Intruso Detectado</h3>
            <p>Se ha identificado a: <b>{data['matched_with']}</b></p>
            <p>Distancia: {data['distance']:.4f}</p>
            <br>
            <img src="{data['image_url']}" width="400" style="border-radius: 10px;" />
            <br>
            <p><a href="{data['image_url']}">Haga clic aquí para ver la imagen original</a></p>
        """

        message = Mail(
            from_email=sender,
            to_emails=to_email,
            subject=f"ALERTA: Rostro detectado ({data['matched_with']})",
            html_content=html_content
        )

        try:
            sg = SendGridAPIClient(api_key)
            sg.send(message)
            print(f"✅ Correo enviado para {data['matched_with']}")
        except Exception as e:
            print(f"❌ Error enviando correo: {e}")