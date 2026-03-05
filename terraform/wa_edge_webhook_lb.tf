# ──────────────────────────────────────────────
# WhatsApp HTTPS Load Balancer + Cloud Armor
# Three-hostname architecture:
#   wa_webhook_domain     → Meta webhook only
#   wa_service_api_domain → /whatsapp/send + /whatsapp/process
#   app_public_domain     → existing app/websocket traffic
# ──────────────────────────────────────────────

# Serverless NEG for Cloud Run app
resource "google_compute_region_network_endpoint_group" "wa_neg" {
  name                  = "wa-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.app.name
  }

  depends_on = [google_project_service.apis]
}

# Helper locals for Cloud Armor CEL rules
locals {
  wa_vm_has_cidrs = length(var.wa_vm_egress_cidrs) > 0
  wa_vm_cidr_expr = local.wa_vm_has_cidrs ? join(" || ", [
    for cidr in var.wa_vm_egress_cidrs : "inIpRange(origin.ip, '${cidr}')"
  ]) : "false"
}

# ── Cloud Armor Security Policy ──

resource "google_compute_security_policy" "wa_edge_policy" {
  name = "wa-edge-policy"

  # /whatsapp/send: allow only from trusted VM egress CIDRs
  rule {
    priority = 900
    action   = "allow"
    match {
      expr {
        expression = "request.host == '${var.wa_service_api_domain}' && request.path == '/api/v1/at/whatsapp/send' && (${local.wa_vm_cidr_expr})"
      }
    }
  }

  rule {
    priority = 910
    action   = "deny(403)"
    match {
      expr {
        expression = "request.host == '${var.wa_service_api_domain}' && request.path == '/api/v1/at/whatsapp/send'"
      }
    }
  }

  # /whatsapp/process: throttle above queue dispatch ceiling
  rule {
    priority = 920
    action   = "throttle"
    match {
      expr {
        expression = "request.host == '${var.wa_service_api_domain}' && request.path == '/api/v1/at/whatsapp/process'"
      }
    }
    rate_limit_options {
      rate_limit_threshold {
        count        = 1200
        interval_sec = 60
      }
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
    }
  }

  # Deny non-allowed paths on WA service domain
  rule {
    priority = 930
    action   = "deny(403)"
    match {
      expr {
        expression = "request.host == '${var.wa_service_api_domain}' && request.path != '/api/v1/at/whatsapp/send' && request.path != '/api/v1/at/whatsapp/process'"
      }
    }
  }

  # Webhook endpoint throttling
  rule {
    priority = 1000
    action   = "throttle"
    match {
      expr {
        expression = "request.host == '${var.wa_webhook_domain}' && request.path == '/api/v1/at/whatsapp/webhook'"
      }
    }
    rate_limit_options {
      rate_limit_threshold {
        count        = 60
        interval_sec = 60
      }
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
    }
  }

  # Deny non-webhook paths on webhook domain
  rule {
    priority = 1010
    action   = "deny(403)"
    match {
      expr {
        expression = "request.host == '${var.wa_webhook_domain}' && request.path != '/api/v1/at/whatsapp/webhook'"
      }
    }
  }

  # Deny WA paths on app public domain
  rule {
    priority = 1020
    action   = "deny(403)"
    match {
      expr {
        expression = "request.host == '${var.app_public_domain}' && request.path.startsWith('/api/v1/at/whatsapp')"
      }
    }
  }

  # Deny unknown hosts
  rule {
    priority = 1030
    action   = "deny(403)"
    match {
      expr {
        expression = "request.host != '${var.wa_webhook_domain}' && request.host != '${var.wa_service_api_domain}' && request.host != '${var.app_public_domain}'"
      }
    }
  }

  # Default: allow known hosts
  rule {
    priority = 2147483647
    action   = "allow"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}

# ── Backend Service + Cloud Armor ──

resource "google_compute_backend_service" "wa_backend" {
  name            = "wa-backend"
  protocol        = "HTTP"
  security_policy = google_compute_security_policy.wa_edge_policy.id

  backend {
    group = google_compute_region_network_endpoint_group.wa_neg.id
  }
}

# ── URL Map with host/path routing + header hygiene ──

resource "google_compute_url_map" "wa_url_map" {
  name            = "wa-url-map"
  default_service = google_compute_backend_service.wa_backend.id

  host_rule {
    hosts        = [var.wa_webhook_domain]
    path_matcher = "wa-webhook-matcher"
  }

  host_rule {
    hosts        = [var.wa_service_api_domain]
    path_matcher = "wa-service-matcher"
  }

  host_rule {
    hosts        = [var.app_public_domain]
    path_matcher = "app-public-matcher"
  }

  path_matcher {
    name            = "wa-webhook-matcher"
    default_service = google_compute_backend_service.wa_backend.id

    route_rules {
      priority = 100
      match_rules {
        full_path_match = "/api/v1/at/whatsapp/webhook"
      }
      service = google_compute_backend_service.wa_backend.id
      header_action {
        request_headers_to_remove = ["X-Edge-RateLimit-Checked"]
        request_headers_to_add {
          header_name  = "X-Edge-RateLimit-Checked"
          header_value = "1"
          replace      = true
        }
      }
    }
  }

  path_matcher {
    name            = "wa-service-matcher"
    default_service = google_compute_backend_service.wa_backend.id

    route_rules {
      priority = 100
      match_rules {
        full_path_match = "/api/v1/at/whatsapp/send"
      }
      service = google_compute_backend_service.wa_backend.id
    }

    route_rules {
      priority = 110
      match_rules {
        full_path_match = "/api/v1/at/whatsapp/process"
      }
      service = google_compute_backend_service.wa_backend.id
    }
  }

  # Existing app/public domain: catch-all routing
  path_matcher {
    name            = "app-public-matcher"
    default_service = google_compute_backend_service.wa_backend.id
  }
}

# ── Managed SSL Certificate ──

resource "google_compute_managed_ssl_certificate" "wa_cert" {
  name = "wa-managed-cert"
  managed {
    domains = compact([
      var.wa_webhook_domain,
      var.wa_service_api_domain,
      var.app_public_domain,
    ])
  }
}

# ── HTTPS Proxy ──

resource "google_compute_target_https_proxy" "wa_proxy" {
  name             = "wa-https-proxy"
  url_map          = google_compute_url_map.wa_url_map.id
  ssl_certificates = [google_compute_managed_ssl_certificate.wa_cert.id]
}

# ── Global Forwarding Rule ──

resource "google_compute_global_address" "wa_ip" {
  name = "wa-lb-ip"
}

resource "google_compute_global_forwarding_rule" "wa_https" {
  name       = "wa-https-forwarding"
  target     = google_compute_target_https_proxy.wa_proxy.id
  port_range = "443"
  ip_address = google_compute_global_address.wa_ip.address
}
