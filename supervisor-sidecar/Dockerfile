FROM golang:1.22-alpine AS build

WORKDIR /src
COPY go.mod .
COPY main.go .

RUN go build -o supervisor-sidecar .

FROM alpine:3.20

WORKDIR /app

COPY --from=build /src/supervisor-sidecar /app/supervisor-sidecar

ENV FG_CORE_BASE_URL=http://core-api:8080 \
    SUPERVISOR_LISTEN_ADDR=:9090

EXPOSE 9090

CMD ["/app/supervisor-sidecar"]
