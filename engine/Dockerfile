FROM rust:1.86-alpine AS builder
RUN apk add --no-cache musl-dev pkgconfig openssl-dev openssl-libs-static
WORKDIR /build/engine
COPY engine/Cargo.toml ./
RUN cargo generate-lockfile \
 && mkdir -p src \
 && echo "fn main(){}" > src/main.rs \
 && cargo build --release 2>/dev/null || true \
 && rm -rf src
COPY engine/src ./src
RUN touch src/main.rs \
 && cargo build --release --bin engine

FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata curl python3 py3-numpy py3-pandas py3-pyarrow py3-matplotlib py3-tabulate
WORKDIR /app
COPY --from=builder /build/engine/target/release/engine .
COPY data/volume_groups.json ./data/volume_groups.json

ENV TZ=Asia/Kolkata
EXPOSE 8080
CMD ["./engine"]
