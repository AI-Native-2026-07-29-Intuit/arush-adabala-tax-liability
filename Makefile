# Makefile - the four-command surface every engineer uses daily.
# Targets are designed to be idempotent and friction-free.

.PHONY: up down logs ps smoke clean nuke dev test e2e build-TaxpayerLookupFunction

up: ## Bring the core stack to healthy.
	docker compose up -d --wait --wait-timeout 90
	docker compose ps

down: ## Stop containers; keep named volumes (DB state preserved).
	docker compose down --remove-orphans

logs: ## Tail logs for every service.
	docker compose logs --follow --tail=200

ps: ## Show service + health state.
	docker compose ps

smoke: ## Run the team smoke script.
	./scripts/smoke.sh

dev: ## Live-reload profile (requires `./gradlew classes --continuous`; see scripts/dev.md).
	docker compose --profile dev up -d --wait --wait-timeout 90 taxcalc-api-dev

test: ## CI integration profile (boots stack + seed-fixtures + smoke).
	docker compose -f compose.yaml -f compose.profiles.yaml --profile test \
		up -d --wait --wait-timeout 120
	./scripts/smoke.sh

e2e: ## End-to-end profile (adds W4 React frontend + otelcol + jaeger).
	docker compose -f compose.yaml -f compose.profiles.yaml --profile e2e \
		up -d --wait --wait-timeout 150

clean: ## Stop containers; remove anonymous volumes; keep named volumes + images.
	docker compose down --remove-orphans

nuke: ## DANGEROUS: stop containers AND wipe named volumes + locally-built images.
	docker compose down --volumes --remove-orphans --rmi local

# --- W5 D4: `sam build` custom build method -----------------------------------------------------
# Invoked by SAM, not by hand: template.yaml marks TaxpayerLookupFunction with
# `Metadata: { BuildMethod: makefile }`, which makes SAM run `make build-<LogicalId>` with
# ARTIFACTS_DIR set to the directory the deployment zip is assembled from.
#
# Why this rather than letting SAM pick a workflow: SAM chooses its Java workflow by looking for a
# build file in the CodeUri directory, and it checks build.gradle BEFORE pom.xml. In this repo the
# root holds both, so `sam build` silently selected JavaGradleWorkflow and tried to build the whole
# Spring Boot application - failing on the app's own (Lambda-irrelevant) dependency graph. A
# makefile build method takes that guess out of SAM's hands.
#
# The shaded jar goes into lib/, not the artifact root: the Lambda Java runtime puts
# /var/task/lib/*.jar on the classpath, but a jar sitting loose at the root of the zip is never
# added to it - the function would deploy cleanly and then fail at ClassNotFoundException.
build-TaxpayerLookupFunction:
	mvn -B -ntp clean package -DskipTests
	mkdir -p "$(ARTIFACTS_DIR)/lib"
	cp target/taxcalc-taxpayer-lookup-1.0.0.jar "$(ARTIFACTS_DIR)/lib/"
