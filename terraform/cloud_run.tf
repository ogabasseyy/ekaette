# ──────────────────────────────────────────────
# Cloud Run v2 Service
# ──────────────────────────────────────────────

resource "google_cloud_run_v2_service" "app" {
  name                = var.app_name
  location            = var.region
  deletion_protection = var.deletion_protection
  ingress             = var.ingress

  template {
    service_account = google_service_account.cloud_run_sa.email

    # Pin WebSocket connections to the same container instance
    session_affinity = true

    # Long-running Live API audio sessions
    timeout = var.timeout

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.container_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle = true
      }

      # Core environment variables
      dynamic "env" {
        for_each = merge({
          GOOGLE_CLOUD_PROJECT      = var.project_id
          GOOGLE_CLOUD_LOCATION     = var.region
          GOOGLE_GENAI_USE_VERTEXAI = "TRUE"
          MEDIA_BUCKET              = google_storage_bucket.media.name
          APP_NAME                  = var.app_name
          SESSION_BACKEND           = "database"
          MEMORY_BACKEND            = "auto"
          LOG_LEVEL                 = "INFO"
        }, var.env_vars)

        content {
          name  = env.key
          value = env.value
        }
      }

      startup_probe {
        http_get {
          path = "/"
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        failure_threshold     = 3
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# Public access (unauthenticated web app)
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
