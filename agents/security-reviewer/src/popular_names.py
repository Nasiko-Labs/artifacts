"""Very popular package names per ecosystem, for the typosquat heuristic (a new name 1-2 edits away from one
of these, and not equal to any of them, is worth a second look)."""

PYPI = frozenset({
    "requests", "urllib3", "boto3", "botocore", "setuptools", "certifi", "idna", "charset-normalizer",
    "typing-extensions", "python-dateutil", "packaging", "six", "numpy", "pyyaml", "s3transfer", "cryptography",
    "pip", "wheel", "jmespath", "pandas", "attrs", "pydantic", "pyasn1", "rsa", "google-api-core", "protobuf",
    "cffi", "pycparser", "markupsafe", "jinja2", "click", "platformdirs", "pytz", "importlib-metadata", "zipp",
    "filelock", "colorama", "virtualenv", "tomli", "pyjwt", "flask", "django", "fastapi", "uvicorn", "starlette",
    "sqlalchemy", "psycopg2", "psycopg2-binary", "pymysql", "redis", "celery", "httpx", "aiohttp", "scipy",
    "matplotlib", "pillow", "scikit-learn", "tensorflow", "torch", "werkzeug", "gunicorn", "openai", "anthropic",
    "langchain", "beautifulsoup4", "lxml", "pytest", "coverage", "mock", "tqdm", "rich", "paramiko", "pyopenssl",
    "bcrypt", "passlib", "marshmallow", "selenium", "kafka-python", "pymongo", "elasticsearch", "grpcio", "docker",
})

NPM = frozenset({
    "react", "react-dom", "lodash", "express", "axios", "chalk", "commander", "debug", "moment", "uuid",
    "typescript", "webpack", "babel-core", "@babel/core", "eslint", "prettier", "jest", "mocha", "chai", "request",
    "body-parser", "cors", "dotenv", "jsonwebtoken", "bcrypt", "mongoose", "mysql", "mysql2", "pg", "sequelize",
    "redis", "socket.io", "next", "vue", "angular", "rxjs", "tslib", "yargs", "glob", "minimist", "semver",
    "fs-extra", "rimraf", "mkdirp", "async", "bluebird", "underscore", "jquery", "classnames", "prop-types",
    "node-fetch", "cross-env", "nodemon", "ws", "inquirer", "ora", "colors", "dayjs", "date-fns", "zod", "graphql",
    "apollo-server", "passport", "helmet", "morgan", "cookie-parser", "multer", "sharp", "electron", "vite",
    "rollup", "esbuild",
})

GEMS = frozenset({
    "rails", "rack", "rake", "bundler", "nokogiri", "json", "activesupport", "activerecord", "puma", "sinatra",
    "devise", "rspec", "thor", "i18n", "faraday", "httparty", "pg", "mysql2", "redis", "sidekiq", "jwt", "bcrypt",
    "rubocop",
})

CRATES = frozenset({
    "serde", "serde_json", "tokio", "rand", "regex", "clap", "log", "anyhow", "thiserror", "reqwest", "hyper",
    "futures", "chrono", "lazy_static", "once_cell", "syn", "quote", "proc-macro2", "libc", "bytes", "tracing",
    "axum", "actix-web", "openssl", "ring", "rustls", "uuid", "base64",
})

BY_ECOSYSTEM = {"pypi": PYPI, "npm": NPM, "gems": GEMS, "crates": CRATES}
