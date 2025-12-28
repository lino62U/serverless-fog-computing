import os # <--- CAMBIO 1: Importar la librería OS para leer variables de entorno
import pulumi
import pulumi_gcp as gcp

PROJECT = gcp.config.project
REGION = "us-central1"

# CAMBIO 2: Leer la imagen desde el entorno o usar 'latest' por defecto
# Esto permite que GitHub Actions "inyecte" la imagen con el SHA del commit
container_image = os.environ.get("IMAGE_URI", f"gcr.io/{PROJECT}/face-recognition:latest")

# -------------------------------------------------
# 1. BUCKETS
# -------------------------------------------------

upload_bucket = gcp.storage.Bucket(
    "bucket-fotos-nuevas",
    location=REGION,
)

known_faces_bucket = gcp.storage.Bucket(
    "bucket-rostros-conocidos",
    location=REGION,
)

# -------------------------------------------------
# 2. PUBSUB
# -------------------------------------------------

images_topic = gcp.pubsub.Topic("imagenes-nuevas-topic")

alerts_topic = gcp.pubsub.Topic("alertas-rostros-topic")

# -------------------------------------------------
# 3. SERVICE ACCOUNT (Cloud Run)
# -------------------------------------------------

run_sa = gcp.serviceaccount.Account(
    "sa-reconocimiento",
    account_id="sa-reconocimiento",
)

# Leer imágenes
gcp.projects.IAMMember(
    "sa-storage-read",
    project=PROJECT,
    role="roles/storage.objectViewer",
    member=pulumi.Output.concat("serviceAccount:", run_sa.email),
)

# Publicar alertas
gcp.projects.IAMMember(
    "sa-pubsub-publish",
    project=PROJECT,
    role="roles/pubsub.publisher",
    member=pulumi.Output.concat("serviceAccount:", run_sa.email),
)

# -------------------------------------------------
# 4. CLOUD RUN
# -------------------------------------------------

cloud_run = gcp.cloudrun.Service(
    "face-recognition-service",
    location=REGION,
    template=gcp.cloudrun.ServiceTemplateArgs(
        spec=gcp.cloudrun.ServiceTemplateSpecArgs(
            service_account_name=run_sa.email,
            containers=[
                gcp.cloudrun.ServiceTemplateSpecContainerArgs(
                    image=container_image, # <--- CAMBIO 3: Usar la variable en lugar del texto fijo
                    resources=gcp.cloudrun.ServiceTemplateSpecContainerResourcesArgs(
                        limits={
                            "memory": "4Gi",
                            "cpu": "2",
                        }
                    ),
                    envs=[
                        gcp.cloudrun.ServiceTemplateSpecContainerEnvArgs(
                            name="KNOWN_FACES_BUCKET",
                            value=known_faces_bucket.name,
                        ),
                        gcp.cloudrun.ServiceTemplateSpecContainerEnvArgs(
                            name="ALERTS_TOPIC",
                            value=alerts_topic.name,
                        ),
                        gcp.cloudrun.ServiceTemplateSpecContainerEnvArgs(
                            name="GOOGLE_CLOUD_PROJECT",
                            value=PROJECT,
                        ),
                    ],
                )
            ],
        )
    ),
)

# Permitir invocación desde Pub/Sub
gcp.cloudrun.IamMember(
    "allow-pubsub-invoke",
    service=cloud_run.name,
    location=REGION,
    role="roles/run.invoker",
    member="serviceAccount:service-{}@gcp-sa-pubsub.iam.gserviceaccount.com".format(
        gcp.organizations.get_project(project_id=PROJECT).number
    ),
)

# -------------------------------------------------
# 5. PUBSUB → CLOUD RUN (PUSH)
# -------------------------------------------------

subscription = gcp.pubsub.Subscription(
    "imagenes-push-subscription",
    topic=images_topic.name,
    push_config=gcp.pubsub.SubscriptionPushConfigArgs(
        push_endpoint=cloud_run.statuses[0].url,
        oidc_token=gcp.pubsub.SubscriptionPushConfigOidcTokenArgs(
            service_account_email=run_sa.email
        ),
    ),
)

# -------------------------------------------------
# 6. STORAGE → PUBSUB
# -------------------------------------------------

gcp.storage.Notification(
    "bucket-notification",
    bucket=upload_bucket.name,
    topic=images_topic.id,
    payload_format="JSON_API_V1",
    event_types=["OBJECT_FINALIZE"],
)

# -------------------------------------------------
# OUTPUTS
# -------------------------------------------------

pulumi.export("upload_bucket", upload_bucket.name)
pulumi.export("known_faces_bucket", known_faces_bucket.name)
pulumi.export("cloud_run_url", cloud_run.statuses[0].url)