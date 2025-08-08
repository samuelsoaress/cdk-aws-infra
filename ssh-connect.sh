#!/bin/bash

# Script para facilitar SSH nas instâncias
# Uso: ./ssh-connect.sh [fastapi|gateway]

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

# Obter instâncias de um ASG
get_asg_instances() {
    local asg_name=$1
    aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names "$asg_name" \
        --query "AutoScalingGroups[0].Instances[?LifecycleState=='InService'].[InstanceId,PublicIpAddress]" \
        --output text 2>/dev/null || echo ""
}

# Verificar se key existe
check_ssh_key() {
    local key_name=$1
    local key_file="$key_name.pem"
    
    if [[ ! -f "$key_file" ]]; then
        log_warn "Arquivo de chave SSH '$key_file' não encontrado."
        log_info "📋 Para obter a chave privada:"
        log_info "1. Vá para AWS Console > EC2 > Key Pairs"
        log_info "2. Encontre a chave '$key_name'"
        log_info "3. Clique em 'Actions' > 'Get private key'"
        log_info "4. Copie o conteúdo e salve como '$key_file'"
        log_info "5. Execute: chmod 400 $key_file"
        log_info ""
        log_info "🔄 Ou via AWS CLI:"
        log_info "aws ec2 describe-key-pairs --key-names $key_name --query 'KeyPairs[0].KeyMaterial' --output text > $key_file"
        log_info "chmod 400 $key_file"
        return 1
    fi
    
    # Verificar permissões
    local perms=$(stat -c %a "$key_file" 2>/dev/null || stat -f %A "$key_file" 2>/dev/null || echo "")
    if [[ "$perms" != "400" ]]; then
        log_warn "Corrigindo permissões da chave SSH..."
        chmod 400 "$key_file"
    fi
    
    return 0
}

