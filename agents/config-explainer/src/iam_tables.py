"""Hand-maintained tables for the IAM checks ("my checks"): short on purpose, cited in the reply, reviewed
quarterly. Action names are compared case-insensitively."""

ESCALATION = (
    "iam:PassRole", "iam:CreateAccessKey", "iam:CreateLoginProfile", "iam:UpdateLoginProfile",
    "iam:AttachUserPolicy", "iam:AttachRolePolicy", "iam:AttachGroupPolicy", "iam:PutUserPolicy",
    "iam:PutRolePolicy", "iam:PutGroupPolicy", "iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion",
    "iam:UpdateAssumeRolePolicy", "iam:AddUserToGroup", "lambda:UpdateFunctionCode",
    "lambda:CreateFunction", "glue:UpdateDevEndpoint", "cloudformation:CreateStack",
)

DATA_READS = ("secretsmanager:GetSecretValue", "kms:Decrypt", "ssm:GetParameter", "ssm:GetParameters",
              "ssm:GetParametersByPath", "s3:GetObject", "dynamodb:Scan", "dynamodb:GetItem", "dynamodb:Query")

# Actions that support resource-level scoping, so `Resource: "*"` grants more than needed.
RESOURCE_SCOPED = (
    "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketPolicy",
    "s3:PutBucketPolicy", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
    "dynamodb:Query", "dynamodb:Scan", "sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage",
    "sns:Publish", "sns:Subscribe", "kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey",
    "logs:PutLogEvents", "logs:CreateLogStream", "lambda:InvokeFunction", "lambda:UpdateFunctionCode",
    "secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue", "ssm:GetParameter",
    "ssm:GetParameters", "ssm:PutParameter", "ecr:BatchGetImage", "ecr:PutImage", "kinesis:PutRecord",
    "kinesis:GetRecords", "states:StartExecution", "events:PutEvents", "iam:PassRole",
    "iam:CreateAccessKey", "iam:AttachRolePolicy", "sts:AssumeRole", "ec2:TerminateInstances",
)

SERVICE_PREFIXES = frozenset({
    "access-analyzer", "account", "acm", "acm-pca", "airflow", "amplify", "apigateway", "app-integrations",
    "appconfig", "appflow", "application-autoscaling", "appmesh", "apprunner", "appstream", "appsync", "athena",
    "autoscaling", "backup", "batch", "bedrock", "budgets", "ce", "chatbot", "cloud9", "cloudformation",
    "cloudfront", "cloudhsm", "cloudshell", "cloudtrail", "cloudwatch", "codeartifact", "codebuild", "codecommit",
    "codedeploy", "codeguru", "codepipeline", "codestar", "codestar-connections", "cognito-identity",
    "cognito-idp", "comprehend", "config", "connect", "cur", "databrew", "datasync", "dax", "detective",
    "devicefarm", "directconnect", "dlm", "dms", "docdb-elastic", "ds", "dynamodb", "ebs", "ec2", "ec2messages",
    "ecr", "ecr-public", "ecs", "eks", "elasticache", "elasticbeanstalk", "elasticfilesystem",
    "elasticloadbalancing", "elasticmapreduce", "elastictranscoder", "emr-containers", "emr-serverless", "es",
    "events", "evidently", "execute-api", "firehose", "fis", "fms", "forecast", "frauddetector", "fsx", "gamelift",
    "glacier", "globalaccelerator", "glue", "grafana", "greengrass", "guardduty", "health", "iam", "identitystore",
    "imagebuilder", "inspector", "inspector2", "iot", "iotevents", "iotsitewise", "kafka", "kafka-cluster",
    "kendra", "kinesis", "kinesisanalytics", "kinesisvideo", "kms", "lakeformation", "lambda", "lex",
    "license-manager", "lightsail", "logs", "macie2", "mediaconvert", "medialive", "mediapackage", "mediastore",
    "memorydb", "mgn", "mobiletargeting", "mq", "network-firewall", "networkmanager", "opensearch",
    "organizations", "outposts", "personalize", "pi", "pipes", "polly", "pricing", "qldb", "quicksight", "ram",
    "rds", "rds-data", "rds-db", "redshift", "redshift-data", "redshift-serverless", "rekognition",
    "resource-explorer-2", "resource-groups", "robomaker", "route53", "route53domains", "route53resolver", "rum",
    "s3", "s3-object-lambda", "sagemaker", "savingsplans", "scheduler", "schemas", "sdb", "secretsmanager",
    "securityhub", "serverlessrepo", "servicecatalog", "servicediscovery", "servicequotas", "ses", "shield",
    "signer", "sms", "sns", "sqs", "ssm", "ssm-incidents", "ssmmessages", "sso", "sso-oauth", "states",
    "storagegateway", "sts", "support", "swf", "synthetics", "tag", "textract", "timestream", "transcribe",
    "transfer", "translate", "trustedadvisor", "waf", "waf-regional", "wafv2", "wellarchitected", "workdocs",
    "worklink", "workmail", "workspaces", "xray",
})
