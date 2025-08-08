
ACTION=$1

if [ "$ACTION" = "stop" ]; then
    echo "Parando instâncias..."
    aws ec2 stop-instances --instance-ids $(aws cloudformation describe-stack-resources --stack-name InfrastructureStack --query 'StackResources[?ResourceType==`AWS::EC2::Instance`].PhysicalResourceId' --output text)
    echo "Instâncias paradas!"
elif [ "$ACTION" = "start" ]; then
    echo "Iniciando instâncias..."
    aws ec2 start-instances --instance-ids $(aws cloudformation describe-stack-resources --stack-name InfrastructureStack --query 'StackResources[?ResourceType==`AWS::EC2::Instance`].PhysicalResourceId' --output text)
    echo "Instâncias iniciadas!"
else
    echo "Uso: ./manage-instances.sh [stop|start]"
fi