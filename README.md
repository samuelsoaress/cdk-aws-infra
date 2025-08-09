# CDK AWS Infrastructure

Infraestrutura AWS com CDK para FastAPI e Gateway.

Agora existem DOIS modos de operação:

1. Modo Persistente (recomendado para desenvolvimento) - instância EC2 única executando ambos os serviços (sem ASG / ALB)
2. Modo Escalável (legado / produção) - ASGs separados + ALB com path-based routing

## 🏗️ Arquitetura

### Componentes (Modo Persistente)

- **VPC** pública simples (sem NAT)
- **EC2 Única** (t4g.medium) rodando FastAPI (porta 8000) e Gateway (porta 3000) via docker-compose
- **Security Group** com SSH + portas 8000/3000 expostas (pode restringir conforme necessário)
- **S3** para configs (opcional em dev, ainda suportado)
- **SSM** para parâmetros / acesso Session Manager

### Componentes (Modo Escalável - Legado)

- **ASGs** separados (FastAPI e Gateway)
- **ALB** público com regras /swagger/api/* e /swagger/gw/*
- **Target Groups** com health checks
- **Instance Refresh** para deploy azul/verde

### Características de Resiliência

- **Instance Refresh**: Blue/Green deployment controlado
- **Auto Recovery**: Health checks e State Manager
- **Zero Downtime**: Substituição controlada de instâncias
- **Persistência**: Configurações em S3, volumes EBS opcionais

## 🚀 Quick Start

### 1. Inicialização

```bash
./deploy.sh init
```

### 2. Deploy Completo (modo persistente rápido)

```bash
./deploy.sh deploy
```

### 3. Verificar Status

```bash
./deploy.sh status
```

## 📋 Comandos Disponíveis

Em modo persistente NÃO há Instance Refresh nem necessidade de quick-restart; basta redeploy / atualizar código e reiniciar containers conforme necessidade.

### Deploy Principal
```bash
./deploy.sh <command> [options]
```

#### Comandos:
- `init` - Configurar ambiente e bootstrap CDK
- `deploy` - Deploy completo (infra + configs + refresh)
- `deploy-infra [opts]` - Deploy apenas da infraestrutura
- `upload-configs` - Upload apenas das configurações Docker
- `refresh [target]` - LEGADO (apenas modo escalável)
- `status` - Verificar status (em modo persistente use a URL direta ou SSH)
- `info` - Mostrar informações da stack
- `destroy` - Destruir infraestrutura

#### Opções para deploy-infra:
- `--expose-swagger-public true|false` (default: true)
- `--restrict-swagger-to-cidr CIDR` (opcional)
- `--use-eip true|false` (default: false)
- `--arch ARM_64|X86_64` (default: ARM_64)

### Exemplos

```bash
# Deploy com configurações customizadas
./deploy.sh deploy-infra --expose-swagger-public true --arch ARM_64

# Refresh apenas do FastAPI
./deploy.sh refresh fastapi

# Refresh forçado (cancela refresh em andamento)
./instance-refresh.sh fastapi --force

# Upload de novas configurações
./deploy.sh upload-configs
```

## 🚀 CI/CD com GitHub Actions

O workflow principal (`deploy.yml`) agora aceita o parâmetro `persistent_mode` (boolean):

```
persistent_mode: true  # cria uma única instância dev
```

Quando `persistent_mode=true`:
- ASGs/ALB NÃO são criados
- Um EC2 único com docker-compose sobe ambos os serviços
- Outputs e parâmetros SSM específicos são gerados:
  - /infra/cdk/dev/instance-id
  - /infra/cdk/dev/public-ip
  - DevFastApiUrl / DevGatewayUrl (Outputs CloudFormation)
  - /infra/cdk/mode/persistent = true

### Workflows Disponíveis

#### 1. Deploy Principal (`deploy.yml`)
Executa automaticamente em push para `main` ou pode ser executado manualmente.

**Triggers:**
- Push para `main`/`master`
- Pull Request (apenas validação)
- Manual com parâmetros customizados

**Parâmetros de Deploy Manual:**
- `target`: infrastructure | frontend | both | configs-only | refresh-only
- `instance_refresh`: true/false (IGNORADO quando persistent_mode=true)
- `expose_swagger_public`: true/false (irrelevante em modo persistente porque não há ALB)
- `arch`: ARM_64 | X86_64
- `persistent_mode`: true/false

**Exemplo de uso manual:**
```
GitHub Actions → Deploy AWS Infrastructure → Run workflow
Target: both
Instance Refresh: true
Expose Swagger Public: true
Architecture: ARM_64
```

#### 2. Manutenção (`maintenance.yml`)
Workflow manual para operações de manutenção.

**Ações disponíveis:**
- `instance-refresh`: Refresh controlado das instâncias
- `upload-configs`: Re-upload das configurações Docker
- `status-check`: Verificação completa de status
- `emergency-stop`: Parada de emergência (scale down para 0)
- `emergency-start`: Início de emergência (scale up para 1)

**Exemplo de uso:**
```
GitHub Actions → Infrastructure Maintenance → Run workflow
Action: instance-refresh
Target: both
Force: false
```

#### 3. Monitor de Saúde (`health-monitor.yml`)
Executa automaticamente para monitorar a saúde dos serviços.

**Características:**
- Execução automática a cada 30 minutos (horário comercial)
- Health check automático dos endpoints
- Tentativa de auto-recovery em caso de falha
- Criação automática de issues para falhas críticas
- Execução manual para verificações detalhadas

### Secrets Necessários

Configure os seguintes secrets no GitHub:

```
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION
```

### Estrutura de Deploy Automático

1. **Validação** (PRs)
   - CDK synth
   - CDK diff (se credenciais disponíveis)

2. **Deploy** (Push para main)
   - Setup do ambiente
   - CDK bootstrap (se necessário)
   - Deploy da infraestrutura
   - Upload de configurações Docker
   - Instance refresh automático
   - Verificação de status
   - Geração de relatório

3. **Monitoramento** (Agendado)
   - Health check dos serviços
   - Auto-recovery em caso de falha
   - Alertas automatizados

### Relatórios e Summaries

Cada workflow gera relatórios detalhados com:
- URLs dos serviços
- Status de saúde
- Parâmetros utilizados
- Ações executadas
- Links para troubleshooting

## 🔧 Scripts Auxiliares

### instance-refresh.sh (LEGADO)
Mantido apenas para compatibilidade quando `persistent_mode=false`.

### upload-configs.sh
Faz upload das configurações Docker para S3:
```bash
./upload-configs.sh
```

## 🌐 Endpoints de Acesso

Modo Persistente:
- **FastAPI Health**: `http://<DEV-IP>:8000/health`
- **Gateway Docs**: `http://<DEV-IP>:3000/api-docs`

Modo Escalável (LEGADO):
- **FastAPI Swagger**: `http://<ALB-DNS>/swagger/api/docs`
- **Gateway Swagger**: `http://<ALB-DNS>/swagger/gw/api-docs`

## 🔒 Security Groups

### Internal SG (Compartilhado)
- **Outbound**: Allow all
- **Inbound**:
  - TCP 8000 (FastAPI) - entre membros do SG
  - TCP 3000 (Gateway) - entre membros do SG
  - ICMP - para ping interno
  - TCP 8000/3000 - do ALB SG

### ALB SG
- **Outbound**: Allow all
- **Inbound**:
  - TCP 80/443 - público ou restrito por CIDR

## 💾 Persistência e Configuração

### S3 Bucket
Armazena configurações Docker:
```
s3://bucket-name/
├── fastapi/
│   ├── docker-compose.yml
│   └── .env
└── gateway/
    ├── docker-compose.yml
    └── .env
```

### UserData Idempotente
- Instala Docker e Docker Compose
- Baixa configurações do S3
- Sobe serviços automaticamente
- Configura health checks via cron

### SSM State Manager
Document para garantir que os serviços Docker estejam sempre rodando.

## 🏗️ Tipos de Instância

**Mantidos da configuração atual:**
- **FastAPI**: `t4g.micro` (ARM64)
- **Gateway**: `t4g.medium` (ARM64)
- **AMI**: Amazon Linux 2023

## 🔄 Instance Refresh (LEGADO)

### Estratégia Blue/Green
- **MinHealthyPercentage**: 0 (permite total replacement)
- **InstanceWarmup**: 300s
- **Checkpoints**: 50%, 100%
- **CheckpointDelay**: 300s

### Quando Ocorre Refresh (somente persistent_mode=false)
- Mudanças no Launch Template
- Mudanças na configuração do ASG
- Execução manual via script

## 📊 Monitoramento

### Health Checks
- **ALB**: `/docs` e `/api-docs` endpoints para health checks
- **ASG**: ELB health check + auto recovery
- **State Manager**: Execução periódica para auto-heal

### Logs e Debug
```bash
# Verificar logs das instâncias
aws ssm start-session --target <instance-id>

# Ver logs do Docker
sudo docker-compose logs -f

# Status dos containers
sudo docker-compose ps
```

## 🔧 Troubleshooting

### Problemas com CDK Synth

#### Erro: `.venv/bin/python: not found`
O projeto usa um script wrapper (`run-app.sh`) que automaticamente detecta o ambiente:
- **Local**: Usa `.venv/bin/python` se disponível
- **CI/CD**: Usa `python3` global com dependências instaladas

Se tiver problemas:
```bash
# Testar o script diretamente
./run-app.sh

# Ou instalar dependências globalmente
pip3 install -r requirements.txt
```

### Instance Refresh Travado
```bash
# Cancelar refresh atual
./instance-refresh.sh fastapi --force
```

### Serviços não Respondendo
```bash
# Verificar status
./deploy.sh status

# Re-upload configs e refresh
./deploy.sh upload-configs
./deploy.sh refresh both
```

### Debug de Instâncias
```bash
# Conectar via Session Manager
aws ssm start-session --target $(aws ec2 describe-instances \
  --filters "Name=tag:aws:autoscaling:groupName,Values=<ASG-NAME>" \
  --query "Reservations[0].Instances[0].InstanceId" --output text)
```

### CI/CD Troubleshooting

#### Deploy Falha no GitHub Actions
1. Verificar secrets AWS configurados
2. Verificar permissões IAM
3. Executar `cdk synth` localmente para validar

#### Health Monitor Criando Issues Desnecessárias
1. Ajustar thresholds no workflow
2. Verificar se os endpoints `/docs` e `/api-docs` estão respondendo
3. Considerar aumentar timeout dos health checks

#### Instance Refresh Travado no CI/CD
```bash
# Cancelar via GitHub Actions
GitHub Actions → Infrastructure Maintenance → Run workflow
Action: instance-refresh
Target: both
Force: true
```

#### Emergency Stop/Start
```bash
# Via GitHub Actions
GitHub Actions → Infrastructure Maintenance → Run workflow
Action: emergency-stop  # ou emergency-start
Target: both
```

#### Verificar Logs de Deploy
1. GitHub Actions → Deploy AWS Infrastructure → Ver último run
2. Expandir seções com falhas
3. Verificar outputs de CDK e AWS CLI

## 🗂️ Estrutura de Arquivos

```
├── app.py                    # CDK App principal
├── run-app.sh               # Script wrapper para executar CDK
├── cdk.json                  # Configuração CDK
├── requirements.txt          # Dependências Python
├── deploy.sh                 # Script principal de deploy
├── instance-refresh.sh       # Gerenciamento de Instance Refresh
├── upload-configs.sh         # Upload de configurações Docker
├── manage-instances.sh       # (legacy)
├── .github/
│   └── workflows/
│       ├── deploy.yml        # CI/CD principal
│       ├── maintenance.yml   # Operações de manutenção
│       └── health-monitor.yml # Monitoramento automático
├── cdk_aws_infra/
│   └── stacks/
│       ├── infrastructure_stack.py  # Stack principal (ASG + ALB)
│       └── frontend_stack.py        # Stack do frontend (S3 + CloudFront)
└── cdk.out/                  # Arquivos gerados pelo CDK
```

## 🎯 Objetivos Alcançados

✅ **Modo Dev Estável**: Instância única não sofre recriações automáticas  
✅ **Alternância Fácil**: `-c persistent_mode=true|false` no CDK / workflow  
✅ **Parâmetros SSM Claros**: /infra/cdk/dev/* para acesso rápido  
✅ **Still Compatible**: Arquitetura antiga preservada para produção  
✅ **Deploy Mais Rápido**: Sem Instance Refresh em dev  
✅ **Documentação Atualizada**  

## 📝 Notas Importantes

- **RemovalPolicy.RETAIN**: ASGs e buckets preservados em destroy
- **Sem NAT Gateway**: Economia de custos, instâncias em subnets públicas
- **Health checks configurados**: 30s interval, timeouts otimizados
- **Instance Refresh monitorado**: Scripts mostram progresso em tempo real
- **Configurações versionadas**: S3 bucket com versionamento habilitado
