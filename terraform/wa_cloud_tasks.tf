# ──────────────────────────────────────────────
# WhatsApp Cloud Tasks Queue + IAM
# ──────────────────────────────────────────────

resource "google_cloud_tasks_queue" "wa_webhook_processing" {
  name     = var.wa_cloud_tasks_queue_name
  location = var.region
  project  = var.project_id

  rate_limits {
    max_dispatches_per_second = 10
    max_concurrent_dispatches = 5
  }

  retry_config {
    max_attempts       = var.wa_cloud_tasks_max_attempts
    min_backoff        = "10s"
    max_backoff        = "300s"
    max_doublings      = 3
  }

  depends_on = [google_project_service.apis]
}

# Service account for Cloud Tasks to call /whatsapp/process
resource "google_service_account" "wa_tasks_invoker" {
  account_id   = "wa-tasks-invoker"
  display_name = "WA Webhook Cloud Tasks Invoker"
  project      = var.project_id
}

# Allow tasks invoker to call Cloud Run v2 service
resource "google_cloud_run_v2_service_iam_member" "wa_tasks_invoker" {
  name     = google_cloud_run_v2_service.app.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.wa_tasks_invoker.email}"
}

# Allow Cloud Run runtime SA to enqueue tasks (queue-scoped least privilege)
resource "google_cloud_tasks_queue_iam_member" "cloud_run_tasks_enqueuer" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_tasks_queue.wa_webhook_processing.name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# Allow Cloud Run runtime SA to set OIDC token on tasks (actAs wa-tasks-invoker)
resource "google_service_account_iam_member" "cloud_run_can_act_as_wa_tasks_invoker" {
  service_account_id = google_service_account.wa_tasks_invoker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}
