kubeconfig := env_var_or_default("KUBECONFIG", env_var("HOME") + "/.kube/homelab.yaml")
kubectl := "kubectl --kubeconfig " + kubeconfig + " -n vishnefske"

# Artifactory (hub.<SITE_HOST>) admin password, generated in-cluster.
artifactory-password:
    @{{kubectl}} get secret artifactory -o jsonpath='{.data.admin-password}' | base64 -d; echo

# Rerun the Job that creates the Artifactory Secret (if missing) and database.
artifactory-db:
    {{kubectl}} delete job artifactory-secrets --ignore-not-found
    flux --kubeconfig {{kubeconfig}} reconcile kustomization apps --with-source
