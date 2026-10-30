.PHONY: build build-linux test
build:
	go build -o bin/sandboxd .
build-linux:
	GOOS=linux GOARCH=$${GOARCH:-$$(go env GOARCH)} CGO_ENABLED=0 go build -o bin/sandboxd-linux .
test:
	go test ./...
