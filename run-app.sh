#!/bin/bash

# Script wrapper para executar a aplicação CDK
# Funciona tanto localmente (com venv) quanto no CI/CD (com deps globais)

if [ -f ".venv/bin/activate" ]; then
    # Ambiente local - usar virtual environment
    source .venv/bin/activate
    python app.py
elif command -v python3 &> /dev/null; then
    # CI/CD ou ambiente sem venv - usar python3 global
    python3 app.py
else
    # Fallback para python
    python app.py
fi
