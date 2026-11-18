# Secrets (created out of band, never committed)

`scripts/create-secrets.sh <dir>` creates all of them from a private
directory (`secrets.env`, `nats-server.conf`, optional `vishnefske-realm.json`).

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

After changing a Secret, restart what uses it, e.g.
`kubectl -n vishnefske rollout restart deploy/pomodoro`.

## One-time setup after the first start

- **Gitea → Keycloak login** (the redirect URI in the realm is
  `/user/oauth2/keycloak/callback`, so the source must be named `keycloak`):
  ```
  kubectl -n vishnefske exec deploy/gitea -- gitea admin auth add-oauth \
    --config /data/custom/conf/app.ini --name keycloak --provider openidConnect \
    --key gitea --secret "$OIDC_GITEA_SECRET" \
    --auto-discover-url https://auth.<SITE_HOST>/realms/vishnefske/.well-known/openid-configuration
  ```
- **Keycloak users**: the realm is imported without users; create them in the
  admin console. The first account to log in to Gerrit becomes its administrator.
