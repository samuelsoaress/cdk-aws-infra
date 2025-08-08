#!/bin/bash

# Script para fazer upload das configurações Docker para S3
# Uso: ./upload-configs.sh

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Obter bucket name do stack output
get_bucket_name() {
    aws cloudformation describe-stacks \
        --stack-name InfrastructureStack \
        --query "Stacks[0].Outputs[?OutputKey=='ConfigBucketName'].OutputValue" \
        --output text 2>/dev/null || echo ""
}

# Criar diretórios temporários para configs
create_config_dirs() {
    mkdir -p configs/fastapi configs/gateway
    log_info "Diretórios de configuração criados"
}

# Criar docker-compose.yml padrão para FastAPI
create_fastapi_config() {
    cat > configs/fastapi/docker-compose.yml << 'EOF'
version: '3.8'

services:
  fastapi:
    image: python:3.11-slim
    ports:
      - "8000:8000"
    restart: always
    environment:
      - PYTHONUNBUFFERED=1
    volumes:
      - /opt/data:/app/data
    working_dir: /app
    command: >
      sh -c "
        pip install fastapi uvicorn[standard] &&
        cat > main.py << 'PYEOF'
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

app = FastAPI(
    title='FastAPI Service',
    description='FastAPI service running in Docker',
    version='1.0.0'
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

@app.get('/health')
async def health_check():
    return {'status': 'ok', 'service': 'fastapi'}

@app.get('/')
async def root():
    return {'message': 'FastAPI is running', 'docs': '/docs'}

# Endpoint para compatibilidade com ALB path routing
@app.get('/swagger/api/docs')
async def swagger_alb_redirect():
    return RedirectResponse(url='/docs')

# Endpoint de health para ALB routing
@app.get('/swagger/api/health')
async def health_check_alb():
    return {'status': 'ok', 'service': 'fastapi'}

# /docs endpoint é automaticamente criado pelo FastAPI
PYEOF
        uvicorn main:app --host 0.0.0.0 --port 8000 --reload
      "
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
EOF

    cat > configs/fastapi/.env << 'EOF'
# FastAPI Environment Variables
ENV=production
DEBUG=false
LOG_LEVEL=info
EOF

    log_info "Configuração FastAPI criada"
}

# Criar docker-compose.yml padrão para Gateway
create_gateway_config() {
    cat > configs/gateway/docker-compose.yml << 'EOF'
version: '3.8'

services:
  gateway:
    image: node:18-alpine
    ports:
      - "3000:3000"
    restart: always
    environment:
      - NODE_ENV=production
    volumes:
      - /opt/data:/app/data
    working_dir: /app
    command: >
      sh -c "
        npm install express cors helmet swagger-jsdoc swagger-ui-express &&
        cat > server.js << 'JSEOF'
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const swaggerJsdoc = require('swagger-jsdoc');
const swaggerUi = require('swagger-ui-express');

const app = express();
const PORT = 3000;

// Swagger configuration
const swaggerOptions = {
  definition: {
    openapi: '3.0.0',
    info: {
      title: 'Gateway API',
      version: '1.0.0',
      description: 'Gateway service API documentation',
    },
    servers: [
      {
        url: 'http://localhost:3000',
        description: 'Development server',
      },
    ],
  },
  apis: ['./server.js'], // paths to files containing OpenAPI definitions
};

const swaggerSpec = swaggerJsdoc(swaggerOptions);

// Middleware
app.use(helmet());
app.use(cors());
app.use(express.json());

// Swagger UI em /api-docs (endpoint real que você usa)
app.use('/api-docs', swaggerUi.serve, swaggerUi.setup(swaggerSpec));

// Swagger UI também em /swagger/gw/api-docs para ALB routing
app.use('/swagger/gw/api-docs', swaggerUi.serve, swaggerUi.setup(swaggerSpec));

/**
 * @swagger
 * /health:
 *   get:
 *     summary: Health check endpoint
 *     responses:
 *       200:
 *         description: Service is healthy
 */
app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'gateway', timestamp: new Date().toISOString() });
});

// Health endpoint também em /swagger/gw/health para ALB routing
app.get('/swagger/gw/health', (req, res) => {
  res.json({ status: 'ok', service: 'gateway', timestamp: new Date().toISOString() });
});

/**
 * @swagger
 * /:
 *   get:
 *     summary: Root endpoint
 *     responses:
 *       200:
 *         description: Gateway service information
 */
app.get('/', (req, res) => {
  res.json({ 
    message: 'Gateway is running',
    swagger: '/api-docs',
    health: '/health'
  });
});

// Start server
app.listen(PORT, '0.0.0.0', () => {
  console.log(\`Gateway server running on port \${PORT}\`);
  console.log(\`Swagger docs available at http://localhost:\${PORT}/api-docs\`);
});
JSEOF
        node server.js
      "
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:3000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
EOF

    cat > configs/gateway/.env << 'EOF'
# Gateway Environment Variables
NODE_ENV=production
PORT=3000
LOG_LEVEL=info
EOF

    log_info "Configuração Gateway criada"
}

# Upload configs para S3
upload_configs() {
    local bucket_name=$1
    
    if [[ -z "$bucket_name" ]]; then
        log_error "Nome do bucket não encontrado. Verifique se a stack foi deployada."
        return 1
    fi
    
    log_info "Fazendo upload das configurações para s3://$bucket_name"
    
    # Upload FastAPI configs
    aws s3 cp configs/fastapi/ s3://$bucket_name/fastapi/ --recursive
    log_info "✅ Configurações FastAPI enviadas"
    
    # Upload Gateway configs
    aws s3 cp configs/gateway/ s3://$bucket_name/gateway/ --recursive
    log_info "✅ Configurações Gateway enviadas"
    
    # List uploaded files
    log_info "Arquivos no bucket:"
    aws s3 ls s3://$bucket_name --recursive
}

# Cleanup
cleanup() {
    rm -rf configs/
    log_info "Diretórios temporários removidos"
}

# Main function
main() {
    log_info "=== Upload de Configurações Docker ==="
    
    # Verificar AWS CLI
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI não encontrado. Instale e configure o AWS CLI."
        exit 1
    fi
    
    # Obter nome do bucket
    bucket_name=$(get_bucket_name)
    
    if [[ -z "$bucket_name" ]]; then
        log_error "Não foi possível obter o nome do bucket. Verifique se a stack InfrastructureStack foi deployada."
        exit 1
    fi
    
    log_info "Bucket S3: $bucket_name"
    
    # Criar configurações
    create_config_dirs
    create_fastapi_config
    create_gateway_config
    
    # Upload para S3
    upload_configs "$bucket_name"
    
    # Cleanup
    cleanup
    
    log_info "🎉 Configurações enviadas com sucesso!"
    log_info ""
    log_info "Próximos passos:"
    log_info "1. Faça deploy da stack: cdk deploy InfrastructureStack"
    log_info "2. Para atualizar instâncias: ./instance-refresh.sh both"
    log_info "3. Acesse Swagger em: http://<ALB-DNS>/swagger/api/docs e /swagger/gw/docs"
}

main "$@"
