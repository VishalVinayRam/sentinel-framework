# Sentinel Observability Stack

Install the full stack with:

```bash
# Prometheus + Grafana + Alertmanager
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install sentinel-monitoring prometheus-community/kube-prometheus-stack \
  -f observability/prometheus/values.yaml \
  --namespace monitoring --create-namespace

# Jaeger (distributed tracing)
helm repo add jaegertracing https://jaegertracing.github.io/helm-charts
helm install sentinel-tracing jaegertracing/jaeger \
  -f observability/jaeger/values.yaml \
  --namespace monitoring

# Import Grafana dashboard
kubectl create configmap sentinel-grafana-dashboards \
  --from-file=observability/grafana/dashboards/ \
  -n monitoring
```

## What's Monitored

| Metric | Dashboard Panel |
|---|---|
| `sentinel_incidents_total` | Incident timeline |
| `sentinel_incidents_open_total` | Open incidents by severity |
| `sentinel_alerts_false_positive_total` | False positive rate |
| `sentinel_incident_detection_duration_seconds` | MTTD |
| `sentinel_pr_reviews_total` | PR review rate |
| `sentinel_pr_reviews_blocked_total` | PRs blocked by severity |
| `sentinel_llm_request_duration_seconds` | LLM inference latency p50/p99 |
