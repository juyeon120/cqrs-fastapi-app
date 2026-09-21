/*
 * CQRS order system - CI pipeline (Jenkins declarative)
 *
 * Repo findings: one FastAPI module (main.py) that hosts the Command API (POST /orders -> MariaDB -> Kafka),
 * the Query API (GET /, GET /orders -> MongoDB) and the Kafka projector (kafka_consumer_worker, started in
 * lifespan). One Dockerfile => one image today. Components are detected from Dockerfiles so a later split
 * into command/query/projector images is picked up without editing this file:
 *     Dockerfile            -> <IMAGE_BASENAME>
 *     Dockerfile.<x>        -> <IMAGE_BASENAME>-<x>
 *     <dir>/Dockerfile      -> <IMAGE_BASENAME>-<dir>
 *
 * Flow: validate -> detect -> unit tests -> build -> integration tests -> push -> bump tag in manifest repo.
 * CD is pull-based: ArgoCD reconciles the manifest repo, Jenkins never touches the cluster.
 *
 * Agent requirements: Docker CLI + daemon access, git, bash, GNU sed/find/xargs; `kustomize` only if the
 *   manifest repo uses kustomization.yaml.
 * Plugins: Credentials Binding, SSH Agent (only for git@/ssh:// manifest URLs), Timestamps, JUnit.
 * Credentials: REGISTRY_CREDENTIALS_ID = username/password; MANIFEST_CREDENTIALS_ID = SSH private key
 *   (ssh URL) or username/token (https URL).
 */

pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 60, unit: 'MINUTES')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
    }

    parameters {
        string(name: 'REGISTRY_URL', defaultValue: 'docker.io', description: 'Docker registry host (no scheme), e.g. docker.io or registry.example.com:5000')
        string(name: 'IMAGE_NAMESPACE', defaultValue: 'juyeon13241', description: 'Registry namespace / project that holds the images')
        string(name: 'IMAGE_BASENAME', defaultValue: 'fastapi-cqrs', description: 'Base image name; detected components append their suffix')
        string(name: 'REGISTRY_CREDENTIALS_ID', defaultValue: 'docker-registry-credentials', description: 'Jenkins credentials ID (username/password) for the registry')
        string(name: 'MANIFEST_REPO_URL', defaultValue: '', description: 'Manifest Git repo URL, e.g. git@git.example.com:team/k8s-manifests.git (required)')
        string(name: 'MANIFEST_CREDENTIALS_ID', defaultValue: 'manifest-repo-credentials', description: 'Jenkins credentials ID: SSH key for ssh URLs, username/token for https URLs')
        string(name: 'MANIFEST_BRANCH', defaultValue: 'main', description: 'Manifest repo branch that ArgoCD tracks')
        string(name: 'MANIFEST_DIR', defaultValue: '.', description: 'Directory inside the manifest repo holding the deployment YAMLs / kustomization.yaml')
        string(name: 'CI_BOT_NAME', defaultValue: 'jenkins-ci-bot', description: 'Git author/committer name for manifest commits')
        string(name: 'CI_BOT_EMAIL', defaultValue: 'jenkins-ci-bot@users.noreply.local', description: 'Git author/committer email for manifest commits')
        string(name: 'DEPLOY_BRANCH_REGEX', defaultValue: '^(origin/)?(main|master)$', description: 'Only source branches matching this push images and update the manifest repo')
        booleanParam(name: 'REQUIRE_TESTS', defaultValue: false, description: 'Fail when a pytest suite collects no tests (no tests exist in the repo yet)')
    }

    environment {
        PYTHON_IMAGE  = 'python:3.11-slim'
        MARIADB_IMAGE = 'mariadb:11.4'
        MONGO_IMAGE   = 'mongo:7.0'
        KAFKA_IMAGE   = 'apache/kafka:3.8.0'
        COMPONENTS_FILE = "${WORKSPACE}/.ci/components.txt"
        GIT_TERMINAL_PROMPT = '0'
    }

    stages {
        stage('Validate & Prepare') {
            steps {
                script {
                    def problems = []
                    def url = (params.MANIFEST_REPO_URL ?: '').trim()
                    if (!url) { problems << 'MANIFEST_REPO_URL is required' }
                    if (!(url ==~ /^(git@|ssh:\/\/|https:\/\/)\S+$/)) { problems << 'MANIFEST_REPO_URL must be an ssh (git@ / ssh://) or https URL' }
                    if (!(params.IMAGE_NAMESPACE ==~ /^[a-z0-9][a-z0-9._\/-]*$/)) { problems << 'IMAGE_NAMESPACE is invalid' }
                    if (!(params.IMAGE_BASENAME ==~ /^[a-z0-9][a-z0-9._-]*$/)) { problems << 'IMAGE_BASENAME is invalid' }
                    if (!(params.MANIFEST_BRANCH ==~ /^[A-Za-z0-9][A-Za-z0-9._\/-]*$/)) { problems << 'MANIFEST_BRANCH is invalid' }
                    if (params.MANIFEST_DIR.startsWith('/') || params.MANIFEST_DIR.contains('..')) { problems << 'MANIFEST_DIR must be a relative path without ..' }
                    if (!params.REGISTRY_CREDENTIALS_ID?.trim() || !params.MANIFEST_CREDENTIALS_ID?.trim()) { problems << 'Both credentials IDs are required' }

                    def registryHost = (params.REGISTRY_URL ?: '').trim().replaceAll(/^https?:\/\//, '').replaceAll(/\/+$/, '')
                    if (!(registryHost ==~ /^[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]+)?(\/[a-z0-9._-]+)*$/)) { problems << 'REGISTRY_URL is invalid' }

                    def sha = env.GIT_COMMIT ?: sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
                    if (!(sha ==~ /^[0-9a-f]{40}$/)) { problems << "Unexpected GIT_COMMIT '${sha}'" }

                    if (problems) { error('Invalid pipeline configuration: ' + problems.join('; ')) }

                    env.GIT_COMMIT    = sha
                    env.IMAGE_TAG     = sha.substring(0, 8)   // == GIT_COMMIT[0..7]
                    env.REGISTRY_HOST = registryHost
                    env.CI_ID         = 'ci-' + "${env.JOB_NAME}".toLowerCase().replaceAll(/[^a-z0-9]+/, '-').take(30) + "-${env.BUILD_NUMBER}"
                    currentBuild.displayName = "#${env.BUILD_NUMBER} ${env.IMAGE_TAG}"
                }

                // Helper files live in .ci/ (workspace only, never committed).
                writeFile file: '.ci/pytest_in_container.sh', text: '''#!/usr/bin/env bash
set -Eeuo pipefail
python -m venv /tmp/venv
. /tmp/venv/bin/activate
pip install --quiet -r requirements.txt pytest pytest-asyncio httpx
find . -name '*.py' -not -path './.ci/*' -not -path './.git/*' -print0 | xargs -0 -r python -c 'import ast, sys; [ast.parse(open(f, encoding="utf-8").read(), f) for f in sys.argv[1:]]'
mkdir -p reports
rc=0
pytest -m "$PYTEST_EXPR" -ra --junitxml="reports/$REPORT_NAME.xml" || rc=$?
exit $rc
'''
                writeFile file: '.ci/e2e_smoke.py', text: '''import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read() or b"null")


# Command side: write to MariaDB and publish the event to Kafka.
status, order = call("POST", "/orders", {"customer": "ci", "item": "smoke", "quantity": 1, "amount": 9.5})
if status != 201:
    sys.exit("POST /orders returned %s" % status)

# Query side: the projector must copy the event into MongoDB.
deadline = time.time() + 90
while time.time() < deadline:
    try:
        status, doc = call("GET", "/orders?id=" + order["id"])
        if status == 200 and doc.get("id") == order["id"]:
            print("CQRS round trip OK: order %s visible in read model" % order["id"])
            sys.exit(0)
    except urllib.error.HTTPError as err:
        if err.code != 404:
            raise
    time.sleep(2)
sys.exit("order %s never reached the read model" % order["id"])
'''
            }
        }

        stage('Detect Components') {
            steps {
                sh bash('''
mkdir -p .ci
out="$COMPONENTS_FILE"
: > "$out"
mapfile -t dockerfiles < <(git ls-files | grep -E '(^|/)Dockerfile([.][A-Za-z0-9_-]+)?$' | grep -Ev '(^|/)(tests?|examples?|node_modules|vendor)/' || true)
if [[ ${#dockerfiles[@]} -eq 0 ]]; then
  echo "No Dockerfile found - nothing to build" >&2
  exit 1
fi
for df in "${dockerfiles[@]}"; do
  dir="$(dirname "$df")"
  file="$(basename "$df")"
  if [[ "$file" == Dockerfile ]]; then
    if [[ "$dir" == . ]]; then suffix=""; else suffix="$(basename "$dir")"; fi
  else
    suffix="${file#Dockerfile.}"
  fi
  name="$(printf '%s' "${IMAGE_BASENAME}${suffix:+-$suffix}" | tr 'A-Z_' 'a-z-')"
  echo "${name}|${dir}|${df}" >> "$out"
done
dupes="$(cut -d'|' -f1 "$out" | sort | uniq -d)"
if [[ -n "$dupes" ]]; then
  echo "Duplicate component names: $dupes" >&2
  exit 1
fi
echo "Detected components (name|context|dockerfile):"
cat "$out"
''')
            }
        }

        stage('Unit Tests') {
            steps {
                sh bash('''
rc=0
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PYTHONDONTWRITEBYTECODE=1 \\
  -e PYTEST_EXPR="not integration" -e REPORT_NAME=unit -v "$PWD:/src" -w /src "$PYTHON_IMAGE" bash .ci/pytest_in_container.sh || rc=$?
if [[ $rc -eq 5 ]]; then
  if [[ "$REQUIRE_TESTS" == true ]]; then
    echo "No unit tests collected and REQUIRE_TESTS=true" >&2
    exit 1
  fi
  echo "WARNING: no unit tests collected (only a syntax check ran)"
  rc=0
fi
exit $rc
''')
            }
        }

        stage('Build Images') {
            steps {
                script {
                    for (c in components()) {
                        withEnv(["C_NAME=${c.name}", "C_CONTEXT=${c.context}", "C_DOCKERFILE=${c.dockerfile}"]) {
                            sh bash('''
image="${REGISTRY_HOST}/${IMAGE_NAMESPACE}/${C_NAME}:${IMAGE_TAG}"
echo "Building $image"
docker build --pull --file "$C_DOCKERFILE" --tag "$image" --label "org.opencontainers.image.revision=$GIT_COMMIT" --label "org.opencontainers.image.version=$IMAGE_TAG" --label "ci.build.url=$BUILD_URL" "$C_CONTEXT"
''')
                        }
                    }
                }
            }
        }

        stage('Integration Tests') {
            options { timeout(time: 20, unit: 'MINUTES') }
            steps {
                // Throw-away MariaDB + MongoDB + Kafka on a private per-build network (no host ports, no
                // clash with concurrent builds). Each freshly built image is started against them, and a
                // command -> Kafka -> projector -> query round trip is verified. pytest -m integration
                // suites, if present, run against the same backing services.
                sh bash('''
net="$CI_ID"
label="ci.id=$CI_ID"

purge() {
  docker ps -aq --filter "label=$label" | xargs -r docker rm -f -v >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
}
cleanup() {
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    for c in $(docker ps -aq --filter "label=$label"); do
      echo "----- logs: $(docker inspect -f '{{.Name}}' "$c") -----"
      docker logs --tail 80 "$c" 2>&1 || true
    done
  fi
  purge
}
trap cleanup EXIT
purge

wait_for() {
  local desc="$1" tries="$2"
  shift 2
  for ((i = 1; i <= tries; i++)); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 3
  done
  echo "Timed out waiting for $desc" >&2
  return 1
}

start() {
  local name="$1" image="$2"
  shift 2
  docker run -d --name "${CI_ID}-${name}" --network "$net" --network-alias "$name" --label "$label" "$@" "$image" >/dev/null
}

docker network create --label "$label" "$net" >/dev/null

kafka_env=(
  -e KAFKA_NODE_ID=1
  -e KAFKA_PROCESS_ROLES=broker,controller
  -e KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka:9092
  -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
  -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@kafka:9093
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1
  -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1
  -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1
  -e KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS=0
  -e KAFKA_NUM_PARTITIONS=1
)
start mariadb "$MARIADB_IMAGE" -e MARIADB_ROOT_PASSWORD=ci-only-root -e MARIADB_DATABASE=orders_db -e MARIADB_USER=user -e MARIADB_PASSWORD=password
start mongo "$MONGO_IMAGE"
start kafka "$KAFKA_IMAGE" "${kafka_env[@]}"

wait_for mariadb 40 docker exec "${CI_ID}-mariadb" healthcheck.sh --connect --innodb_initialized
wait_for mongo 40 docker exec "${CI_ID}-mongo" mongosh --quiet --eval 'db.runCommand({ping: 1}).ok'
wait_for kafka 40 docker exec "${CI_ID}-kafka" /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list

svc_env=(
  -e MARIADB_HOST=mariadb -e MARIADB_PORT=3306 -e MARIADB_USER=user -e MARIADB_PASSWORD=password -e MARIADB_DB=orders_db
  -e MONGO_URI=mongodb://mongo:27017 -e MONGO_DB=read_db
  -e KAFKA_BOOTSTRAP_SERVERS=kafka:9092 -e KAFKA_TOPIC=order-events
)

# pytest integration suites (exit code 5 = nothing collected)
rc=0
docker run --rm --user "$(id -u):$(id -g)" --network "$net" -e HOME=/tmp -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PYTHONDONTWRITEBYTECODE=1 "${svc_env[@]}" \\
  -e PYTEST_EXPR=integration -e REPORT_NAME=integration -v "$PWD:/src" -w /src "$PYTHON_IMAGE" bash .ci/pytest_in_container.sh || rc=$?
if [[ $rc -eq 5 ]]; then
  if [[ "$REQUIRE_TESTS" == true ]]; then
    echo "No integration tests collected and REQUIRE_TESTS=true" >&2
    exit 1
  fi
  echo "WARNING: no pytest integration tests collected"
  rc=0
fi
[[ $rc -eq 0 ]] || exit $rc

# End-to-end smoke test of every built image
for name in $(cut -d'|' -f1 "$COMPONENTS_FILE"); do
  image="${REGISTRY_HOST}/${IMAGE_NAMESPACE}/${name}:${IMAGE_TAG}"
  svc="${CI_ID}-svc"
  echo "Smoke testing $image"
  docker rm -f -v "$svc" >/dev/null 2>&1 || true
  docker run -d --name "$svc" --network "$net" --label "$label" "${svc_env[@]}" "$image" >/dev/null
  wait_for "$name /healthz" 40 docker exec "$svc" curl -fsS http://127.0.0.1:8080/healthz
  docker exec -i "$svc" python - < .ci/e2e_smoke.py
  docker rm -f -v "$svc" >/dev/null
done
''')
            }
        }

        stage('Push Images') {
            when { expression { isDeployBranch() } }
            steps {
                script {
                    // Registry credentials live in a per-build docker config that is always removed.
                    withEnv(["DOCKER_CONFIG=${env.WORKSPACE}/.docker"]) {
                        try {
                            withCredentials([usernamePassword(credentialsId: params.REGISTRY_CREDENTIALS_ID, usernameVariable: 'REG_USER', passwordVariable: 'REG_PASS')]) {
                                sh bash('printf \'%s\' "$REG_PASS" | docker login "$REGISTRY_HOST" --username "$REG_USER" --password-stdin\n')
                            }
                            for (c in components()) {
                                withEnv(["C_NAME=${c.name}"]) {
                                    retry(3) {
                                        sh bash('docker push "${REGISTRY_HOST}/${IMAGE_NAMESPACE}/${C_NAME}:${IMAGE_TAG}"\n')
                                    }
                                }
                            }
                        } finally {
                            sh bash('docker logout "$REGISTRY_HOST" >/dev/null 2>&1 || true\nrm -rf "$DOCKER_CONFIG"\n')
                        }
                    }
                }
            }
        }

        stage('Update Manifest Repo') {
            when { expression { isDeployBranch() } }
            steps {
                script {
                    withManifestAuth {
                        sh bash('''
umask 077
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [[ "$MANIFEST_AUTH" == https ]]; then
  { echo '#!/usr/bin/env bash'; echo 'case "$1" in Username*) printf "%s" "$MANIFEST_GIT_USER" ;; *) printf "%s" "$MANIFEST_GIT_PASS" ;; esac'; } > "$WORK/askpass.sh"
  chmod 700 "$WORK/askpass.sh"
  export GIT_ASKPASS="$WORK/askpass.sh"
else
  export GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes'
fi

case "$REGISTRY_HOST" in
  docker.io|index.docker.io|registry-1.docker.io) is_hub=true ;;
  *) is_hub=false ;;
esac
q="'"
dq='"'

clone_fresh() {
  rm -rf "$WORK/repo"
  git clone --quiet --depth 1 --single-branch --branch "$MANIFEST_BRANCH" "$MANIFEST_REPO_URL" "$WORK/repo"
  git -C "$WORK/repo" config user.name "$CI_BOT_NAME"
  git -C "$WORK/repo" config user.email "$CI_BOT_EMAIL"
  git -C "$WORK/repo" config commit.gpgsign false
}

# Sets the tag for every detected component; safe to re-run on a fresh clone.
apply_updates() {
  local dir="$WORK/repo/$MANIFEST_DIR" name repo_re reg_re pat new_ref
  [[ -d "$dir" ]] || { echo "MANIFEST_DIR '$MANIFEST_DIR' not found in manifest repo" >&2; return 1; }
  reg_re="${REGISTRY_HOST//./[.]}"
  while IFS='|' read -r name _ _; do
    repo_re="${IMAGE_NAMESPACE//./[.]}/${name//./[.]}"
    if [[ -f "$dir/kustomization.yaml" || -f "$dir/kustomization.yml" ]]; then
      command -v kustomize >/dev/null || { echo "kustomize is required on the agent for kustomization.yaml" >&2; return 1; }
      if [[ "$is_hub" == true ]]; then new_ref="${IMAGE_NAMESPACE}/${name}"; else new_ref="${REGISTRY_HOST}/${IMAGE_NAMESPACE}/${name}"; fi
      (cd "$dir" && kustomize edit set image "${IMAGE_NAMESPACE}/${name}=${new_ref}:${IMAGE_TAG}")
      grep -q -- "$IMAGE_TAG" "$dir"/kustomization.y*ml || { echo "kustomize did not set tag for $name" >&2; return 1; }
    else
      # Keeps whatever registry prefix and quoting the manifest already uses.
      pat="(image:[[:space:]]*[${dq}${q}]?)((${reg_re}/)?${repo_re}):[^[:space:]${dq}${q}]+"
      find "$dir" -path '*/.git' -prune -o -type f '(' -name '*.yaml' -o -name '*.yml' ')' ! -name 'kustomization.y*ml' ! -name 'Chart.y*ml' -print0 \\
        | xargs -0 -r sed -E -i "s|${pat}|\\\\1\\\\2:${IMAGE_TAG}|g"
      grep -rEq --include='*.yaml' --include='*.yml' -e "${repo_re}:${IMAGE_TAG}" "$dir" \\
        || { echo "No image reference for ${IMAGE_NAMESPACE}/${name} found under '$MANIFEST_DIR' in the manifest repo" >&2; return 1; }
    fi
  done < "$COMPONENTS_FILE"
}

names="$(cut -d'|' -f1 "$COMPONENTS_FILE" | paste -sd, -)"
pushed=false
for attempt in 1 2 3 4 5; do
  clone_fresh
  apply_updates
  if [[ -z "$(git -C "$WORK/repo" status --porcelain)" ]]; then
    echo "Manifest repo already references tag ${IMAGE_TAG} for ${names}; nothing to commit."
    exit 0
  fi
  git -C "$WORK/repo" add -A
  git -C "$WORK/repo" commit --quiet -m "ci: bump ${names} image tag to ${IMAGE_TAG} [skip ci]" -m "Source commit: ${GIT_COMMIT}" -m "Build: ${BUILD_URL}"
  if git -C "$WORK/repo" push --quiet origin "HEAD:refs/heads/${MANIFEST_BRANCH}"; then
    pushed=true
    break
  fi
  echo "Push rejected (attempt ${attempt}/5) - re-cloning and re-applying on the latest branch head"
  sleep $((attempt * 3))
done
if [[ "$pushed" != true ]]; then
  echo "Failed to push manifest update after 5 attempts" >&2
  exit 1
fi
echo "Manifest repo updated: ${names} -> ${IMAGE_TAG}"
''')
                    }
                }
            }
        }
    }

    post {
        always {
            junit allowEmptyResults: true, testResults: 'reports/*.xml'
            archiveArtifacts artifacts: '.ci/components.txt', allowEmptyArchive: true
            script {
                // Safety net if the integration stage was aborted before its own trap could run.
                if (env.CI_ID) {
                    sh bash('''
docker ps -aq --filter "label=ci.id=$CI_ID" | xargs -r docker rm -f -v >/dev/null 2>&1 || true
docker network rm "$CI_ID" >/dev/null 2>&1 || true
if [[ -f "$COMPONENTS_FILE" && -n "${IMAGE_TAG:-}" ]]; then
  for name in $(cut -d'|' -f1 "$COMPONENTS_FILE"); do
    docker rmi "${REGISTRY_HOST}/${IMAGE_NAMESPACE}/${name}:${IMAGE_TAG}" >/dev/null 2>&1 || true
  done
fi
''')
                }
            }
            deleteDir()
        }
        failure {
            echo "Pipeline failed for ${env.GIT_COMMIT ?: 'unknown commit'} - no manifest change is pushed unless every previous stage passed."
        }
    }
}

