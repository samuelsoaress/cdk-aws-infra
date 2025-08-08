
# AWS CDK Infrastructure Project 🚀

Este projeto implementa uma infraestrutura AWS usando CDK (Cloud Development Kit) em Python, contendo:

- **Frontend Stack**: Hospedagem estática React com S3 + CloudFront + OAC
- **Infrastructure Stack**: Instâncias EC2 para FastAPI e Gateway com VPC dedicada

## 📋 Arquitetura

### Frontend Stack
- **S3 Bucket**: Hospedagem de arquivos estáticos
- **CloudFront**: CDN global com Origin Access Control (OAC)
- **Segurança**: Bucket privado, acesso apenas via CloudFront

### Infrastructure Stack
- **VPC**: Rede virtual dedicada com subnets públicas
- **EC2 FastAPI**: Instância `t4g.micro` (ARM64) para API backend
- **EC2 Gateway**: Instância `t4g.medium` (ARM64) para gateway/proxy
- **Security Groups**: Regras de firewall configuradas
- **SSH Keys**: Chaves gerenciadas automaticamente via SSM Parameter Store

## 🛠️ Setup Inicial

Este projeto é configurado como um projeto Python padrão. O processo de inicialização cria automaticamente um ambiente virtual no diretório `.venv`.

### 1. Criar ambiente virtual

Para criar manualmente o ambiente virtual no MacOS/Linux:

```bash
python3 -m venv .venv
```

### 2. Ativar ambiente virtual

```bash
# MacOS/Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate.bat
```

### 3. Instalar dependências

```bash
pip install -r requirements.txt
```

### 4. Fazer bootstrap do CDK (primeira vez)

```bash
cdk bootstrap
```

## 🚀 Deploy da Infraestrutura

### Deploy de todas as stacks
```bash
cdk deploy --all
```

### Deploy individual
```bash
# Apenas o frontend
cdk deploy FrontendStack

# Apenas a infraestrutura
cdk deploy InfrastructureStack
```

## 🔧 Gerenciamento de Instâncias EC2

### Script `manage-instances.sh`

O arquivo `manage-instances.sh` é um utilitário para **parar** e **iniciar** as instâncias EC2 da Infrastructure Stack, permitindo economia de custos quando não estiver usando os serviços.

#### 📋 O que o script faz:

- **Identifica automaticamente** todas as instâncias EC2 da stack `InfrastructureStack`
- **Para ou inicia** as seguintes instâncias:
  - 🟦 **FastAPI Instance** (`t4g.micro`): Servidor backend da API
  - 🟩 **Gateway Instance** (`t4g.medium`): Servidor gateway/proxy
- **Preserva todos os dados** - apenas para/inicia as instâncias, não deleta nada

#### 🎯 Como usar:

```bash
# Dar permissão de execução (primeira vez)
chmod +x manage-instances.sh

# Parar todas as instâncias (economiza $$$)
./manage-instances.sh stop

# Iniciar todas as instâncias
./manage-instances.sh start
```

#### ⚠️ Importante:
- **IPs públicos mudam** após parar/iniciar as instâncias
- **Dados permanecem salvos** nos volumes EBS
- **Use antes de dormir** para não pagar por instâncias paradas 💰

#### 💰 Economia de custos:
- **t4g.micro**: ~$0.0084/hora → Parar 12h = ~$0.10 economizado/dia
- **t4g.medium**: ~$0.0336/hora → Parar 12h = ~$0.40 economizado/dia
- **Total**: ~$0.50/dia de economia parando durante a noite

## 🔑 Acesso SSH às Instâncias

As chaves SSH são geradas automaticamente e armazenadas no AWS Systems Manager:

### Baixar chaves SSH
```bash
# Chave do FastAPI
aws ssm get-parameter --name "/ec2/keypair/key-033d04e0bd6d8db25" --with-decryption --query 'Parameter.Value' --output text > fastapi-key.pem

# Chave do Gateway  
aws ssm get-parameter --name "/ec2/keypair/key-007e08a008afa4908" --with-decryption --query 'Parameter.Value' --output text > gateway-key.pem

# Definir permissões corretas
chmod 600 *.pem
```

### Conectar via SSH
```bash
# Obter IPs das instâncias
aws cloudformation describe-stacks --stack-name InfrastructureStack --query 'Stacks[0].Outputs' --output table

# Conectar ao FastAPI
ssh -i fastapi-key.pem ec2-user@<FASTAPI_IP>

# Conectar ao Gateway
ssh -i gateway-key.pem ec2-user@<GATEWAY_IP>
```

## 🔄 CI/CD com GitHub Actions

O projeto inclui workflows automatizados para deploy:

- **Deploy da infraestrutura**: Executado automaticamente em push para `main`
- **Deploy de aplicações**: Workflows separados para cada repositório (FastAPI/Gateway)
- **Chaves SSH**: Recuperadas automaticamente do SSM nos workflows

### Secrets necessários no GitHub:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`  
- `AWS_REGION`

## 📊 Outputs da Stack

Após o deploy, você pode obter informações importantes:

```bash
# Ver todos os outputs
aws cloudformation describe-stacks --stack-name InfrastructureStack --query 'Stacks[0].Outputs' --output table

# Frontend URL
aws cloudformation describe-stacks --stack-name FrontendStack --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' --output text
```

## 📚 Comandos CDK Úteis

| Comando | Descrição |
|---------|-----------|
| `cdk list` | Lista todas as stacks no app |
| `cdk synth` | Gera o template CloudFormation |
| `cdk deploy` | Faz deploy da stack para AWS |
| `cdk diff` | Compara stack deployada com estado atual |
| `cdk destroy` | **⚠️ DELETA** toda a stack (use com cuidado!) |
| `cdk docs` | Abre a documentação do CDK |

## 🗂️ Estrutura do Projeto

```
cdk-aws-infra/
├── app.py                          # Entry point do CDK
├── cdk.json                        # Configuração do CDK
├── manage-instances.sh             # Script para parar/iniciar EC2s
├── cdk_aws_infra/
│   ├── cdk_aws_infra_stack.py     # Stack principal (orquestrador)
│   └── stacks/
│       ├── frontend_stack.py       # Stack do frontend (S3 + CloudFront)
│       └── infrastructure_stack.py # Stack da infraestrutura (VPC + EC2)
├── .github/
│   └── workflows/
│       └── deploy.yml              # CI/CD automático
└── requirements.txt                # Dependências Python
```

## 🔧 Troubleshooting

### Problema: CDK não encontrado
```bash
# Ativar ambiente virtual
source .venv/bin/activate

# Reinstalar dependências
pip install -r requirements.txt
```

### Problema: Bootstrap necessário
```bash
cdk bootstrap
```

### Problema: Permissões SSH
```bash
chmod 600 *.pem
```

### Problema: IP mudou após restart
```bash
# Obter novos IPs
aws cloudformation describe-stacks --stack-name InfrastructureStack --query 'Stacks[0].Outputs' --output table
```

---

**💡 Dica**: Use o script `manage-instances.sh stop` antes de sair do trabalho para economizar na conta da AWS! 💰

Enjoy! 🚀
