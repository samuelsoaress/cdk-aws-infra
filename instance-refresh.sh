#!/bin/bash

# Script para realizar Instance Refresh controlado nos ASGs
# Uso: ./instance-refresh.sh [fastapi|gateway|both] [--force]

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

# Função para fazer instance refresh
start_instance_refresh() {
    local asg_name=$1
    local service_name=$2
    
    log_info "Iniciando Instance Refresh para $service_name (ASG: $asg_name)..."
    
    # Verificar se já existe um refresh em andamento
    active_refresh=$(aws autoscaling describe-instance-refreshes \
        --auto-scaling-group-name "$asg_name" \
        --query "InstanceRefreshes[?Status=='InProgress'].InstanceRefreshId" \
        --output text 2>/dev/null || echo "")
    
    if [[ -n "$active_refresh" ]]; then
        log_warn "Já existe um Instance Refresh em andamento: $active_refresh"
        if [[ "$2" != "--force" ]]; then
            log_info "Use --force para cancelar o refresh atual e iniciar um novo"
            return 1
        else
            log_warn "Cancelando refresh atual..."
            aws autoscaling cancel-instance-refresh --auto-scaling-group-name "$asg_name"
            sleep 10
        fi
    fi
    
    # Iniciar novo refresh (otimizado para velocidade)
    refresh_id=$(aws autoscaling start-instance-refresh \
        --auto-scaling-group-name "$asg_name" \
        --preferences '{
            "InstanceWarmup": 120,
            "MinHealthyPercentage": 0,
            "CheckpointPercentages": [100],
            "CheckpointDelay": 60
        }' \
        --query "InstanceRefreshId" \
        --output text)
    
    log_info "Instance Refresh iniciado: $refresh_id"
    
    # Monitorar progresso
    monitor_refresh "$asg_name" "$refresh_id" "$service_name"
}

# Função para monitorar o progresso do refresh
monitor_refresh() {
    local asg_name=$1
    local refresh_id=$2
    local service_name=$3
    
    log_info "Monitorando progresso do Instance Refresh para $service_name..."
    
    while true; do
        refresh_status=$(aws autoscaling describe-instance-refreshes \
            --auto-scaling-group-name "$asg_name" \
            --instance-refresh-ids "$refresh_id" \
            --query "InstanceRefreshes[0].[Status, PercentageComplete, StatusReason]" \
            --output text)
        
        status=$(echo "$refresh_status" | cut -f1)
        percentage=$(echo "$refresh_status" | cut -f2)
        reason=$(echo "$refresh_status" | cut -f3)
        
        case "$status" in
            "InProgress")
                log_info "Progresso: ${percentage}% - $reason"
                sleep 30
                ;;
            "Successful")
                log_info "✅ Instance Refresh completado com sucesso para $service_name!"
                break
                ;;
            "Failed"|"Cancelled")
                log_error "❌ Instance Refresh falhou para $service_name: $reason"
                return 1
                ;;
            *)
                log_warn "Status desconhecido: $status"
                sleep 30
                ;;
        esac
    done
}

# Função para verificar health dos serviços
check_service_health() {
    local service_name=$1
    log_info "Verificando health do $service_name..."
    
    case "$service_name" in
        "FastAPI")
            alb_dns=$(get_stack_output "SwaggerAlbDnsName")
            if [[ -n "$alb_dns" ]]; then
                # Testar primeiro o endpoint nativo
                health_url="http://$alb_dns/swagger/api/docs"
                if curl -s -f "$health_url" > /dev/null; then
                    log_info "✅ FastAPI está saudável (Swagger acessível)"
                else
                    # Fallback para docs endpoint
                    health_url="http://$alb_dns/swagger/api/docs"
                    if curl -s -f "$health_url" > /dev/null; then
                        log_info "✅ FastAPI está saudável (Health OK)"
                    else
                        log_warn "⚠️  FastAPI pode não estar respondendo corretamente"
                    fi
                fi
            fi
            ;;
        "Gateway")
            alb_dns=$(get_stack_output "SwaggerAlbDnsName")
            if [[ -n "$alb_dns" ]]; then
                # Testar primeiro o endpoint nativo
                health_url="http://$alb_dns/swagger/gw/api-docs"
                if curl -s -f "$health_url" > /dev/null; then
                    log_info "✅ Gateway está saudável (Swagger acessível)"
                else
                    # Fallback para api-docs endpoint
                    health_url="http://$alb_dns/swagger/gw/api-docs"
                    if curl -s -f "$health_url" > /dev/null; then
                        log_info "✅ Gateway está saudável (Health OK)"
                    else
                        log_warn "⚠️  Gateway pode não estar respondendo corretamente"
                    fi
                fi
            fi
            ;;
    esac
}

# Função principal
main() {
    local target=${1:-"both"}
    local force_flag=$2
    
    log_info "=== Instance Refresh Manager ==="
    log_info "Target: $target"
    
    # Obter nomes dos ASGs
    fastapi_asg=$(get_stack_output "FastAPIASGName")
    gateway_asg=$(get_stack_output "GatewayASGName")
    
    if [[ -z "$fastapi_asg" || -z "$gateway_asg" ]]; then
        log_error "Não foi possível obter os nomes dos ASGs. Verifique se a stack foi deployada."
        exit 1
    fi
    
    log_info "FastAPI ASG: $fastapi_asg"
    log_info "Gateway ASG: $gateway_asg"
    
    case "$target" in
        "fastapi")
            start_instance_refresh "$fastapi_asg" "FastAPI" "$force_flag"
            check_service_health "FastAPI"
            ;;
        "gateway")
            start_instance_refresh "$gateway_asg" "Gateway" "$force_flag"
            check_service_health "Gateway"
            ;;
        "both")
            log_info "Iniciando refresh sequencial (FastAPI primeiro)..."
            start_instance_refresh "$fastapi_asg" "FastAPI" "$force_flag"
            check_service_health "FastAPI"
            
            log_info "Aguardando 30s antes do próximo refresh..."
            sleep 30
            
            start_instance_refresh "$gateway_asg" "Gateway" "$force_flag"
            check_service_health "Gateway"
            ;;
        *)
            log_error "Target inválido. Use: fastapi, gateway, ou both"
            exit 1
            ;;
    esac
    
    log_info "🎉 Processo completado!"
}

# Verificar argumentos
if [[ "$1" == "--help" || "$1" == "-h" ]]; then
    echo "Uso: $0 [fastapi|gateway|both] [--force]"
    echo ""
    echo "Exemplos:"
    echo "  $0 fastapi          # Refresh apenas FastAPI"
    echo "  $0 gateway          # Refresh apenas Gateway" 
    echo "  $0 both             # Refresh ambos (sequencial)"
    echo "  $0 fastapi --force  # Force refresh mesmo se já existir um em andamento"
    exit 0
fi

main "$@"
