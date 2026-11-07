"""Pure: a CloudFormation template (JSON or YAML with the short intrinsic tags) to resources, mapped onto
the terraform-style types and attribute names pricing and the waste rules use. `Ref` to a parameter takes
its Default; any other intrinsic stays unresolved."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

import resources as rs

# CFN type -> (type, {CFN property: attribute})
TYPES: dict[str, tuple[str, dict[str, str]]] = {
    "AWS::EC2::Instance": ("aws_instance", {"InstanceType": "instance_type"}),
    "AWS::RDS::DBInstance": ("aws_db_instance", {"DBInstanceClass": "instance_class", "MultiAZ": "multi_az",
                                                 "AllocatedStorage": "allocated_storage", "StorageType": "storage_type",
                                                 "Iops": "iops", "Engine": "engine"}),
    "AWS::EC2::Volume": ("aws_ebs_volume", {"Size": "size", "VolumeType": "type", "Iops": "iops",
                                            "Throughput": "throughput"}),
    "AWS::EC2::VolumeAttachment": ("aws_volume_attachment", {"VolumeId": "volume_id"}),
    "AWS::EC2::NatGateway": ("aws_nat_gateway", {"AllocationId": "allocation_id"}),
    "AWS::EC2::EIP": ("aws_eip", {"InstanceId": "instance"}),
    "AWS::EC2::EIPAssociation": ("aws_eip_association", {}),
    "AWS::ElasticLoadBalancingV2::LoadBalancer": ("aws_lb", {"Type": "load_balancer_type"}),
    "AWS::EKS::Cluster": ("aws_eks_cluster", {}),
    "AWS::ElastiCache::CacheCluster": ("aws_elasticache_cluster", {"CacheNodeType": "node_type",
                                                                  "NumCacheNodes": "num_cache_nodes"}),
    "AWS::EC2::VPCEndpoint": ("aws_vpc_endpoint", {"VpcEndpointType": "vpc_endpoint_type", "SubnetIds": "subnet_ids"}),
    "AWS::DynamoDB::Table": ("aws_dynamodb_table", {"BillingMode": "billing_mode"}),
    "AWS::Lambda::Function": ("aws_lambda_function", {"MemorySize": "memory_size", "Architectures": "architectures"}),
    "AWS::S3::Bucket": ("aws_s3_bucket", {"LifecycleConfiguration": "lifecycle_rule"}),
    "AWS::Logs::LogGroup": ("aws_cloudwatch_log_group", {"RetentionInDays": "retention_in_days"}),
    "AWS::SQS::Queue": ("aws_sqs_queue", {}),
    "AWS::SNS::Topic": ("aws_sns_topic", {}),
    "AWS::ApiGateway::RestApi": ("aws_api_gateway_rest_api", {}),
    "AWS::StepFunctions::StateMachine": ("aws_sfn_state_machine", {}),
    "AWS::Route53::RecordSet": ("aws_route53_record", {}),
    "AWS::ApplicationAutoScaling::ScalableTarget": ("aws_appautoscaling_target", {}),
    "AWS::EC2::VPC": ("aws_vpc", {}), "AWS::EC2::Subnet": ("aws_subnet", {}),
    "AWS::EC2::SecurityGroup": ("aws_security_group", {}), "AWS::EC2::InternetGateway": ("aws_internet_gateway", {}),
    "AWS::EC2::RouteTable": ("aws_route_table", {}), "AWS::EC2::Route": ("aws_route", {}),
    "AWS::RDS::DBSubnetGroup": ("aws_db_subnet_group", {}),
}
_SUB_PARAM = re.compile(r"\$\{([\w:]+)\}")


class _Loader(yaml.SafeLoader):
    pass


def _intrinsic(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> dict:
    name = "Ref" if suffix == "Ref" else f"Fn::{suffix}"
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
        if suffix == "GetAtt" and isinstance(value, str):
            value = value.split(".", 1)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {name: value}


_Loader.add_multi_constructor("!", lambda loader, suffix, node: _intrinsic(loader, suffix, node))


def load(text: str) -> dict | None:
    stripped = text.strip()
    try:
        data = json.loads(stripped) if stripped.startswith("{") else yaml.load(stripped, Loader=_Loader)
    except (ValueError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def is_template(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    res = data.get("Resources")
    typed = isinstance(res, dict) and any(isinstance(v, dict) and str(v.get("Type", "")).startswith("AWS::")
                                          for v in res.values())
    return "AWSTemplateFormatVersion" in data or typed


def _resolve(value: Any, params: dict) -> Any:
    if isinstance(value, list):
        return [_resolve(v, params) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"Ref"} and value["Ref"] in params:
        return params[value["Ref"]]
    if set(value) == {"Fn::Sub"} and isinstance(value["Fn::Sub"], str):
        out = _SUB_PARAM.sub(lambda m: str(params.get(m.group(1), m.group(0))), value["Fn::Sub"])
        return out if "${" not in out else "${" + rs.dump(value) + "}"
    if len(value) == 1 and (next(iter(value)) == "Ref" or next(iter(value)).startswith("Fn::")):
        return "${" + rs.dump(value) + "}"
    return {k: _resolve(v, params) for k, v in value.items()}


def parse(data: dict) -> tuple[list[dict], list[str]]:
    """(resources, notes)."""
    params = {k: v["Default"] for k, v in (data.get("Parameters") or {}).items()
              if isinstance(v, dict) and "Default" in v}
    out, mentions, notes = [], {}, []
    for logical, body in (data.get("Resources") or {}).items():
        if not isinstance(body, dict):
            notes.append(f"Resource `{logical}` is not a mapping; skipped.")
            continue
        source_type = str(body.get("Type") or "")
        props = body.get("Properties") if isinstance(body.get("Properties"), dict) else {}
        mentions[logical] = rs.dump(props)
        out.append(_resource(logical, source_type, _resolve(props, params)))
    return rs.link(out, mentions, lambda r: r["address"]), notes


def _resource(logical: str, source_type: str, props: dict) -> dict:
    rtype, names = TYPES.get(source_type, (source_type, {}))
    attrs = {attr: props[prop] for prop, attr in names.items() if prop in props}
    throughput = props.get("ProvisionedThroughput")
    if isinstance(throughput, dict):
        attrs["read_capacity"] = throughput.get("ReadCapacityUnits")
        attrs["write_capacity"] = throughput.get("WriteCapacityUnits")
    if rtype == "aws_dynamodb_table" and "billing_mode" not in attrs:
        attrs["billing_mode"] = "PROVISIONED"
    raw_tags = props.get("Tags")
    tags, known = {}, raw_tags is None or isinstance(raw_tags, (list, dict))
    if isinstance(raw_tags, list):
        tags = {str(t.get("Key")): str(t.get("Value")) for t in raw_tags if isinstance(t, dict) and "Key" in t}
    elif isinstance(raw_tags, dict):
        tags = {str(k): str(v) for k, v in raw_tags.items()}
    return rs.make(logical, rtype, source_type, attrs, tags, known)
