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
| `pomodoro` | vishnefske | `SESSION_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | pomodoro (Google keys optional until set) |

## One-time setup after the first start

- **Gitea → Keycloak login**: done by the playbook (source named `keycloak`
  to match the realm's redirect URI `/user/oauth2/keycloak/callback`).
- **Keycloak users**: the realm is imported without users; create them in the
  admin console. The first account to log in to Gerrit becomes its administrator.
