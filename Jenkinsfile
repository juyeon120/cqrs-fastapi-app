pipeline {
    agent any

    environment {
        // [중요] 도커 허브와 깃허브 계정 분리
        DOCKERHUB_USER   = 'juyeon13241'
        GITHUB_USER      = 'juyeon120'
        IMAGE_NAME       = 'cqrs-fastapi-app'
        
        // Phase 3에서 등록한 Credentials ID
        DOCKER_CREDS_ID  = 'dockerhub-credentials'
        GIT_CREDS_ID     = 'github-credentials'
    }

    stages {
        stage('1. Code Checkout') {
            steps {
                checkout scm
            }
        }

        stage('2. Build Docker Image') {
            steps {
                sh """
                    echo "Building Docker Image version: ${BUILD_NUMBER} for user: ${DOCKERHUB_USER}"
                    docker build -t ${DOCKERHUB_USER}/${IMAGE_NAME}:${BUILD_NUMBER} .
                    docker tag ${DOCKERHUB_USER}/${IMAGE_NAME}:${BUILD_NUMBER} ${DOCKERHUB_USER}/${IMAGE_NAME}:latest
                """
            }
        }

        stage('3. Push to Docker Hub') {
            steps {
                withCredentials([usernamePassword(credentialsId: "${DOCKER_CREDS_ID}", usernameVariable: 'DH_USER', passwordVariable: 'DH_PASS')]) {
                    sh """
                        echo "${DH_PASS}" | docker login -u "${DH_USER}" --password-stdin
                        docker push ${DOCKERHUB_USER}/${IMAGE_NAME}:${BUILD_NUMBER}
                        docker push ${DOCKERHUB_USER}/${IMAGE_NAME}:latest
                        docker logout
                    """
                }
            }
        }

        stage('4. Update Manifest Git Repo') {
            steps {
                withCredentials([usernamePassword(credentialsId: "${GIT_CREDS_ID}", usernameVariable: 'GIT_USER', passwordVariable: 'GIT_PASS')]) {
                    sh """
                        rm -rf temp_manifest
                        
                        # GitHub juyeon120 계정의 매니페스트 저장소 clone
                        git clone https://${GIT_USER}:${GIT_PASS}@github.com/${GITHUB_USER}/cqrs-k8s-manifests.git temp_manifest
                        cd temp_manifest

                        # Git 작성자 정보
                        git config user.name "jenkins-bot"
                        git config user.email "jenkins@fastapi-cqrs.local"

                        # 05-fastapi-app.yaml 내 도커 이미지 주소 치환 (juyeon13241/cqrs-fastapi-app:태그)
                        sed -i -E "s|image: .*/${IMAGE_NAME}:.*|image: ${DOCKERHUB_USER}/${IMAGE_NAME}:${BUILD_NUMBER}|g" 05-fastapi-app.yaml

                        # 변경 사항 커밋 및 푸시
                        git add 05-fastapi-app.yaml
                        if git diff-index --quiet HEAD --; then
                            echo "No changes to commit."
                        else
                            git commit -m "ci: update image tag to ${BUILD_NUMBER} [skip ci]"
                            git push origin main
                        fi

                        cd ..
                        rm -rf temp_manifest
                    """
                }
            }
        }
    }

    post {
        always {
            sh "docker rmi ${DOCKERHUB_USER}/${IMAGE_NAME}:${BUILD_NUMBER} ${DOCKERHUB_USER}/${IMAGE_NAME}:latest || true"
        }
        success {
            echo "Pipeline succeeded! New image: ${DOCKERHUB_USER}/${IMAGE_NAME}:${BUILD_NUMBER}"
        }
        failure {
            echo "Pipeline failed."
        }
    }
}
