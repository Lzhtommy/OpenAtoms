FROM golang:1.23 AS build
WORKDIR /src
COPY apps/gateway ./
RUN CGO_ENABLED=0 go build -o /gateway .
FROM gcr.io/distroless/static
COPY --from=build /gateway /gateway
EXPOSE 8080
ENTRYPOINT ["/gateway"]
