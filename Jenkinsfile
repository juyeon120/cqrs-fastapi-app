/*
 * CQRS order system - declarative CI/CD (Jenkins -> Docker Hub -> manifest repo -> ArgoCD)
 *
 * Flow: unit test (isolated container) -> docker build & push (host daemon, legacy builder) -> bump the image
 * tag in the manifest repo. CD is pull-based: ArgoCD reconciles the manifest repo, Jenkins never touches the cluster.
 *
 * Agent requirements: Docker CLI + daemon access, git, bash, GNU sed.
 * Plugins: Credentials Binding, GitHub (githubPush trigger), Timestamps.
 * Credentials (Manage Jenkins > Credentials):
 *   dockerhub-creds    Username with password  (Docker Hub user + access token)
 *   github-credentials Username with password  (GitHub user + Personal Access Token with repo write access)
 */

pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 30, unit: 'MINUTES')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    triggers {
        githubPush()
    }

    environment {
        IMAGE_REPO    = 'juyeon13241/fastapi-cqrs'
        IMAGE_TAG = "${GIT_COMMIT[0..7]}"
        MANIFEST_REPO = 'https://github.com/juyeon120/cqrs-k8s-manifests.git'
        MANIFEST_DIR  = 'cqrs-k8s-manifests'
        PYTHON_IMAGE  = 'python:3.11-slim'
        GIT_TERMINAL_PROMPT = '0'
    }

    stages {
        stage('Unit Test') {
            steps {
                // Isolated container: nothing is installed on the agent and no MariaDB/MongoDB/Kafka is needed
                // (the suite mocks all three).
                sh '''#!/usr/bin/env bash
set -Eeuo pipefail
docker run --rm --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD:/src" -w /src "$PYTHON_IMAGE" bash -c '
    set -Eeuo pipefail
    python -m venv /tmp/venv
    . /tmp/venv/bin/activate
    pip install --quiet -r requirements.txt pytest pytest-asyncio httpx
    pytest tests/ -v
  '
'''
            }
        }

        stage('Docker Build & Push') {
            when { expression { isDeployBranch() } }
            steps {
                // Registry login lives in a per-build docker config that is always removed.
                withEnv(["DOCKER_CONFIG=${env.WORKSPACE}/.docker"]) {
                    withCredentials([usernamePassword(credentialsId: 'dockerhub-credentials', usernameVariable: 'DOCKERHUB_USER', passwordVariable: 'DOCKERHUB_PASS')]) {
                        sh '''#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s' "$DOCKERHUB_PASS" | docker login --username "$DOCKERHUB_USER" --password-stdin
DOCKER_BUILDKIT=0 docker build -t "${IMAGE_REPO}:${IMAGE_TAG}" .
docker push "${IMAGE_REPO}:${IMAGE_TAG}"
'''
                    }
                }
            }
        }

        stage('Manifest GitOps Promotion') {
            when { expression { isDeployBranch() } }
            steps {
                withCredentials([usernamePassword(credentialsId: 'github-credentials', usernameVariable: 'GIT_USER', passwordVariable: 'GIT_PASS')]) {
                    sh '''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# The token is handed to git through GIT_ASKPASS, never embedded in the URL or printed.
ASKPASS="$(mktemp -p "$WORKSPACE")"
trap 'rm -f "$ASKPASS"' EXIT
cat > "$ASKPASS" <<'EOF'
#!/bin/sh
case "$1" in Username*) printf '%s' "$GIT_USER" ;; *) printf '%s' "$GIT_PASS" ;; esac
EOF
chmod 700 "$ASKPASS"
export GIT_ASKPASS="$ASKPASS"

MANIFEST_FILE="$MANIFEST_DIR/05-fastapi-app.yaml"

# Clone on first use, otherwise pull the latest main.
sync_manifests() {
  if [[ -d "$MANIFEST_DIR/.git" ]]; then
    git -C "$MANIFEST_DIR" fetch --quiet origin main
    git -C "$MANIFEST_DIR" reset --quiet --hard origin/main
  else
    rm -rf "$MANIFEST_DIR"
    git clone --quiet --branch main "$MANIFEST_REPO" "$MANIFEST_DIR"
  fi
}

# Deterministic in-place tag replacement; fails if the result is not exactly the wanted image line.
promote() {
  sed -i "s|image: ${IMAGE_REPO}:.*|image: ${IMAGE_REPO}:${IMAGE_TAG}|" "$MANIFEST_FILE"
  grep -qx "[[:space:]]*image: ${IMAGE_REPO}:${IMAGE_TAG}" "$MANIFEST_FILE" \
    || { echo "image line for ${IMAGE_REPO} not found in $MANIFEST_FILE" >&2; return 1; }
}

for attempt in 1 2 3; do
  sync_manifests
  promote
  if git -C "$MANIFEST_DIR" diff --quiet; then
    echo "Manifest already pins ${IMAGE_REPO}:${IMAGE_TAG}; nothing to commit."
    exit 0
  fi
  git -C "$MANIFEST_DIR" -c user.name="jenkins-ci-bot" -c user.email="jenkins-ci-bot@users.noreply.local" \
    commit --quiet -am "ci(cd): promote fastapi-cqrs to ${IMAGE_TAG} [skip ci]"
  if git -C "$MANIFEST_DIR" push --quiet origin HEAD:main; then
    echo "Manifest repo updated: ${IMAGE_REPO}:${IMAGE_TAG}"
    exit 0
  fi
  echo "Push rejected (attempt ${attempt}/3) - re-syncing with origin/main and re-applying"
  sleep $((attempt * 3))
done
echo "Failed to push the manifest update after 3 attempts" >&2
exit 1
'''
                }
            }
        }
    }

    post {
        always {
            sh '''#!/usr/bin/env bash
DOCKER_CONFIG="$WORKSPACE/.docker" docker logout >/dev/null 2>&1 || true
docker rmi "${IMAGE_REPO}:${IMAGE_TAG}" >/dev/null 2>&1 || true
'''
            deleteDir()
        }
    }
}

// Only builds of the main branch publish an image and touch the manifest repo (never PRs or feature branches).
boolean isDeployBranch() {
    if (env.CHANGE_ID) { return false }
    def branch = env.BRANCH_NAME ?: env.GIT_BRANCH ?: ''
    return branch == 'main' || branch == 'origin/main'
}
