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

## Verified Production State
- Application Image: `juyeon13241/fastapi-cqrs:<GIT_SHORT_SHA>` (immutable 8-char commit tag; currently `8266700f`, never `:latest`)
- Target Namespace: `k8s-assign`
- GitOps Repository: `cqrs-k8s-manifests`, managed by a single ArgoCD Application: `cqrs-fastapi-pipeline`
- Build Constraints: host daemon builder enforced (`DOCKER_BUILDKIT=0`) until the native buildx plugin is migrated; `.dockerignore` protection rules are active
