# CQRS Application & CI Context

## Architecture
- Pattern: CQRS (Command/Query Responsibility Segregation)
- Write DB: MariaDB
- Read DB: MongoDB
- Event Broker: Apache Kafka
- Orchestration: Bare-metal/On-Premise Kubernetes

## CI/CD Pipeline Standards
- CI: Jenkins Pipeline (Jenkinsfile)
- CD: GitOps via ArgoCD (Pull-based reconciliation)
- Manifest Repo: Separated Git repository for Kubernetes manifests
- Target: Automated build, test, multi-module Docker image build, push to registry, and git-commit image tag to Manifest repo.
