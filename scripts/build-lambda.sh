#!/usr/bin/env bash
# scripts/build-lambda.sh - produce the Lambda deployment artefact inside the AWS Lambda build
# image, so the jar that ships is compiled in a Lambda-parity environment (W5 D4).
#
# Why this exists rather than `sam build --use-container` doing it:
#
#   template.yaml keeps the deliverable's literal `CodeUri: target/taxcalc-taxpayer-lookup-1.0.0.jar`.
#   SAM treats CodeUri as a DIRECTORY for every build workflow, so that path cannot drive one -
#   the Gradle/Maven workflows report "build file not found: .../taxcalc-taxpayer-lookup-1.0.0.jar/
#   build.gradle", and a makefile BuildMethod fails earlier still with "[Errno 20] Not a directory".
#   Both were tried. The function is therefore marked `Metadata: { SkipBuild: true }`, which makes
#   `sam build` stage the jar rather than compile it.
#
#   That leaves `--use-container` with nothing to containerise, so the container build happens
#   HERE instead. The artefact SAM stages is genuinely built inside
#   public.ecr.aws/sam/build-java21 - the same image `sam build --use-container` would have used.
#
# Java bytecode is architecture-independent, so building in the x86_64 image for an arm64 function
# is correct: --use-container exists to correct for NATIVE extensions, which a shaded jar has none
# of. That is also why this is fast (~3s) rather than an emulated arm64 build.
set -euo pipefail

IMAGE="${LAMBDA_BUILD_IMAGE:-public.ecr.aws/sam/build-java21:latest}"
M2_REPO="${M2_REPO:-${HOME}/.m2/repository}"
# Behind a TLS-intercepting proxy the container cannot reach Maven Central; set MAVEN_EXTRA_ARGS=-o
# to build purely from the mounted repository. An array, not a string: an unquoted string relies on
# word splitting, which shellcheck flags (SC2086) and which breaks the moment an argument contains
# a space.
_extra_raw="${MAVEN_EXTRA_ARGS:-}"
MAVEN_EXTRA_ARGS=()
if [ -n "${_extra_raw}" ]; then
  read -r -a MAVEN_EXTRA_ARGS <<< "${_extra_raw}"
fi

mkdir -p "${M2_REPO}"

echo "==> building the deployment artefact in ${IMAGE}"
# --user: without it the container writes target/ as root and every subsequent host-side build
#   fails on permissions.
# -Dmaven.repo.local: the repository is mounted at a fixed path rather than relying on the
#   container's HOME, which --user changes out from under Maven.
docker run --rm \
  -v "${PWD}":/src -w /src \
  -v "${M2_REPO}":/m2repo \
  --user "$(id -u):$(id -g)" \
  "${IMAGE}" \
  mvn -B -ntp -Dmaven.repo.local=/m2repo ${MAVEN_EXTRA_ARGS[@]+"${MAVEN_EXTRA_ARGS[@]}"} clean package -DskipTests

ARTEFACT="target/taxcalc-taxpayer-lookup-1.0.0.jar"
if [ ! -f "${ARTEFACT}" ]; then
  echo "ERROR: ${ARTEFACT} was not produced."
  exit 1
fi
echo "==> built in-container: ${ARTEFACT} ($(wc -c < "${ARTEFACT}") bytes)"
