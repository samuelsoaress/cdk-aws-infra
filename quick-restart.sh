#!/bin/bash

# Script para restart rápido dos containers (sem Instance Refresh)
# Uso: ./quick-restart.sh [fastapi|gateway|both]

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

# Restart containers via SSM
restart_service_containers() {
    local service_name=$1
    local asg_name=$2
    
    log_info "Fazendo restart dos containers $service_name..."
    
    # Obter instâncias do ASG
    local instances=$(aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names "$asg_name" \
        --query "AutoScalingGroups[0].Instances[?LifecycleState=='InService'].InstanceId" \
        --output text)
    
    if [[ -z "$instances" ]]; then
        log_error "Nenhuma instância ativa encontrada no ASG $asg_name"
        return 1
    fi
    
    log_info "Instâncias encontradas: $instances"
    
    # Comando para restart dos containers
    local restart_command=""
    case "$service_name" in
        "FastAPI")
            restart_command="cd /opt/fastapi && docker-compose down && sleep 5 && docker-compose up -d && docker-compose logs --tail=20"
            ;;
        "Gateway")
            restart_command="cd /opt/gateway && docker-compose down && sleep 5 && docker-compose up -d && docker-compose logs --tail=20"
            ;;
    esac
    
    # Executar restart em cada instância
    for instance_id in $instances; do
        log_info "Restartando containers na instância $instance_id..."
        
        local command_id=$(aws ssm send-command \
            --instance-ids "$instance_id" \
            --document-name "AWS-RunShellScript" \
            --parameters "commands=[\"$restart_command\"]" \
            --query "Command.CommandId" \
            --output text)
        
        log_info "Comando SSM enviado: $command_id"
        
        # Aguardar execução
        log_info "Aguardando execução do restart..."
        sleep 10
        
        # Verificar status do comando
        local status=$(aws ssm get-command-invocation \
            --command-id "$command_id" \
            --instance-id "$instance_id" \
            --query "Status" \
            --output text 2>/dev/null || echo "InProgress")
        
        local attempts=0
        while [[ "$status" == "InProgress" && $attempts -lt 30 ]]; do
            sleep 5
            attempts=$((attempts + 1))
            status=$(aws ssm get-command-invocation \
                --command-id "$command_id" \
                --instance-id "$instance_id" \
                --query "Status" \
                --output text 2>/dev/null || echo "InProgress")
        done
        
        if [[ "$status" == "Success" ]]; then
            log_info "✅ Restart concluído com sucesso na instância $instance_id"
        else
            log_warn "⚠️  Status do restart: $status na instância $instance_id"
        fi
    done
    
    # Aguardar containers subirem
    log_info "Aguardando 30s para containers iniciarem..."
    sleep 30
}

# Verificar health dos serviços
check_service_health() {
    local service_name=$1
    local endpoint_path=$2
    
    log_info "Verificando health do $service_name..."
    
    local alb_dns=$(get_stack_output "SwaggerAlbDnsName")
    if [[ -n "$alb_dns" ]]; then
        local health_url="http://$alb_dns$endpoint_path"
        if curl -s -f "$health_url" > /dev/null; then
            log_info "✅ $service_name está saudável (endpoint acessível)"
        else
            log_warn "⚠️  $service_name pode não estar respondendo corretamente"
        fi
    fi
}

# Função principal
main() {
    local target=${1:-"both"}
    
    log_info "=== Quick Restart Manager ==="
    log_info "Target: $target"
    log_info "⚡ Método: Restart de containers (SEM Instance Refresh)"
    
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
            restart_service_containers "FastAPI" "$fastapi_asg"
            check_service_health "FastAPI" "/swagger/api/docs"
            ;;
        "gateway")
            restart_service_containers "Gateway" "$gateway_asg"
            check_service_health "Gateway" "/swagger/gw/api-docs"
            ;;
        "both")
            log_info "Restartando ambos os serviços..."
            restart_service_containers "FastAPI" "$fastapi_asg"
            log_info "Aguardando 10s antes do próximo restart..."
            sleep 10
            restart_service_containers "Gateway" "$gateway_asg"
            
            log_info "Verificando health de ambos os serviços..."
            check_service_health "FastAPI" "/swagger/api/docs"
            check_service_health "Gateway" "/swagger/gw/api-docs"
            ;;
        *)
            log_error "Target inválido. Use: fastapi, gateway, ou both"
            exit 1
            ;;
    esac
    
    log_info "🎉 Quick restart completado!"
    log_info "⏱️  Tempo total: ~2-3 minutos (vs 10-15 min do Instance Refresh)"
}

# Verificar argumentos
if [[ "$1" == "--help" || "$1" == "-h" ]]; then
    echo "Uso: $0 [fastapi|gateway|both]"
    echo ""
    echo "Quick Restart - Reinicia apenas os containers (sem trocar instâncias)"
    echo ""
    echo "Exemplos:"
    echo "  $0 fastapi    # Restart apenas FastAPI containers"
    echo "  $0 gateway    # Restart apenas Gateway containers" 
    echo "  $0 both       # Restart ambos os containers"
    echo ""
    echo "⚡ Vantagens:"
    echo "  - Muito mais rápido (2-3 min vs 10-15 min)"
    echo "  - Não troca instâncias EC2"
    echo "  - Aplica mudanças de configuração do S3"
    echo ""
    echo "⚠️  Limitações:"
    echo "  - Não aplica mudanças no Launch Template"
    echo "  - Não atualiza AMI ou tipo de instância"
    exit 0
fi

main "$@"
