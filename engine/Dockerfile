# Build stage — WORKDIR mirrors local layout so include_str! paths resolve identically
FROM rust:1.86-alpine AS builder
RUN apk add --no-cache musl-dev pkgconfig openssl-dev openssl-libs-static
WORKDIR /build/engine
COPY engine/Cargo.toml ./
# Generate lockfile then pre-cache dependencies (layer cache)
RUN cargo generate-lockfile \
    && mkdir -p src/bin && echo "fn main(){}" > src/main.rs \
    && echo "fn main(){}" > src/bin/backtest.rs \
    && echo "" > src/lib.rs \
    && cargo build --release 2>/dev/null || true \
    && rm -rf src
COPY engine/src ./src
COPY data/volume_groups.json /build/data/volume_groups.json
COPY data/recommended-watchlist.json /build/data/recommended-watchlist.json
COPY data/margin-stocks.json /build/data/margin-stocks.json
COPY data/liquid-5l-symbols.json /build/data/liquid-5l-symbols.json
RUN touch src/main.rs src/lib.rs src/bin/backtest.rs \
    && cargo build --release --bin engine --bin backtest

# Final stage
FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata curl
WORKDIR /app
COPY --from=builder /build/engine/target/release/engine .
COPY --from=builder /build/engine/target/release/backtest .
COPY data/volume_groups.json ./data/volume_groups.json
COPY data/recommended-watchlist.json ./data/recommended-watchlist.json
COPY data/margin-stocks.json ./data/margin-stocks.json
COPY data/liquid-5l-symbols.json ./data/liquid-5l-symbols.json

ENV TZ=Asia/Kolkata
EXPOSE 8080
CMD ["./engine"]
