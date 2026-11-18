# homelab-flux

Flux GitOps for the two-node homelab k3s cluster (Wyse 5070 server + PC Engines
APU2 agent; NixOS hosts from `jvishnefske/nixos-x86-squashfs`).

```
clusters/homelab/         Flux entry point (flux-system + the Kustomizations below)
infrastructure/
  controllers/            cert-manager (HelmRelease), k3s Traefik tuning (HelmChartConfig)
  configs/                Let's Encrypt ClusterIssuer
apps/vishnefske/          vishnefske.com and its services
```

**This repo is public. Nothing secret or personal goes in it.** Hostnames and
the Let's Encrypt e-mail are substituted by Flux from the `cluster-vars`
Secret (`postBuild.substituteFrom`); credentials live in Secrets created out
of band (see `apps/vishnefske/SECRETS.md`).

## Images

Every app image is built with Nix in the private `vishnefske.com` repo and
preloaded on the nodes by NixOS (`services.k3s.images`), so manifests use
`imagePullPolicy: Never` and there is no registry. The tag is that repo's
git revision, set in one place: `apps/vishnefske/kustomization.yaml`
(`images:`). `deploy-site.sh` in the NixOS repo bumps it after the images
are on the nodes.

## Placement

The router forwards 80/443 to the APU2. Traefik runs on both nodes with
`externalTrafficPolicy: Local`, and the static site has a replica on each
node behind an `internalTrafficPolicy: Local` Service, so the site keeps
being served by the APU2 alone while the Wyse (control plane) is down.
Postgres, Keycloak, Gitea, Gerrit and NATS are pinned to the Wyse (RAM).
