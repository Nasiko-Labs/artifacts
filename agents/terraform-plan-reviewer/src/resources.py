"""Pure: resource-type tables (AWS, GCP, Azure). Matching is exact on the type, or by prefix where an entry
ends in `*`. A test lists every entry so additions are deliberate."""

from __future__ import annotations

STATEFUL_AWS = (
    "aws_db_instance", "aws_rds_cluster", "aws_rds_cluster_instance", "aws_rds_global_cluster", "aws_dynamodb_table",
    "aws_dynamodb_global_table", "aws_s3_bucket", "aws_ebs_volume", "aws_efs_file_system", "aws_fsx_*",
    "aws_elasticache_cluster", "aws_elasticache_replication_group", "aws_memorydb_cluster",
    "aws_elasticsearch_domain", "aws_opensearch_domain", "aws_redshift_cluster", "aws_docdb_cluster",
    "aws_neptune_cluster", "aws_kinesis_stream", "aws_msk_cluster", "aws_mq_broker", "aws_sqs_queue", "aws_kms_key",
    "aws_secretsmanager_secret", "aws_ssm_parameter", "aws_cloudwatch_log_group", "aws_backup_vault",
    "aws_ecr_repository", "aws_cognito_user_pool", "aws_route53_zone", "aws_glacier_vault", "aws_timestream_database",
    "aws_qldb_ledger",
)
STATEFUL_GCP = (
    "google_sql_database_instance", "google_sql_database", "google_spanner_instance", "google_spanner_database",
    "google_bigtable_instance", "google_bigtable_table", "google_storage_bucket", "google_compute_disk",
    "google_compute_region_disk", "google_filestore_instance", "google_redis_instance", "google_memcache_instance",
    "google_bigquery_dataset", "google_bigquery_table", "google_firestore_database", "google_alloydb_cluster",
    "google_alloydb_instance", "google_pubsub_topic", "google_pubsub_subscription", "google_kms_key_ring",
    "google_kms_crypto_key", "google_secret_manager_secret", "google_container_cluster",
    "google_logging_project_bucket_config",
)
STATEFUL_AZURE = (
    "azurerm_mssql_server", "azurerm_mssql_database", "azurerm_mssql_managed_instance", "azurerm_postgresql_server",
    "azurerm_postgresql_flexible_server", "azurerm_postgresql_database", "azurerm_mysql_server",
    "azurerm_mysql_flexible_server", "azurerm_mariadb_server", "azurerm_cosmosdb_account",
    "azurerm_cosmosdb_sql_database", "azurerm_storage_account", "azurerm_storage_container", "azurerm_managed_disk",
    "azurerm_key_vault", "azurerm_key_vault_key", "azurerm_key_vault_secret", "azurerm_redis_cache",
    "azurerm_kubernetes_cluster", "azurerm_servicebus_namespace", "azurerm_eventhub_namespace",
    "azurerm_log_analytics_workspace", "azurerm_recovery_services_vault", "azurerm_synapse_workspace",
    "azurerm_data_lake_store",
)
STATEFUL = STATEFUL_AWS + STATEFUL_GCP + STATEFUL_AZURE

# Types whose names look stateful but hold no data of their own.
STATELESS = (
    "aws_s3_bucket_policy", "aws_s3_bucket_public_access_block", "aws_s3_bucket_versioning",
    "aws_s3_bucket_lifecycle_configuration", "aws_s3_bucket_server_side_encryption_configuration",
    "aws_s3_bucket_acl", "aws_s3_bucket_ownership_controls", "aws_s3_bucket_notification", "aws_s3_bucket_cors_configuration",
    "aws_sqs_queue_policy", "aws_kms_alias", "aws_secretsmanager_secret_version", "aws_db_subnet_group",
    "aws_db_parameter_group", "aws_rds_cluster_parameter_group", "aws_elasticache_subnet_group",
    "aws_elasticache_parameter_group", "aws_ecs_cluster", "aws_eks_cluster", "aws_eks_node_group",
    "aws_dynamodb_table_item", "aws_sns_topic", "aws_sns_topic_subscription", "aws_sns_topic_policy",
    "aws_ecr_lifecycle_policy", "aws_ecr_repository_policy", "aws_lb_target_group", "google_storage_bucket_iam_member",
    "google_storage_bucket_iam_binding", "google_pubsub_topic_iam_member", "google_container_node_pool",
    "google_kms_crypto_key_iam_member", "google_secret_manager_secret_version", "azurerm_storage_account_network_rules",
    "azurerm_key_vault_access_policy", "azurerm_kubernetes_cluster_node_pool", "azurerm_role_assignment",
)
STATEFUL_HINTS = ("database", "db_", "bucket", "volume", "disk", "snapshot", "vault", "kms", "secret", "queue",
                  "topic", "cache", "cluster", "table", "storage")

SERVICE_PREFIXES = ("aws_", "google_", "azurerm_")

NETWORK = ("aws_vpc", "aws_subnet", "aws_security_group", "aws_route_table", "aws_eip", "aws_internet_gateway",
           "google_compute_network", "google_compute_subnetwork", "azurerm_virtual_network", "azurerm_subnet")
COMPUTE_DOWNTIME = ("aws_instance", "aws_lb", "aws_alb", "aws_nat_gateway", "aws_eks_cluster", "aws_autoscaling_group",
                    "google_compute_instance", "google_container_node_pool", "azurerm_virtual_machine*", "azurerm_lb",
                    "azurerm_application_gateway", "azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine")

_FOUNDATION_HINTS = ("vpc", "subnet", "network", "security_group", "route_table", "internet_gateway", "nat_gateway",
                     "_eip", "kms", "iam", "secret", "firewall", "key_vault", "role", "policy")
_EDGE_HINTS = ("route53", "dns", "acm_certificate", "certificate", "cloudfront", "cdn", "cloudwatch", "alarm",
               "monitoring", "dashboard", "log_metric", "frontdoor")
_DATA_HINTS = ("db_", "_db", "database", "bucket", "queue", "volume", "disk", "table", "cache", "topic", "storage",
               "stream", "_sql", "redis", "dynamodb", "rds", "efs", "fsx")


def matches(rtype: str, table: tuple[str, ...]) -> bool:
    return any(rtype == entry or (entry.endswith("*") and rtype.startswith(entry[:-1])) for entry in table)


def is_stateful(rtype: str) -> bool:
    return matches(rtype, STATEFUL)


def maybe_stateful(rtype: str) -> bool:
    """Not in a table but the name suggests stored data (a MEDIUM finding, never a guardrail block)."""
    if is_stateful(rtype) or matches(rtype, STATELESS) or rtype.startswith("data."):
        return False
    return any(hint in rtype for hint in STATEFUL_HINTS)


def known_provider(rtype: str) -> bool:
    return rtype.startswith(SERVICE_PREFIXES)


def service(rtype: str) -> str:
    """`aws_s3_bucket` → `aws_s3`: the provider and its first type word, for scope checks."""
    parts = rtype.split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else rtype


def tier(rtype: str) -> str:
    """foundation | data | compute | edge: the apply-order tier of a type."""
    if any(h in rtype for h in _EDGE_HINTS):
        return "edge"
    if any(h in rtype for h in _FOUNDATION_HINTS):
        return "foundation"
    if is_stateful(rtype) or any(h in rtype for h in _DATA_HINTS):
        return "data"
    return "compute"
