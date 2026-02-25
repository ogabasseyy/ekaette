# ──────────────────────────────────────────────
# Firestore Database
# ──────────────────────────────────────────────

resource "google_firestore_database" "main" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"

  # ABANDON keeps the database alive if terraform destroy runs
  deletion_policy = "ABANDON"

  depends_on = [google_project_service.apis]
}
