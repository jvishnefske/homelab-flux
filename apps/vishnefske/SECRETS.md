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
| `updates-zenoh` | vishnefske | `config.json5`, `dictionary`, `credentials`, `ROOT_KEY` (secrets.env `MW_UPDATES_ROOT_KEY`) | updates-zenohd, mw-updates |

## The updates channel (`updates-zenoh`)

Files in the secrets directory (see `updates.yaml`):

```
# One password per Zenoh user: the server, each publisher, each updater host.
pw() { head -c 24 /dev/urandom | base64 | tr -d '/+='; }
{ echo "mw-updates:$(pw)"; echo "release:$(pw)"; echo "pi-a:$(pw)"; } > updates-dictionary
grep '^mw-updates:' updates-dictionary > updates-server.cred
mw-updates router-config --server mw-updates --publishers release \
  --updaters pi-a --dictionary /etc/zenoh/auth/dictionary > updates-zenohd.json5
```

and in `secrets.env`, `MW_UPDATES_ROOT_KEY=<hex>` from
`mwctl key pub --pkcs11 mw-release` (the root's public key; the private half
stays in the TPM). Give each updater host its own line of the dictionary as
its credentials file, and the publisher (`mw-release`) its own. Adding a
user means regenerating both `updates-dictionary` and
`updates-zenohd.json5` (each updater has its own status rule).

## One-time setup after the first start

- **Gitea → Keycloak login**: done by the playbook (source named `keycloak`
  to match the realm's redirect URI `/user/oauth2/keycloak/callback`).
- **Keycloak users**: the realm is imported without users; create them in the
  admin console. The first account to log in to Gerrit becomes its administrator.
