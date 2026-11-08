"""Pure: resource-type category tables (AWS, GCP, Azure, Kubernetes) used by the risk score.

Named `tf_types` (not `types`) so it never shadows the standard library module. Matching is exact on the
type, or by prefix where an entry ends in `*`. The strings are Terraform domain data taken from plans, not
connector or tool names.
"""

from __future__ import annotations

# Databases, caches, buckets, volumes, queues, key management, file systems, stateful sets / volumes.
STATEFUL = (
    "aws_db_instance", "aws_rds_cluster", "aws_rds_cluster_instance", "aws_rds_global_cluster",
    "aws_dynamodb_table", "aws_dynamodb_global_table", "aws_s3_bucket", "aws_ebs_volume", "aws_efs_file_system",
    "aws_fsx_*", "aws_elasticache_cluster", "aws_elasticache_replication_group", "aws_memorydb_cluster",
    "aws_elasticsearch_domain", "aws_opensearch_domain", "aws_redshift_cluster", "aws_docdb_cluster",
    "aws_neptune_cluster", "aws_kinesis_stream", "aws_msk_cluster", "aws_mq_broker", "aws_sqs_queue",
    "aws_kms_key", "aws_secretsmanager_secret", "aws_backup_vault", "aws_ecr_repository", "aws_glacier_vault",
    "google_sql_database_instance", "google_sql_database", "google_spanner_instance", "google_spanner_database",
    "google_bigtable_instance", "google_storage_bucket", "google_compute_disk", "google_compute_region_disk",
    "google_filestore_instance", "google_redis_instance", "google_memcache_instance", "google_bigquery_dataset",
    "google_bigquery_table", "google_firestore_database", "google_alloydb_*", "google_pubsub_topic",
    "google_pubsub_subscription", "google_kms_key_ring", "google_kms_crypto_key", "google_secret_manager_secret",
    "azurerm_mssql_server", "azurerm_mssql_database", "azurerm_postgresql_*", "azurerm_mysql_*",
    "azurerm_mariadb_server", "azurerm_cosmosdb_*", "azurerm_storage_account", "azurerm_storage_container",
    "azurerm_managed_disk", "azurerm_key_vault", "azurerm_key_vault_key", "azurerm_redis_cache",
    "azurerm_servicebus_namespace", "azurerm_eventhub_namespace",
    "kubernetes_stateful_set", "kubernetes_stateful_set_v1", "kubernetes_persistent_volume",
    "kubernetes_persistent_volume_v1", "kubernetes_persistent_volume_claim", "kubernetes_persistent_volume_claim_v1",
)

# VPCs, subnets, routes, gateways, load balancers, DNS records, firewall / security-group rules.
NETWORK = (
    "aws_vpc", "aws_subnet", "aws_route", "aws_route_table", "aws_route_table_association", "aws_internet_gateway",
    "aws_nat_gateway", "aws_eip", "aws_lb", "aws_alb", "aws_elb", "aws_lb_listener*", "aws_lb_target_group*",
    "aws_route53_record", "aws_route53_zone", "aws_security_group", "aws_security_group_rule",
    "aws_vpc_security_group_*", "aws_network_acl*", "aws_vpc_peering_connection", "aws_transit_gateway*",
    "google_compute_network", "google_compute_subnetwork", "google_compute_route", "google_compute_router*",
    "google_compute_firewall", "google_compute_forwarding_rule", "google_compute_global_forwarding_rule",
    "google_compute_backend_service", "google_compute_url_map", "google_dns_record_set",
    "azurerm_virtual_network", "azurerm_subnet", "azurerm_route_table", "azurerm_route", "azurerm_lb*",
    "azurerm_network_security_group", "azurerm_network_security_rule", "azurerm_application_gateway",
    "azurerm_dns_*_record", "azurerm_firewall*", "azurerm_public_ip",
    "kubernetes_service", "kubernetes_service_v1", "kubernetes_ingress", "kubernetes_ingress_v1",
    "kubernetes_network_policy", "kubernetes_network_policy_v1",
)

# IAM roles / policies / bindings, service accounts, key grants.
IDENTITY = (
    "aws_iam_*", "aws_kms_grant", "google_service_account", "google_service_account_key",
    "google_*_iam_member", "google_*_iam_binding", "google_*_iam_policy", "google_project_iam_custom_role",
    "azurerm_role_assignment", "azurerm_role_definition", "azurerm_user_assigned_identity",
    "kubernetes_role*", "kubernetes_cluster_role*", "kubernetes_service_account*",
)

TAG_KEYS = frozenset({"tags", "tags_all", "labels"})

# Attribute flags: +1 each (max +2) when an update touches one of these keys.
FLAG_KEYS = ("engine_version", "instance_class", "cidr_block", "ingress", "egress", "policy", "assume_role_policy")

# Keys whose values are never echoed (a pasted JSON plan can hold secrets).
SENSITIVE_HINTS = ("password", "secret", "token", "key", "credential", "private")

# The first of these present (as a plain string) is a resource's distinctive identifier for text search.
# Host-like values first: they are what services and manifests in other repositories reference.
IDENTIFIER_KEYS = ("address", "endpoint", "fqdn", "dns_name", "domain_name", "bucket", "identifier",
                   "cluster_identifier", "cluster_id", "function_name", "queue_name", "table_name", "name")


def matches(rtype: str, table: tuple[str, ...]) -> bool:
    for entry in table:
        if entry == rtype:
            return True
        if "*" in entry:
            head, _, tail = entry.partition("*")
            if rtype.startswith(head) and rtype.endswith(tail) and len(rtype) >= len(head) + len(tail):
                return True
    return False


def category(rtype: str) -> str:
    """`stateful`, `network`, `identity`, or `` (none). Stateful wins over the others."""
    if matches(rtype, STATEFUL):
        return "stateful"
    if matches(rtype, IDENTITY):
        return "identity"
    if matches(rtype, NETWORK):
        return "network"
    return ""


def is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(hint in lowered for hint in SENSITIVE_HINTS)
