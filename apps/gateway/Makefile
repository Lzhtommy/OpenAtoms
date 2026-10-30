.PHONY: build bridge test
bridge:
	cd ../.. && pnpm --filter @oa/bridge build
build:
	go build -o bin/gateway .
test:
	go test ./...
