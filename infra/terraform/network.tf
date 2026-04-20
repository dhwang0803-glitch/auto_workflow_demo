# ADR-020 §2 — Custom VPC + Service Networking peering for Cloud SQL Private IP.
#
# This lets Cloud SQL drop its public IP and Cloud Run reach it via Direct VPC
# Egress (ADR-020 §2, Serverless VPC Connector v1 rejected). The allocated IP
# range is reserved for Google-managed services (Cloud SQL etc.) — do not
# overlap it with the subnet that Cloud Run egresses through.

resource "google_project_service" "network_apis" {
  for_each = toset([
    "compute.googleapis.com",
    "vpcaccess.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "vpc" {
  name                    = "auto-workflow-${var.environment}"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"

  depends_on = [google_project_service.network_apis]
}

# Subnet used by Cloud Run Direct VPC Egress. Needs its own non-overlapping
# range; /28 is the minimum Cloud Run accepts and plenty for egress-only use.
resource "google_compute_subnetwork" "cloudrun" {
  name          = "auto-workflow-${var.environment}-cloudrun"
  region        = var.region
  network       = google_compute_network.vpc.id
  ip_cidr_range = var.cloudrun_subnet_cidr
  purpose       = "PRIVATE"
  stack_type    = "IPV4_ONLY"
}

# Address block Google reserves for service producers (Cloud SQL lives here).
# Peered into our VPC below so Cloud SQL gets a routable private IP.
resource "google_compute_global_address" "private_services_range" {
  name          = "auto-workflow-${var.environment}-services-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services_range.name]

  depends_on = [google_project_service.apis]
}
