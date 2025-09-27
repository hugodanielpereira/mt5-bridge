# MT5-Bridge

Bridge leve em **FastAPI** para interagir com o MetaTrader 5 via HTTP/JSON.  
Permite enviar ordens, consultar preços, históricos e gerir posições de forma programática.

## 🚀 Requisitos

- **Python 3.9+**
- MT5 instalado e ativo (conta DEMO ou LIVE)
- Virtualenv ativo (`.venv`) recomendado

Instalação inicial:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## ⚙️ Configuração

Cria um ficheiro .env com as credenciais:
```bash
MT5_LOGIN=12345678
MT5_PASSWORD=senha_secreta
MT5_SERVER=Broker-Demo
HOST=0.0.0.0
PORT=5005
```

## ▶️ Execução

Linux / macOS
```bash
./run.sh
```
Windows
```bash
.\run.bat
```
O bridge ficará disponível em:
```bash
http://127.0.0.1:5005
```
Testa se está no ar:
```bash
curl http://127.0.0.1:5005/ping
```

## 🧩 Endpoints
	•	GET /ping → teste de conectividade
	•	GET /symbols → lista de símbolos disponíveis
	•	POST /order → envia ordem (JSON payload)
	•	GET /positions → lista posições abertas

## 🛠 Desenvolvimento
	•	app.py contém a lógica principal do FastAPI
	•	Adiciona endpoints conforme precisares (ex.: histórico de OHLCV, trailing stop, etc.)
	•	Usa uvicorn com --reload para hot-reload em dev.
---

Desta forma temos:
- **`run.bat`** para Windows  
- **`run.sh`** para Linux/macOS  
- **`.gitignore`** para manter o repositório limpo  
- **`README.md`** explicativo para quem clonar o projeto 