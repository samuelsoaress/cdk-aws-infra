#!/bin/bash

# Script para baixar a chave SSH da AWS
# Uso: ./download-ssh-key.sh

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Funções auxiliares
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Verificar se AWS CLI está configurado
if ! command -v aws &> /dev/null; then
    log_error "AWS CLI não encontrado. Instale e configure o AWS CLI."
    exit 1
fi

# Obter stack outputs
get_stack_output() {
    local output_key=$1
    aws cloudformation describe-stacks \
        --stack-name InfrastructureStack \
        --query "Stacks[0].Outputs[?OutputKey=='$output_key'].OutputValue" \
        --output text 2>/dev/null || echo ""
}

# Função principal
main() {
    log_info "=== Download SSH Key ==="
    
    # Obter nome da chave
    local key_name=$(get_stack_output "SSHKeyName")
    if [[ -z "$key_name" ]]; then
        log_error "Nome da chave SSH não encontrado nos outputs da stack"
        log_info "Verifique se a stack InfrastructureStack foi deployada"
        exit 1
    fi
    
    local key_file="$key_name.pem"
    
    log_info "Nome da chave: $key_name"
    log_info "Arquivo local: $key_file"
    
    # Verificar se já existe
    if [[ -f "$key_file" ]]; then
        log_warn "Arquivo $key_file já existe!"
        echo -n "Sobrescrever? (y/N): "
        read -r response
        if [[ ! "$response" =~ ^[Yy]$ ]]; then
            log_info "Operação cancelada"
            exit 0
        fi
    fi
    
    log_info "Tentando obter a chave privada via AWS CLI..."
    
    # Tentar diferentes métodos
    if aws ec2 describe-key-pairs --key-names "$key_name" --include-public-key --query 'KeyPairs[0].KeyMaterial' --output text > "$key_file" 2>/dev/null; then
        log_info "✅ Chave baixada com sucesso via describe-key-pairs!"
    else
        log_warn "❌ Método describe-key-pairs falhou (esperado para chaves criadas via CDK)"
        log_info ""
        log_info "📋 SOLUÇÃO MANUAL:"
        log_info "1. Vá para AWS Console: https://console.aws.amazon.com/ec2/v2/home#KeyPairs"
        log_info "2. Encontre a chave: $key_name"
        log_info "3. Selecione a chave e clique em 'Actions'"
        log_info "4. Escolha 'Get private key' (se disponível)"
        log_info "5. Copie o conteúdo e cole em um arquivo:"
        echo ""
        echo "cat > $key_file << 'EOF'"
        echo "-----BEGIN RSA PRIVATE KEY-----"
        echo "[COLE AQUI O CONTEÚDO DA CHAVE PRIVADA]"
        echo "-----END RSA PRIVATE KEY-----"
        echo "EOF"
        echo ""
        log_info "6. Defina as permissões corretas:"
        echo "chmod 400 $key_file"
        echo ""
        log_warn "⚠️  NOTA: Chaves criadas via CDK podem não ter o material privado"
        log_warn "disponível via API. Você pode precisar recriar a chave manualmente."
        exit 1
    fi
    
    # Definir permissões corretas
    chmod 400 "$key_file"
    log_info "✅ Permissões definidas para 400"
    
    # Verificar se o arquivo foi criado corretamente
    if [[ -s "$key_file" ]]; then
        log_info "✅ Chave SSH baixada com sucesso!"
        log_info "📁 Arquivo: $key_file"
        log_info ""
        log_info "🚀 Agora você pode usar SSH:"
        log_info "  ./deploy.sh ssh fastapi"
        log_info "  ./deploy.sh ssh gateway"
    else
        log_error "❌ Arquivo de chave está vazio ou não foi criado"
        rm -f "$key_file"
        exit 1
    fi
}

# Verificar argumentos
if [[ "$1" == "--help" || "$1" == "-h" ]]; then
    echo "Uso: $0"
    echo ""
    echo "Baixa a chave SSH privada da AWS para conexão com as instâncias."
    echo ""
    echo "Pré-requisitos:"
    echo "  - AWS CLI configurado"
    echo "  - Stack InfrastructureStack deployada"
    echo ""
    echo "Após baixar a chave, use:"
    echo "  ./deploy.sh ssh fastapi"
    echo "  ./deploy.sh ssh gateway"
    exit 0
fi

main "$@"
