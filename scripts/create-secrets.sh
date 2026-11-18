#!/usr/bin/env bash
# Create/refresh every Secret the manifests reference, from a private
# directory that is never committed:
#   secrets.env            KEY=value lines (see the keys used below)
#   nats-server.conf       NATS config (has the cluster route password)
#   vishnefske-realm.json  Keycloak realm to import (optional, no users)
# Usage: scripts/create-secrets.sh ~/backup/vishnefske-server/k8s-secrets
# Re-running replaces the Secrets (kubectl apply); restart the affected pods.
set -euo pipefail
dir=${1:?usage: $0 <secrets-dir>}
set -a; . "$dir/secrets.env"; set +a
ns=vishnefske
apply() { kubectl apply -f - >/dev/null && echo "secret $*"; }
gen() { kubectl -n "$1" create secret generic "$2" --dry-run=client -o yaml "${@:3}"; }

for n in flux-system $ns; do kubectl get ns $n >/dev/null 2>&1 || kubectl create ns $n; done

# Flux postBuild substitution (hostnames + e-mails stay out of the public repo).
gen flux-system cluster-vars \
  --from-literal=SITE_HOST="$SITE_HOST" \
  --from-literal=LETSENCRYPT_EMAIL="$LETSENCRYPT_EMAIL" \
  --from-literal=POMODORO_OWNER_EMAIL="$POMODORO_OWNER_EMAIL" | apply flux-system/cluster-vars

gen $ns db-credentials \
  --from-literal=POSTGRES_USER="$POSTGRES_USER" \
  --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  --from-literal=KEYCLOAK_DB_NAME="$KEYCLOAK_DB_NAME" \
  --from-literal=KEYCLOAK_DB_USER="$KEYCLOAK_DB_USER" \
  --from-literal=KEYCLOAK_DB_PASSWORD="$KEYCLOAK_DB_PASSWORD" \
  --from-literal=GITEA_DB_NAME="$GITEA_DB_NAME" \
  --from-literal=GITEA_DB_USER="$GITEA_DB_USER" \
  --from-literal=GITEA_DB_PASSWORD="$GITEA_DB_PASSWORD" \
  --from-literal=GERRIT_DB_NAME="$GERRIT_DB_NAME" \
  --from-literal=GERRIT_DB_USER="$GERRIT_DB_USER" \
  --from-literal=GERRIT_DB_PASSWORD="$GERRIT_DB_PASSWORD" | apply $ns/db-credentials

gen $ns keycloak \
  --from-literal=KC_BOOTSTRAP_ADMIN_USERNAME="$KC_BOOTSTRAP_ADMIN_USERNAME" \
  --from-literal=KC_BOOTSTRAP_ADMIN_PASSWORD="$KC_BOOTSTRAP_ADMIN_PASSWORD" | apply $ns/keycloak
if [ -f "$dir/vishnefske-realm.json" ]; then
  gen $ns keycloak-realm --from-file=vishnefske-realm.json="$dir/vishnefske-realm.json" | apply $ns/keycloak-realm
fi

gen $ns gitea \
  --from-literal=GITEA__security__SECRET_KEY="$GITEA_SECRET_KEY" \
  --from-literal=GITEA__security__INTERNAL_TOKEN="$GITEA_INTERNAL_TOKEN" \
  --from-literal=GITEA__oauth2__JWT_SECRET="$GITEA_JWT_SECRET" | apply $ns/gitea

gen $ns gerrit --from-literal=OAUTH_CLIENT_SECRET="$OIDC_GERRIT_SECRET" | apply $ns/gerrit

gen $ns nats-config --from-file=nats-server.conf="$dir/nats-server.conf" | apply $ns/nats-config

pomodoro=(--from-literal=SESSION_SECRET="$POMODORO_SESSION_SECRET")
[ -z "${GOOGLE_CLIENT_ID:-}" ] || pomodoro+=(--from-literal=GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID")
[ -z "${GOOGLE_CLIENT_SECRET:-}" ] || pomodoro+=(--from-literal=GOOGLE_CLIENT_SECRET="$GOOGLE_CLIENT_SECRET")
gen $ns pomodoro "${pomodoro[@]}" | apply $ns/pomodoro
