FROM rust:1-alpine AS builder
RUN apk add --no-cache musl-dev build-base cmake perl git
WORKDIR /app
COPY . .
RUN cargo build --release --locked

FROM alpine:latest AS certs
RUN apk --no-cache add ca-certificates

FROM scratch
COPY --from=certs /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=builder /app/target/release/formatter-rust /agent
EXPOSE 8000
ENTRYPOINT ["/agent"]
