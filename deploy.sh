#!/bin/bash

# Script principal para gerenciar a infraestrutura CDK
# Uso: ./deploy.sh [command] [options]

set -e

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
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

log_header() {
    echo -e "${BLUE}=== $1 ===${NC}"
}

# Verificar dependências
check_dependencies() {
    local missing_deps=()
    
    if ! command -v aws &> /dev/null; then
        missing_deps+=("aws-cli")
    fi
    
    if ! command -v cdk &> /dev/null; then
        missing_deps+=("aws-cdk")
    fi
    
    if ! command -v python3 &> /dev/null; then
        missing_deps+=("python3")
    fi
    
    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        log_error "Dependências não encontradas: ${missing_deps[*]}"
        log_info "Instale as dependências necessárias:"
        log_info "- AWS CLI: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
        log_info "- AWS CDK: npm install -g aws-cdk"
        log_info "- Python 3: https://www.python.org/downloads/"
        exit 1
    fi
}

# Configurar ambiente Python
setup_python_env() {
    log_info "Configurando ambiente Python..."
    
    if [[ ! -d ".venv" ]]; then
        python3 -m venv .venv
        log_info "Ambiente virtual criado"
    fi
    
    source .venv/bin/activate
    pip install -r requirements.txt
    log_info "Dependências Python instaladas"
}

# Bootstrap CDK (se necessário)
bootstrap_cdk() {
    log_info "Verificando bootstrap do CDK..."
    
    # Verificar se já foi feito bootstrap
    account=$(aws sts get-caller-identity --query Account --output text)
    region=$(aws configure get region || echo "us-east-1")
    
    bootstrap_stack="CDKToolkit"
    if ! aws cloudformation describe-stacks --stack-name "$bootstrap_stack" --region "$region" &>/dev/null; then
        log_warn "CDK Bootstrap necessário. Executando..."
        cdk bootstrap aws://$account/$region
        log_info "Bootstrap concluído"
    else
        log_info "Bootstrap já foi executado"
    fi
}

# Deploy da infraestrutura
deploy_infrastructure() {
    local context_args=""
    
    # Verificar argumentos de contexto
    while [[ $# -gt 0 ]]; do
        case $1 in
            --expose-swagger-public)
                context_args="$context_args -c expose_swagger_public=$2"
                shift 2
                ;;
            --restrict-swagger-to-cidr)
                context_args="$context_args -c restrict_swagger_to_cidr=$2"
                shift 2
                ;;
            --use-eip)
                context_args="$context_args -c use_eip=$2"
                shift 2
                ;;
            --arch)
                context_args="$context_args -c arch=$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done
    
    log_header "Deploy da Infraestrutura"
    log_info "Context args: $context_args"
    
    # Deploy
    if [[ -n "$context_args" ]]; then
        cdk deploy InfrastructureStack $context_args --require-approval never
    else
        cdk deploy InfrastructureStack --require-approval never
    fi
    
    log_info "✅ Infraestrutura deployada com sucesso!"
}

# Upload configurações e refresh das instâncias
setup_and_refresh() {
    log_header "Configuração e Atualização das Instâncias"
    
    # Upload das configurações
    if [[ -f "upload-configs.sh" ]]; then
        ./upload-configs.sh
    else
        log_error "Script upload-configs.sh não encontrado"
        return 1
    fi
    
    # Aguardar um pouco para as instâncias estarem prontas
    log_info "Aguardando 60s para as instâncias estarem prontas..."
    sleep 60
    
    # Instance refresh
    if [[ -f "instance-refresh.sh" ]]; then
        ./instance-refresh.sh both
    else
        log_error "Script instance-refresh.sh não encontrado"
        return 1
    fi
}

# Mostrar informações da stack
show_info() {
    log_header "Informações da Stack"
    
    # Obter outputs da stack
    outputs=$(aws cloudformation describe-stacks \
        --stack-name InfrastructureStack \
        --query "Stacks[0].Outputs" \
        --output table 2>/dev/null || echo "")
    
    if [[ -n "$outputs" ]]; then
        echo "$outputs"
        echo ""
        
        # URLs importantes
        alb_dns=$(aws cloudformation describe-stacks \
            --stack-name InfrastructureStack \
            --query "Stacks[0].Outputs[?OutputKey=='SwaggerAlbDnsName'].OutputValue" \
            --output text 2>/dev/null || echo "")
        
        if [[ -n "$alb_dns" ]]; then
            log_info "🌐 URLs importantes:"
            log_info "   FastAPI Swagger: http://$alb_dns/swagger/api/docs"
            log_info "   Gateway Swagger: http://$alb_dns/swagger/gw/api-docs"
            log_info "   FastAPI Health:  http://$alb_dns/swagger/api/docs"
            log_info "   Gateway Health:  http://$alb_dns/swagger/gw/api-docs"
        fi
    else
        log_warn "Stack não encontrada ou sem outputs"
    fi
}

# Destruir stack
destroy_infrastructure() {
    log_header "Destruição da Infraestrutura"
    log_warn "⚠️  ATENÇÃO: Isso irá destruir TODA a infraestrutura!"
    log_warn "As seguintes ações serão executadas:"
    log_warn "  - Terminar todas as instâncias EC2"
    log_warn "  - Deletar Load Balancers e Target Groups"
    log_warn "  - Deletar Security Groups"
    log_warn "  - Manter VPC e bucket S3 (RemovalPolicy.RETAIN)"
    echo ""
    read -p "Tem certeza que deseja continuar? (digite 'confirmar'): " confirmation
    
    if [[ "$confirmation" != "confirmar" ]]; then
        log_info "Operação cancelada"
        return 0
    fi
    
    log_info "Destruindo stack..."
    cdk destroy InfrastructureStack --force
    log_info "✅ Stack destruída"
}

