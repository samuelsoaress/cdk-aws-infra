#!/bin/bash

# Script para monitoramento contínuo dos serviços
# Uso: ./monitor.sh [--interval seconds] [--alerts] [--detailed]

set -e

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[$(date '+%H:%M:%S')]${NC} $1"
}

log_error() {
    echo -e "${RED}[$(date '+%H:%M:%S')]${NC} $1"
}

log_header() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')] === $1 ===${NC}"
}

# Parâmetros
INTERVAL=30
ALERTS=false
DETAILED=false
CONTINUOUS=true

# Parse argumentos
while [[ $# -gt 0 ]]; do
    case $1 in
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        --alerts)
            ALERTS=true
            shift
            ;;
        --detailed)
            DETAILED=true
            shift
            ;;
        --once)
            CONTINUOUS=false
            shift
            ;;
        --help|-h)
            echo "Uso: $0 [--interval seconds] [--alerts] [--detailed] [--once]"
            echo ""
            echo "Opções:"
            echo "  --interval N    Intervalo entre checks em segundos (default: 30)"
            echo "  --alerts        Mostrar alertas detalhados"
            echo "  --detailed      Mostrar informações detalhadas"
            echo "  --once          Executar apenas uma vez"
            echo "  --help         Mostrar esta ajuda"
            exit 0
            ;;
        *)
            echo "Opção desconhecida: $1"
            exit 1
            ;;
    esac
done

# Obter informações da stack
get_stack_info() {
    ALB_DNS=$(aws cloudformation describe-stacks \
        --stack-name InfrastructureStack \
        --query "Stacks[0].Outputs[?OutputKey=='SwaggerAlbDnsName'].OutputValue" \
        --output text 2>/dev/null || echo "")
    
    FASTAPI_ASG=$(aws cloudformation describe-stacks \
        --stack-name InfrastructureStack \
        --query "Stacks[0].Outputs[?OutputKey=='FastAPIASGName'].OutputValue" \
        --output text 2>/dev/null || echo "")
    
    GATEWAY_ASG=$(aws cloudformation describe-stacks \
        --stack-name InfrastructureStack \
        --query "Stacks[0].Outputs[?OutputKey=='GatewayASGName'].OutputValue" \
        --output text 2>/dev/null || echo "")
}

# Check health de um serviço
check_service_health() {
    local service_name=$1
    local endpoint=$2
    local timeout=${3:-10}
    
    if curl -s -f --max-time $timeout "$endpoint" >/dev/null 2>&1; then
        echo "healthy"
    else
        echo "unhealthy"
    fi
}

# Check ASG status
check_asg_status() {
    local asg_name=$1
    
    if [[ -z "$asg_name" ]]; then
        echo "unknown"
        return
    fi
    
    local asg_info=$(aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names "$asg_name" \
        --query "AutoScalingGroups[0].[DesiredCapacity, Instances[?LifecycleState=='InService'].InstanceId | length([]), Instances[?HealthStatus=='Healthy'].InstanceId | length([])]" \
        --output text 2>/dev/null || echo "")
    
    if [[ -n "$asg_info" ]]; then
        echo "$asg_info"
    else
        echo "unknown"
    fi
}

# Check instance refresh status
check_refresh_status() {
    local asg_name=$1
    
    if [[ -z "$asg_name" ]]; then
        return
    fi
    
    local refresh_info=$(aws autoscaling describe-instance-refreshes \
        --auto-scaling-group-name "$asg_name" \
        --query "InstanceRefreshes[?Status=='InProgress'][0].[InstanceRefreshId, PercentageComplete, Status]" \
        --output text 2>/dev/null || echo "")
    
    if [[ -n "$refresh_info" && "$refresh_info" != "None" ]]; then
        echo "$refresh_info"
    fi
}

