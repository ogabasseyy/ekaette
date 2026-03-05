provider "google" {
  project = var.project_id
  region  = var.region
}

# ──────────────────────────────────────────────
# Enable Required GCP APIs
# ──────────────────────────────────────────────

locals {
  required_apis = [
    "run.googleapis.com",
    "firestore.googleapis.com",
    "storage.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "iam.googleapis.com",
    "aiplatform.googleapis.com",
    "cloudtasks.googleapis.com",
    "compute.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.required_apis)

  project                    = var.project_id
  service                    = each.value
  disable_dependent_services = false
  disable_on_destroy         = false
}