// ---- helpers ----------------------------------------------------------------------------------------

// Wraps a shell body in a strict-mode bash script. The shebang stops Jenkins adding `set -x`, so
// secrets are not echoed (Credentials Binding masks them in the log as well).
String bash(String body) {
    return '#!/usr/bin/env bash\nset -Eeuo pipefail\n' + body
}

// Parses .ci/components.txt (name|context|dockerfile) written by the Detect Components stage.
List components() {
    def raw = readFile(env.COMPONENTS_FILE).trim()
    if (!raw) { error('No components detected') }
    return raw.split('\n').collect { String line ->
        def p = line.split('[|]')
        [name: p[0], context: p[1], dockerfile: p[2]]
    }
}

boolean isDeployBranch() {
    if (env.CHANGE_ID) { return false }   // never publish pull-request builds
    def branch = env.BRANCH_NAME ?: env.GIT_BRANCH ?: ''
    return branch ==~ params.DEPLOY_BRANCH_REGEX
}

// SSH key for ssh URLs, username/token via GIT_ASKPASS for https URLs; secrets stay masked in both cases.
void withManifestAuth(Closure body) {
    if (params.MANIFEST_REPO_URL.trim() ==~ /^(git@|ssh:\/\/).*/) {
        sshagent(credentials: [params.MANIFEST_CREDENTIALS_ID]) {
            withEnv(['MANIFEST_AUTH=ssh']) { body() }
        }
    } else {
        withCredentials([usernamePassword(credentialsId: params.MANIFEST_CREDENTIALS_ID, usernameVariable: 'MANIFEST_GIT_USER', passwordVariable: 'MANIFEST_GIT_PASS')]) {
            withEnv(['MANIFEST_AUTH=https']) { body() }
        }
    }
}