# Mostrar status dos serviços
check_status() {
    log_header "Status dos Serviços"
    
    # Obter ALB DNS
    alb_dns=$(aws cloudformation describe-stacks \
        --stack-name InfrastructureStack \
        --query "Stacks[0].Outputs[?OutputKey=='SwaggerAlbDnsName'].OutputValue" \
        --output text 2>/dev/null || echo "")
    
    if [[ -z "$alb_dns" ]]; then
        log_warn "Stack não deployada ou ALB não encontrado"
        return 1
    fi
    
    log_info "Verificando serviços..."
    
    # Check FastAPI
    log_info "FastAPI:"
    if curl -s -f "http://$alb_dns/swagger/api/docs" >/dev/null 2>&1; then
        log_info "  ✅ Saudável - http://$alb_dns/swagger/api/docs"
    else
        log_warn "  ❌ Não respondendo - http://$alb_dns/swagger/api/docs"
    fi
    
    # Check Gateway  
    log_info "Gateway:"
    if curl -s -f "http://$alb_dns/swagger/gw/api-docs" >/dev/null 2>&1; then
        log_info "  ✅ Saudável - http://$alb_dns/swagger/gw/api-docs"
    else
        log_warn "  ❌ Não respondendo - http://$alb_dns/swagger/gw/api-docs"
    fi
    
    # ASG Status
    fastapi_asg=$(aws cloudformation describe-stacks \
        --stack-name InfrastructureStack \
        --query "Stacks[0].Outputs[?OutputKey=='FastAPIASGName'].OutputValue" \
        --output text 2>/dev/null || echo "")
    
    gateway_asg=$(aws cloudformation describe-stacks \
        --stack-name InfrastructureStack \
        --query "Stacks[0].Outputs[?OutputKey=='GatewayASGName'].OutputValue" \
        --output text 2>/dev/null || echo "")
    
    if [[ -n "$fastapi_asg" ]]; then
        log_info "ASG FastAPI ($fastapi_asg):"
        aws autoscaling describe-auto-scaling-groups \
            --auto-scaling-group-names "$fastapi_asg" \
            --query "AutoScalingGroups[0].[DesiredCapacity, MinSize, MaxSize]" \
            --output text | while read desired min max; do
            log_info "  Desired: $desired, Min: $min, Max: $max"
        done
    fi
    
    if [[ -n "$gateway_asg" ]]; then
        log_info "ASG Gateway ($gateway_asg):"
        aws autoscaling describe-auto-scaling-groups \
            --auto-scaling-group-names "$gateway_asg" \
            --query "AutoScalingGroups[0].[DesiredCapacity, MinSize, MaxSize]" \
            --output text | while read desired min max; do
            log_info "  Desired: $desired, Min: $min, Max: $max"
        done
    fi
}

# Mostrar ajuda
show_help() {
    echo "Uso: $0 <command> [options]"
    echo ""
    echo "Comandos:"
    echo "  init                    - Configurar ambiente e bootstrap CDK"
    echo "  deploy                  - Deploy completo (infra + configs + refresh)"
    echo "  deploy-infra [opts]     - Deploy apenas da infraestrutura"
    echo "  upload-configs          - Upload apenas das configurações Docker"
    echo "  refresh [target]        - Instance refresh (fastapi|gateway|both)"
    echo "  status                  - Verificar status dos serviços"
    echo "  info                    - Mostrar informações da stack"
    echo "  destroy                 - Destruir infraestrutura"
    echo "  help                    - Mostrar esta ajuda"
    echo ""
    echo "Opções para deploy-infra:"
    echo "  --expose-swagger-public true|false"
    echo "  --restrict-swagger-to-cidr CIDR"
    echo "  --use-eip true|false"
    echo "  --arch ARM_64|X86_64"
    echo ""
    echo "Exemplos:"
    echo "  $0 init"
    echo "  $0 deploy"
    echo "  $0 deploy-infra --expose-swagger-public true --arch ARM_64"
    echo "  $0 refresh fastapi"
    echo "  $0 status"
}

# Main function
main() {
    local command=${1:-"help"}
    shift || true
    
    case "$command" in
        "init")
            log_header "Inicialização do Ambiente"
            check_dependencies
            setup_python_env
            bootstrap_cdk
            log_info "🎉 Ambiente configurado com sucesso!"
            ;;
        "deploy")
            check_dependencies
            setup_python_env
            deploy_infrastructure "$@"
            setup_and_refresh
            show_info
            ;;
        "deploy-infra")
            check_dependencies
            setup_python_env
            deploy_infrastructure "$@"
            ;;
        "upload-configs")
            if [[ -f "upload-configs.sh" ]]; then
                ./upload-configs.sh
            else
                log_error "Script upload-configs.sh não encontrado"
                exit 1
            fi
            ;;
        "refresh")
            local target=${1:-"both"}
            if [[ -f "instance-refresh.sh" ]]; then
                ./instance-refresh.sh "$target" "$2"
            else
                log_error "Script instance-refresh.sh não encontrado"
                exit 1
            fi
            ;;
        "status")
            check_status
            ;;
        "info")
            show_info
            ;;
        "destroy")
            destroy_infrastructure
            ;;
        "help"|"--help"|"-h")
            show_help
            ;;
        *)
            log_error "Comando inválido: $command"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
