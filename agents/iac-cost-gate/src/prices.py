"""The static price table: USD, us-east-1, on-demand, Linux, shared tenancy.

Seed values from the build plan, re-checked against the public AWS on-demand price list on the build
date below; they are list prices, not a quote. Every reply shows `AS_OF`."""

from __future__ import annotations

AS_OF = "2026-11"
CHECKED_ON = "2026-11-06"
HOURS_PER_MONTH = 730
DEFAULT_REGION = "us-east-1"

REGION_MULTIPLIERS = {"us-east-1": 1.00, "us-west-2": 1.00, "eu-west-1": 1.10, "eu-central-1": 1.15,
                      "ap-southeast-1": 1.16, "ap-south-1": 1.05}

EC2_HOURLY = {
    "t3.micro": 0.0104, "t3.small": 0.0208, "t3.medium": 0.0416, "t3.large": 0.0832, "t3.xlarge": 0.1664,
    "m5.large": 0.096, "m5.xlarge": 0.192, "m5.2xlarge": 0.384, "m5.4xlarge": 0.768, "c5.large": 0.085,
    "c5.xlarge": 0.17, "r5.large": 0.126, "r5.xlarge": 0.252, "m6i.large": 0.096, "m6g.large": 0.077,
    "t4g.medium": 0.0336,
}
RDS_HOURLY = {
    "db.t3.micro": 0.017, "db.t3.small": 0.034, "db.t3.medium": 0.068, "db.m5.large": 0.171,
    "db.m5.xlarge": 0.342, "db.r5.large": 0.24, "db.r5.xlarge": 0.48,
}
RDS_STORAGE_GB = {"gp2": 0.115, "gp3": 0.115, "io1": 0.125, "io2": 0.125}
RDS_IOPS_MONTH = 0.10
AURORA_FACTOR = 1.2
MULTI_AZ_FACTOR = 2

EBS_GB = {"gp3": 0.08, "gp2": 0.10, "io1": 0.125, "io2": 0.125, "st1": 0.045, "sc1": 0.015, "standard": 0.05}
EBS_PIOPS_MONTH = 0.065
GP3_FREE_IOPS = 3000
GP3_FREE_MBPS = 125
GP3_IOPS_MONTH = 0.005
GP3_MBPS_MONTH = 0.04

NAT_HOURLY = 0.045
NAT_PER_GB = 0.045
NAT_DEFAULT_GB = 100.0
LB_HOURLY = 0.0225
LCU_HOURLY = 0.008
LB_DEFAULT_LCU = 1
EKS_HOURLY = 0.10
EIP_HOURLY = 0.005
CACHE_HOURLY = {"cache.t3.micro": 0.017, "cache.t3.small": 0.034, "cache.t3.medium": 0.068,
                "cache.m5.large": 0.156, "cache.r5.large": 0.216}
ENDPOINT_HOURLY_PER_AZ = 0.01
ENDPOINT_PER_GB = 0.01
ENDPOINT_DEFAULT_GB = 10.0
DYNAMO_WCU_HOURLY = 0.00065
DYNAMO_RCU_HOURLY = 0.00013

USAGE_PRICED = frozenset({
    "aws_lambda_function", "aws_s3_bucket", "aws_sqs_queue", "aws_sns_topic", "aws_cloudwatch_log_group",
    "aws_api_gateway_rest_api", "aws_apigatewayv2_api", "aws_sfn_state_machine", "aws_route53_record",
})
# No charge of their own (wiring, identity, config): neither priced nor counted as unpriced.
FREE = frozenset({
    "aws_vpc", "aws_subnet", "aws_security_group", "aws_security_group_rule", "aws_route_table", "aws_route",
    "aws_route_table_association", "aws_internet_gateway", "aws_volume_attachment", "aws_eip_association",
    "aws_lb_target_group", "aws_lb_listener", "aws_lb_listener_rule", "aws_lb_target_group_attachment",
    "aws_db_subnet_group", "aws_db_parameter_group", "aws_elasticache_subnet_group", "aws_key_pair",
    "aws_launch_template", "aws_appautoscaling_target", "aws_appautoscaling_policy", "aws_network_acl",
    "aws_vpc_security_group_ingress_rule", "aws_vpc_security_group_egress_rule", "aws_instance_profile",
})
FREE_PREFIXES = ("aws_iam_", "aws_s3_bucket_")


def multiplier(region: str) -> float | None:
    return REGION_MULTIPLIERS.get(region)


def is_free(rtype: str) -> bool:
    return rtype in FREE or rtype.startswith(FREE_PREFIXES)
