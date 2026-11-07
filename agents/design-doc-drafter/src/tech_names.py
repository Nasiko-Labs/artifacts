"""Pure data: specific technologies the draft may only name when the user named them or a decision proposes
them, each with the generic noun used instead. Matched case-sensitively as whole words; ambiguous everyday
words (Go, Spark as a verb) are left out on purpose."""

_QUEUE = "a message queue"
_RDB = "a relational database"
_DOC = "a document database"
_KV = "a key-value store"
_CACHE = "a cache"
_OBJ = "object storage"
_SEARCH = "a search index"
_STREAM = "a stream processor"
_ORCH = "a container platform"
_CLOUD = "the cloud provider"
_FN = "a serverless function"
_RPC = "an RPC protocol"
_API = "an API style"
_IDP = "an identity provider"
_OBS = "an observability tool"
_WH = "a data warehouse"
_LB = "a load balancer"
_SECRET = "a secrets manager"
_CI = "a CI system"
_FLAG = "a feature-flag service"
_LLM = "a language model"
_VEC = "a vector store"
_FW = "a web framework"
_WF = "a workflow engine"

TECH: dict[str, str] = {
    **dict.fromkeys(["Kafka", "RabbitMQ", "SQS", "SNS", "Pub/Sub", "NATS", "ActiveMQ", "Kinesis", "EventBridge",
                     "Service Bus", "Event Hubs", "Pulsar", "ZeroMQ", "Celery", "Sidekiq"], _QUEUE),
    **dict.fromkeys(["Postgres", "PostgreSQL", "MySQL", "MariaDB", "SQL Server", "Oracle", "Aurora", "SQLite",
                     "CockroachDB", "Spanner", "Cloud SQL", "RDS"], _RDB),
    **dict.fromkeys(["MongoDB", "DynamoDB", "Cosmos DB", "Firestore", "Couchbase", "CouchDB"], _DOC),
    **dict.fromkeys(["Cassandra", "ScyllaDB", "Bigtable", "etcd", "Consul"], _KV),
    **dict.fromkeys(["Redis", "Memcached", "Valkey", "ElastiCache", "Varnish"], _CACHE),
    **dict.fromkeys(["S3", "GCS", "Azure Blob", "MinIO", "Cloud Storage"], _OBJ),
    **dict.fromkeys(["Elasticsearch", "OpenSearch", "Solr", "Algolia", "Meilisearch", "Typesense"], _SEARCH),
    **dict.fromkeys(["Flink", "Apache Spark", "Beam", "Dataflow", "Kafka Streams"], _STREAM),
    **dict.fromkeys(["Kubernetes", "K8s", "EKS", "GKE", "AKS", "ECS", "Fargate", "Docker", "Nomad", "OpenShift",
                     "Cloud Run", "Heroku"], _ORCH),
    **dict.fromkeys(["AWS", "GCP", "Azure", "Google Cloud", "DigitalOcean", "Cloudflare"], _CLOUD),
    **dict.fromkeys(["Lambda", "Cloud Functions", "Azure Functions"], _FN),
    **dict.fromkeys(["gRPC", "Thrift", "Protobuf", "Avro"], _RPC),
    **dict.fromkeys(["GraphQL", "WebSocket", "WebSockets", "SOAP"], _API),
    **dict.fromkeys(["Okta", "Auth0", "Keycloak", "Cognito", "Entra ID", "Azure AD"], _IDP),
    **dict.fromkeys(["Datadog", "Prometheus", "Grafana", "New Relic", "Splunk", "Sentry", "Honeycomb", "Jaeger",
                     "OpenTelemetry", "Loki", "PagerDuty", "CloudWatch"], _OBS),
    **dict.fromkeys(["Snowflake", "BigQuery", "Redshift", "Databricks", "ClickHouse"], _WH),
    **dict.fromkeys(["Nginx", "NGINX", "HAProxy", "Envoy", "Istio", "Traefik", "ALB"], _LB),
    **dict.fromkeys(["Vault", "Secrets Manager", "Key Vault", "KMS"], _SECRET),
    **dict.fromkeys(["Jenkins", "GitHub Actions", "CircleCI", "GitLab CI", "Argo CD", "ArgoCD", "Terraform"], _CI),
    **dict.fromkeys(["LaunchDarkly", "Unleash", "Flagsmith"], _FLAG),
    **dict.fromkeys(["GPT-4", "GPT-4o", "Claude", "Gemini", "Llama", "OpenAI", "Anthropic", "Mistral"], _LLM),
    **dict.fromkeys(["Pinecone", "Weaviate", "Qdrant", "Milvus", "pgvector", "Chroma", "FAISS"], _VEC),
    **dict.fromkeys(["Django", "Flask", "FastAPI", "Spring Boot", "Rails", "Next.js", "React",
                     "LangGraph", "LangChain"], _FW),
    **dict.fromkeys(["Temporal", "Airflow", "Step Functions", "Prefect", "Dagster"], _WF),
    **dict.fromkeys(["Stripe", "Adyen", "Braintree", "PayPal", "Twilio", "SendGrid", "Mailgun"],
                    "the external provider"),
}
