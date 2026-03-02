# ──────────────────────────────────────────────
# Service Account for Cloud Run
# ──────────────────────────────────────────────

resource "google_service_account" "cloud_run_sa" {
  account_id   = "${var.app_name}-run-sa"
  display_name = "Ekaette Cloud Run Service Account"
  description  = "Least-privilege SA for the Ekaette Cloud Run service"
}

# Roles: only what the service actually needs
locals {
  cloud_run_roles = [
    "roles/datastore.user",      # Firestore read/write
    "roles/storage.objectAdmin", # Cloud Storage CRUD
    "roles/aiplatform.user",     # Vertex AI / Gemini API
    "roles/logging.logWriter",   # Cloud Logging
  ]
}

resource "google_project_iam_member" "cloud_run_sa_roles" {
  for_each = toset(local.cloud_run_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}
