.PHONY: up down logs bootstrap ps smoke

up:
	docker compose up -d --build

bootstrap:
	./scripts/bootstrap_local_aws.sh

down:
	docker compose down -v

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

smoke:
	./scripts/smoke_test.sh
