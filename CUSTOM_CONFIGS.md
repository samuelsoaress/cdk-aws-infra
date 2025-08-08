# Exemplos de Configuração Personalizada

Este arquivo contém exemplos de como personalizar as configurações Docker que são enviadas para o S3.

## Como Personalizar

1. Execute o script de upload padrão primeiro:
```bash
./upload-configs.sh
```

2. Baixe o bucket name:
```bash
BUCKET_NAME=$(aws cloudformation describe-stacks --stack-name InfrastructureStack --query "Stacks[0].Outputs[?OutputKey=='ConfigBucketName'].OutputValue" --output text)
echo "Bucket: $BUCKET_NAME"
```

3. Baixe as configurações atuais:
```bash
aws s3 cp s3://$BUCKET_NAME/fastapi/ ./custom-configs/fastapi/ --recursive
aws s3 cp s3://$BUCKET_NAME/gateway/ ./custom-configs/gateway/ --recursive
```

4. Edite os arquivos conforme necessário

5. Faça upload das configurações personalizadas:
```bash
aws s3 cp ./custom-configs/fastapi/ s3://$BUCKET_NAME/fastapi/ --recursive
aws s3 cp ./custom-configs/gateway/ s3://$BUCKET_NAME/gateway/ --recursive
```

6. Execute instance refresh para aplicar:
```bash
./instance-refresh.sh both
```

## Exemplos de Configurações

### FastAPI com PostgreSQL

```yaml
# custom-configs/fastapi/docker-compose.yml
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    environment:
      - POSTGRES_DB=fastapi_db
      - POSTGRES_USER=fastapi_user
      - POSTGRES_PASSWORD=your_password_here
    volumes:
      - /opt/data/postgres:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    restart: always

  fastapi:
    image: python:3.11-slim
    depends_on:
      - postgres
    ports:
      - "8000:8000"
    restart: always
    environment:
      - PYTHONUNBUFFERED=1
      - DATABASE_URL=postgresql://fastapi_user:your_password_here@postgres:5432/fastapi_db
    volumes:
      - /opt/data/app:/app/data
    working_dir: /app
    command: >
      sh -c "
        pip install fastapi uvicorn[standard] psycopg2-binary sqlalchemy &&
        cat > main.py << 'PYEOF'
from fastapi import FastAPI
from sqlalchemy import create_engine
import os

app = FastAPI(title='FastAPI with PostgreSQL')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://fastapi_user:your_password_here@postgres:5432/fastapi_db')
engine = create_engine(DATABASE_URL)

@app.get('/health')
async def health_check():
    try:
        with engine.connect() as conn:
            conn.execute('SELECT 1')
        return {'status': 'ok', 'database': 'connected'}
    except Exception as e:
        return {'status': 'error', 'database': 'disconnected', 'error': str(e)}

@app.get('/')
async def root():
    return {'message': 'FastAPI with PostgreSQL is running'}
PYEOF
        uvicorn main:app --host 0.0.0.0 --port 8000 --reload
      "
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

```env
# custom-configs/fastapi/.env
ENV=production
DEBUG=false
LOG_LEVEL=info
DATABASE_URL=postgresql://fastapi_user:your_password_here@postgres:5432/fastapi_db
POSTGRES_PASSWORD=your_password_here
```

### Gateway com Redis Cache

```yaml
# custom-configs/gateway/docker-compose.yml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - /opt/data/redis:/data
    restart: always
    command: redis-server --appendonly yes

  gateway:
    image: node:18-alpine
    depends_on:
      - redis
    ports:
      - "3000:3000"
    restart: always
    environment:
      - NODE_ENV=production
      - REDIS_URL=redis://redis:6379
    volumes:
      - /opt/data/gateway:/app/data
    working_dir: /app
    command: >
      sh -c "
        npm install express cors helmet redis &&
        cat > server.js << 'JSEOF'
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const redis = require('redis');

const app = express();
const PORT = 3000;

// Redis client
const client = redis.createClient({
  url: process.env.REDIS_URL || 'redis://redis:6379'
});

client.connect().catch(console.error);

// Middleware
app.use(helmet());
app.use(cors());
app.use(express.json());

// Health check with Redis
app.get('/health', async (req, res) => {
  try {
    await client.ping();
    res.json({ 
      status: 'ok', 
      service: 'gateway',
      redis: 'connected',
      timestamp: new Date().toISOString() 
    });
  } catch (error) {
    res.status(500).json({ 
      status: 'error', 
      service: 'gateway',
      redis: 'disconnected',
      error: error.message,
      timestamp: new Date().toISOString() 
    });
  }
});

// Cache example endpoint
app.get('/cache/:key', async (req, res) => {
  try {
    const value = await client.get(req.params.key);
    res.json({ key: req.params.key, value: value });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.post('/cache/:key', async (req, res) => {
  try {
    await client.set(req.params.key, JSON.stringify(req.body), { EX: 3600 });
    res.json({ message: 'Cached successfully', key: req.params.key });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/docs', (req, res) => {
  res.json({ 
    message: 'Gateway API with Redis Cache',
    version: '1.0.0',
    endpoints: {
      health: '/health',
      docs: '/docs',
      cache_get: '/cache/:key',
      cache_set: 'POST /cache/:key'
    }
  });
});

app.get('/', (req, res) => {
  res.json({ 
    message: 'Gateway with Redis is running',
    docs: '/docs',
    health: '/health'
  });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(\`Gateway server with Redis running on port \${PORT}\`);
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
```

```env
# custom-configs/gateway/.env
NODE_ENV=production
PORT=3000
LOG_LEVEL=info
REDIS_URL=redis://redis:6379
```

### Configuração com SSL Termination

Se você quiser adicionar certificados SSL, pode configurar via contexto CDK:

```bash
# Deploy com SSL (você precisa ter um domínio e certificado ACM)
./deploy.sh deploy-infra \
  --expose-swagger-public true \
  --domain-name yourdomain.com \
  --certificate-arn arn:aws:acm:region:account:certificate/cert-id
```

### Configuração com Volumes EBS Personalizados

Para adicionar volumes EBS persistentes, você pode modificar a stack ou usar init scripts no UserData para montar volumes existentes.

## Automação de Configurações Personalizadas

Você pode criar um script para automatizar o processo:

```bash
#!/bin/bash
# custom-deploy.sh

set -e

echo "Deploying custom configurations..."

# Get bucket name
BUCKET_NAME=$(aws cloudformation describe-stacks --stack-name InfrastructureStack --query "Stacks[0].Outputs[?OutputKey=='ConfigBucketName'].OutputValue" --output text)

# Upload custom configs
aws s3 cp ./custom-configs/fastapi/ s3://$BUCKET_NAME/fastapi/ --recursive
aws s3 cp ./custom-configs/gateway/ s3://$BUCKET_NAME/gateway/ --recursive

echo "Custom configurations uploaded. Starting instance refresh..."

# Instance refresh
./instance-refresh.sh both

echo "Custom deployment completed!"
```
