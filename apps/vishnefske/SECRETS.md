# Secrets (created out of band, never committed)

`ansible/cluster.yml` creates all of them from a private directory
(`secrets.env`, `nats-server.conf`, optional `vishnefske-realm.json`; default
`~/backup/vishnefske-server/k8s-secrets`, or set `SECRETS_DIR`) and is
idempotent, so it doubles as a drift check:

```
cd ansible
ansible-playbook cluster.yml --check --diff   # report what differs, change nothing
ansible-playbook cluster.yml                  # converge
```

It targets `~/.kube/homelab.yaml` unless `KUBECONFIG` is set, rolls the
Deployments that use a Secret it changed (`-e restart=false` to skip), checks
that the edge-ca Secrets (`pubsub-tls`, `pubsub-users`) exist, and registers
Gitea's Keycloak login source if it's missing. Values never reach the log
unless `-e show_secrets=true`. Needs `kubernetes.core` (ships with the
`ansible` package) and the `kubernetes` Python library
(`sudo apt install python3-kubernetes`).

| Secret | Namespace | Keys | Used by |
|---|---|---|---|
| `cluster-vars` | flux-system | `SITE_HOST`, `LETSENCRYPT_EMAIL`, `POMODORO_OWNER_EMAIL` | Flux `postBuild.substituteFrom` |
| `db-credentials` | vishnefske | `POSTGRES_USER/PASSWORD`, `{KEYCLOAK,GITEA,GERRIT}_DB_{NAME,USER,PASSWORD}` | postgres, keycloak, gitea |
| `keycloak` | vishnefske | `KC_BOOTSTRAP_ADMIN_USERNAME/PASSWORD` | keycloak (temporary admin on first start) |
| `keycloak-realm` | vishnefske | `vishnefske-realm.json` | keycloak, imported if the realm is missing |
| `gitea` | vishnefske | `GITEA__security__SECRET_KEY`, `GITEA__security__INTERNAL_TOKEN`, `GITEA__oauth2__JWT_SECRET` | gitea |
| `gerrit` | vishnefske | `OAUTH_CLIENT_SECRET` (Keycloak client `gerrit`) | gerrit |
| `nats-config` | vishnefske | `nats-server.conf` | nats |
| `pomodoro` | vishnefske | `SESSION_SECRET`, `OIDC_CLIENT_SECRET` (Keycloak client `pomodoro`) | pomodoro |
| `admin` | vishnefske | `SESSION_SECRET`, `OIDC_CLIENT_SECRET` (Keycloak client `admin`), `SIGNER_TOKEN`, `METRICS_TOKEN` | admin |
| `grafana` | vishnefske | `GF_SECURITY_ADMIN_PASSWORD` (break-glass), `GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET` (Keycloak client `grafana`) | grafana |
| `prometheus-scrape` | vishnefske | `token` (same value as admin's `METRICS_TOKEN`) | prometheus |

## One-time setup after the first start

- **Gitea → Keycloak login**: done by the playbook (source named `keycloak`
  to match the realm's redirect URI `/user/oauth2/keycloak/callback`).
- **Admin and Grafana → Keycloak login**: create confidential clients in the
  `vishnefske` realm. `admin` redirects to `https://admin.<SITE_HOST>/auth/callback`
  and `grafana` to `https://grafana.<SITE_HOST>/login/generic_oauth`. Their
  secrets go in `secrets.env` as `OIDC_ADMIN_SECRET` and `OIDC_GRAFANA_SECRET`.
  The other secrets are `ADMIN_SESSION_SECRET`, `ADMIN_SIGNER_TOKEN`,
  `ADMIN_METRICS_TOKEN`, `GRAFANA_ADMIN_PASSWORD`. Generate the random ones with
  `openssl rand -hex 32`. The Wyse signer needs `ADMIN_SIGNER_TOKEN` too (see
  `vishnefske.com/admin/README.md`).
- **Pomodoro → Keycloak login**: create a confidential client `pomodoro` in the
  `vishnefske` realm with redirect URI `https://timer.<SITE_HOST>/auth/callback`.
  Put its secret in `secrets.env` as `OIDC_POMODORO_SECRET`. The owner user
  (`POMODORO_OWNER_EMAIL`) needs a verified email address in Keycloak.
- **Keycloak users**: the realm is imported without users; create them in the
  admin console. The first account to log in to Gerrit becomes its administrator.