# Conectar via SSH
ssh_connect() {
    local service_name=$1
    
    log_info "=== SSH Connect para $service_name ==="
    
    # Obter nome do ASG
    local asg_name=""
    case "$service_name" in
        "fastapi")
            asg_name=$(get_stack_output "FastAPIASGName")
            ;;
        "gateway")
            asg_name=$(get_stack_output "GatewayASGName")
            ;;
        *)
            log_error "Serviço inválido. Use: fastapi ou gateway"
            exit 1
            ;;
    esac
    
    if [[ -z "$asg_name" ]]; then
        log_error "ASG não encontrado para $service_name. Verifique se a stack foi deployada."
        exit 1
    fi
    
    log_info "ASG: $asg_name"
    
    # Obter instâncias
    local instances=$(get_asg_instances "$asg_name")
    if [[ -z "$instances" ]]; then
        log_error "Nenhuma instância ativa encontrada no ASG $asg_name"
        exit 1
    fi
    
    # Listar instâncias disponíveis
    log_info "Instâncias disponíveis:"
    local count=1
    declare -a instance_list
    while IFS=$'\t' read -r instance_id public_ip; do
        if [[ -n "$instance_id" ]]; then
            echo "  $count) $instance_id ($public_ip)"
            instance_list[$count]="$instance_id:$public_ip"
            count=$((count + 1))
        fi
    done <<< "$instances"
    
    if [[ ${#instance_list[@]} -eq 0 ]]; then
        log_error "Nenhuma instância encontrada"
        exit 1
    fi
    
    # Selecionar instância (se mais de uma)
    local selected_instance=""
    if [[ ${#instance_list[@]} -eq 1 ]]; then
        selected_instance="${instance_list[1]}"
    else
        echo -n "Selecione a instância (1-${#instance_list[@]}): "
        read -r choice
        if [[ "$choice" -ge 1 && "$choice" -le ${#instance_list[@]} ]]; then
            selected_instance="${instance_list[$choice]}"
        else
            log_error "Seleção inválida"
            exit 1
        fi
    fi
    
    # Extrair dados da instância
    local instance_id=$(echo "$selected_instance" | cut -d: -f1)
    local public_ip=$(echo "$selected_instance" | cut -d: -f2)
    
    if [[ -z "$public_ip" || "$public_ip" == "None" ]]; then
        log_error "Instância não tem IP público. Verifique se está em subnet pública."
        exit 1
    fi
    
    log_info "Conectando na instância: $instance_id ($public_ip)"
    
    # Verificar chave SSH
    local key_name=$(get_stack_output "SSHKeyName")
    if [[ -z "$key_name" ]]; then
        log_error "Nome da chave SSH não encontrado nos outputs da stack"
        exit 1
    fi
    
    if ! check_ssh_key "$key_name"; then
        exit 1
    fi
    
    # Comandos úteis para o usuário
    log_info "🔍 Comandos úteis após conectar:"
    echo "  # Ver status dos containers:"
    if [[ "$service_name" == "fastapi" ]]; then
        echo "  cd /opt/fastapi && docker-compose ps"
        echo "  # Ver logs:"
        echo "  docker-compose logs -f"
        echo "  # Testar endpoint:"
        echo "  curl http://localhost:8000/health"
        echo "  curl http://localhost:8000/docs"
    else
        echo "  cd /opt/gateway && docker-compose ps"
        echo "  # Ver logs:"
        echo "  docker-compose logs -f"
        echo "  # Testar endpoint:"
        echo "  curl http://localhost:3000/health"
        echo "  curl http://localhost:3000/api-docs"
    fi
    echo "  # Ver logs do sistema:"
    echo "  sudo journalctl -u cloud-init-output.log -f"
    echo "  # Ver user data execution:"
    echo "  sudo cat /var/log/cloud-init-output.log"
    echo ""
    
    # Conectar
    log_info "🚀 Conectando via SSH..."
    ssh -i "$key_name.pem" -o StrictHostKeyChecking=no ec2-user@"$public_ip"
}

# Listar todas as instâncias
list_instances() {
    log_info "=== Instâncias Disponíveis ==="
    
    local fastapi_asg=$(get_stack_output "FastAPIASGName")
    local gateway_asg=$(get_stack_output "GatewayASGName")
    
    if [[ -n "$fastapi_asg" ]]; then
        log_info "FastAPI Instances ($fastapi_asg):"
        local instances=$(get_asg_instances "$fastapi_asg")
        if [[ -n "$instances" ]]; then
            while IFS=$'\t' read -r instance_id public_ip; do
                if [[ -n "$instance_id" ]]; then
                    echo "  $instance_id - $public_ip"
                fi
            done <<< "$instances"
        else
            echo "  Nenhuma instância ativa"
        fi
        echo ""
    fi
    
    if [[ -n "$gateway_asg" ]]; then
        log_info "Gateway Instances ($gateway_asg):"
        local instances=$(get_asg_instances "$gateway_asg")
        if [[ -n "$instances" ]]; then
            while IFS=$'\t' read -r instance_id public_ip; do
                if [[ -n "$instance_id" ]]; then
                    echo "  $instance_id - $public_ip"
                fi
            done <<< "$instances"
        else
            echo "  Nenhuma instância ativa"
        fi
        echo ""
    fi
}

# Função principal
main() {
    local command=${1:-"help"}
    
    case "$command" in
        "fastapi")
            ssh_connect "fastapi"
            ;;
        "gateway")
            ssh_connect "gateway"
            ;;
        "list")
            list_instances
            ;;
        "help"|"--help"|"-h")
            echo "Uso: $0 <command>"
            echo ""
            echo "Comandos:"
            echo "  fastapi    - Conectar na instância FastAPI"
            echo "  gateway    - Conectar na instância Gateway"
            echo "  list       - Listar todas as instâncias disponíveis"
            echo "  help       - Mostrar esta ajuda"
            echo ""
            echo "Exemplos:"
            echo "  $0 fastapi     # SSH na instância FastAPI"
            echo "  $0 gateway     # SSH na instância Gateway"
            echo "  $0 list        # Listar instâncias disponíveis"
            echo ""
            echo "📋 Pré-requisitos:"
            echo "  1. AWS CLI configurado"
            echo "  2. Stack InfrastructureStack deployada"
            echo "  3. Arquivo .pem da chave SSH baixado do console AWS"
            ;;
        *)
            log_error "Comando inválido. Use: fastapi, gateway, list, ou help"
            exit 1
            ;;
    esac
}

main "$@"