# Função principal de monitoramento
monitor_services() {
    log_header "Monitor de Serviços AWS"
    
    if [[ -z "$ALB_DNS" ]]; then
        log_error "Stack não encontrada ou ALB não disponível"
        return 1
    fi
    
    log_info "ALB DNS: $ALB_DNS"
    echo ""
    
    # Service Health
    log_info "🔍 Health Check dos Serviços"
    
    local fastapi_health=$(check_service_health "FastAPI" "http://$ALB_DNS/swagger/api/docs")
    local gateway_health=$(check_service_health "Gateway" "http://$ALB_DNS/swagger/gw/api-docs")
    
    if [[ "$fastapi_health" == "healthy" ]]; then
        log_info "  ✅ FastAPI: Saudável"
    else
        log_error "  ❌ FastAPI: Não respondendo"
        if [[ "$ALERTS" == true ]]; then
            log_warn "     URL: http://$ALB_DNS/swagger/api/docs"
        fi
    fi
    
    if [[ "$gateway_health" == "healthy" ]]; then
        log_info "  ✅ Gateway: Saudável"
    else
        log_error "  ❌ Gateway: Não respondendo"
        if [[ "$ALERTS" == true ]]; then
            log_warn "     URL: http://$ALB_DNS/swagger/gw/api-docs"
        fi
    fi
    
    echo ""
    
    # ASG Status
    if [[ "$DETAILED" == true ]]; then
        log_info "🏗️  Status dos Auto Scaling Groups"
        
        if [[ -n "$FASTAPI_ASG" ]]; then
            local fastapi_asg_status=$(check_asg_status "$FASTAPI_ASG")
            local desired=$(echo "$fastapi_asg_status" | cut -f1)
            local inservice=$(echo "$fastapi_asg_status" | cut -f2)
            local healthy=$(echo "$fastapi_asg_status" | cut -f3)
            
            log_info "  FastAPI ASG ($FASTAPI_ASG):"
            log_info "    Desired: $desired, InService: $inservice, Healthy: $healthy"
            
            # Check for instance refresh
            local refresh_status=$(check_refresh_status "$FASTAPI_ASG")
            if [[ -n "$refresh_status" ]]; then
                local refresh_id=$(echo "$refresh_status" | cut -f1)
                local percentage=$(echo "$refresh_status" | cut -f2)
                log_warn "    🔄 Instance Refresh em andamento: $refresh_id ($percentage%)"
            fi
        fi
        
        if [[ -n "$GATEWAY_ASG" ]]; then
            local gateway_asg_status=$(check_asg_status "$GATEWAY_ASG")
            local desired=$(echo "$gateway_asg_status" | cut -f1)
            local inservice=$(echo "$gateway_asg_status" | cut -f2)
            local healthy=$(echo "$gateway_asg_status" | cut -f3)
            
            log_info "  Gateway ASG ($GATEWAY_ASG):"
            log_info "    Desired: $desired, InService: $inservice, Healthy: $healthy"
            
            # Check for instance refresh
            local refresh_status=$(check_refresh_status "$GATEWAY_ASG")
            if [[ -n "$refresh_status" ]]; then
                local refresh_id=$(echo "$refresh_status" | cut -f1)
                local percentage=$(echo "$refresh_status" | cut -f2)
                log_warn "    🔄 Instance Refresh em andamento: $refresh_id ($percentage%)"
            fi
        fi
        
        echo ""
    fi
    
    # Overall Status
    if [[ "$fastapi_health" == "healthy" && "$gateway_health" == "healthy" ]]; then
        log_info "🎉 Todos os serviços estão saudáveis"
        return 0
    else
        log_warn "⚠️  Alguns serviços apresentam problemas"
        if [[ "$ALERTS" == true ]]; then
            log_info "💡 Ações sugeridas:"
            log_info "   - Verificar logs: ./deploy.sh status"
            log_info "   - Restart serviços: ./instance-refresh.sh both"
            log_info "   - Debug instâncias: aws ssm start-session --target <instance-id>"
        fi
        return 1
    fi
}

# Signal handlers
trap 'echo -e "\n${YELLOW}Monitor interrompido pelo usuário${NC}"; exit 0' INT TERM

# Main execution
main() {
    # Verificar dependências
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI não encontrado"
        exit 1
    fi
    
    if ! command -v curl &> /dev/null; then
        log_error "curl não encontrado"
        exit 1
    fi
    
    # Obter informações da stack
    log_info "Obtendo informações da stack..."
    get_stack_info
    
    if [[ "$CONTINUOUS" == true ]]; then
        log_info "Iniciando monitoramento contínuo (intervalo: ${INTERVAL}s)"
        log_info "Pressione Ctrl+C para parar"
        echo ""
        
        while true; do
            monitor_services
            echo ""
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Próxima verificação em ${INTERVAL}s..."
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo ""
            sleep "$INTERVAL"
        done
    else
        monitor_services
    fi
}

main "$@"
