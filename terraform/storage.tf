# ──────────────────────────────────────────────
# Cloud Storage — Customer Media
# ──────────────────────────────────────────────

resource "google_storage_bucket" "media" {
  name          = "${var.project_id}-${var.app_name}-media"
  location      = var.region
  force_destroy = false

  uniform_bucket_level_access = true

  cors {
    origin          = var.cors_allowed_origins
    method          = ["GET", "POST", "PUT", "OPTIONS"]
    response_header = ["Content-Type", "Content-Disposition"]
    max_age_seconds = 3600
  }

  # Temporary uploads auto-delete after 30 days
  lifecycle_rule {
    condition {
      age            = 30
      matches_prefix = ["uploads/tmp/"]
    }
    action {
      type = "Delete"
    }
  }

  # Move older media to cheaper storage after 90 days
  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  versioning {
    enabled = true
  }

  dynamic "encryption" {
    for_each = var.kms_key_name != null ? [var.kms_key_name] : []
    content {
      default_kms_key_name = encryption.value
    }
  }

  depends_on = [google_project_service.apis]
}

# ──────────────────────────────────────────────
# Artifact Registry — Docker Images
# ──────────────────────────────────────────────

resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = var.app_name
  format        = "DOCKER"
  description   = "Docker images for ${var.app_name}"

  depends_on = [google_project_service.apis]
}
