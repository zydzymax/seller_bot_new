# Multi-stage Dockerfile 4;O AI-?@>402F0 A 0C48> ?>445@6:>9
# © SoVAni 2025

# Stage 1: Go API builder
FROM golang:1.21-alpine AS go-builder

WORKDIR /app/go-api

# #AB0=02;8205< 7028A8<>AB8
RUN apk add --no-cache git ca-certificates

# >?8@C5< go.mod 8 go.sum
COPY go-api/go.mod go-api/go.sum ./
RUN go mod download

# >?8@C5< 8AE>4=K9 :>4 Go API
COPY go-api/ ./

# !>18@05< Go ?@8;>65=85
RUN CGO_ENABLED=0 GOOS=linux go build -a -installsuffix cgo -o main .

# Stage 2: Python environment
FROM python:3.11-slim AS python-base

# #AB0=02;8205< A8AB5<=K5 7028A8<>AB8 4;O 0C48> >1@01>B:8
RUN apt-get update && apt-get install -y \
    # 1I85 7028A8<>AB8
    gcc \
    g++ \
    make \
    pkg-config \
    curl \
    wget \
    # C48> 181;8>B5:8
    libsndfile1-dev \
    libsndfile1 \
    ffmpeg \
    # G8AB:0 :5H0
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# >?8@C5< requirements.txt 8 CAB0=02;8205< Python 7028A8<>AB8
COPY python-core/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 3: Production image
FROM python:3.11-slim AS production

# #AB0=02;8205< runtime 7028A8<>AB8
RUN apt-get update && apt-get install -y \
    # C48> runtime 181;8>B5:8
    libsndfile1 \
    ffmpeg \
    # #B8;8BK
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# >?8@C5< Python 7028A8<>AB8 87 python-base stage
COPY --from=python-base /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-base /usr/local/bin /usr/local/bin

# >?8@C5< Go 18=0@L
COPY --from=go-builder /app/go-api/main /app/go-api-server

# >?8@C5< Python :>4
COPY python-core/ ./python-core/

# !>7405< 48@5:B>@88 4;O 0C48> >1@01>B:8
RUN mkdir -p /tmp/ai_seller_audio && \
    chmod 755 /tmp/ai_seller_audio

# !>7405< ?>;L7>20B5;O 4;O 157>?0A=>AB8
RUN groupadd -r aibot && useradd -r -g aibot aibot
RUN chown -R aibot:aibot /app /tmp/ai_seller_audio

# B:@K205< ?>@BK
EXPOSE 8080 8081 8082

# 5@5<5==K5 >:@C65=8O
ENV PYTHONPATH=/app/python-core
ENV PYTHONUNBUFFERED=1
ENV AUDIO_TEMP_DIR=/tmp/ai_seller_audio

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8082/health || exit 1

# 5@5:;NG05<AO =0 ?>;L7>20B5;O aibot
USER aibot

# Entrypoint script
COPY deployments/entrypoint.sh /entrypoint.sh
USER root
RUN chmod +x /entrypoint.sh
USER aibot

ENTRYPOINT ["/entrypoint.sh"]