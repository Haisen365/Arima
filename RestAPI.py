"""
Binary Elite Bot REST API
API completa para controle e monitoramento do bot de trading
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any, Union
from datetime import datetime, timedelta
import asyncio
import json
import threading
import uvicorn
import os
import sys
from enum import Enum
import logging

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Importações do bot (assumindo que o arima_v2.py está no mesmo diretório)
try:
    from arima_v2 import *
except ImportError as e:
    logger.error(f"Erro ao importar arima_v2: {e}")
    # Fallback para desenvolvimento sem o arquivo original
    pass

# ================================
# MODELOS PYDANTIC (DTOs)
# ================================

class BotStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"

class SignalType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"

class GerenciamentoType(str, Enum):
    MASANIELLO = "Masaniello"
    CICLOS = "Ciclos"

class ModoEntrada(str, Enum):
    TEMPO_FIXO = "tempo_fixo"
    FIM_DA_VELA = "fim_da_vela"

class ModoGale(str, Enum):
    NORMAL = "normal"
    ZIGZAG = "zigzag"

# Modelos de configuração
class TokenConfig(BaseModel):
    demo_token: str = Field(..., description="Token da conta demo")
    real_token: str = Field(..., description="Token da conta real")

class MasanielloConfig(BaseModel):
    risco: float = Field(35.0, ge=0, description="Valor do risco em dólares")
    total_operations: int = Field(12, ge=1, description="Total de operações")
    wins: int = Field(3, ge=1, description="Número de wins necessários")
    payout: float = Field(1.94, ge=1.0, description="Payout da corretora")
    min_entry: float = Field(0.35, ge=0.1, description="Entrada mínima")
    numero_gales: int = Field(1, ge=0, le=5, description="Quantidade de gales")
    tipo: int = Field(1, ge=0, le=1, description="0=Progressivo, 1=Normal")
    style: str = Field("Normal", description="Estilo: Normal, Composto, Conservador")

class CiclosConfig(BaseModel):
    matriz_ciclos: List[List[float]] = Field(..., description="Matriz 10x10 dos ciclos")
    linha_atual: int = Field(0, ge=0, le=9, description="Linha atual")
    coluna_atual: int = Field(0, ge=0, le=9, description="Coluna atual")
    modo_ataque_defesa: bool = Field(False, description="Modo ataque/defesa")
    alvo_lucro: float = Field(0.5, ge=0, description="Alvo de lucro por ciclo")

class EstrategiasConfig(BaseModel):
    numero_confluencias: int = Field(1, ge=1, le=10, description="Número de confluências")
    retracao_value: bool = Field(False, description="Ativar estratégia de retração")
    reversao_value: bool = Field(False, description="Ativar estratégia de reversão")
    fluxo_active: bool = Field(False, description="Ativar estratégia de fluxo")
    ml_strategy_active: bool = Field(False, description="Ativar estratégia ML")
    abr_strategy_active: bool = Field(False, description="Ativar estratégia ABR")
    price_action_active: bool = Field(False, description="Ativar Price Action")
    volume_profile_active: bool = Field(False, description="Ativar Volume Profile")
    antiloss_ativado: bool = Field(False, description="Ativar antiloss")
    required_losses: int = Field(2, ge=1, le=10, description="Quantidade de losses para antiloss")
    modo_antiloss: str = Field("global", description="Modo antiloss: global ou restrito")

class ABRConfig(BaseModel):
    sequencia_minima: int = Field(7, ge=1, description="Sequência mínima de velas")
    sequencia_maxima: int = Field(13, ge=1, description="Sequência máxima de velas")
    winrate: int = Field(60, ge=0, le=100, description="Winrate mínimo em %")

class StopConfig(BaseModel):
    stop_win: float = Field(500.0, ge=0, description="Stop Win em dólares")
    stop_loss: float = Field(300.0, ge=0, description="Stop Loss em dólares")

class ModoEntradaConfig(BaseModel):
    tipo: ModoEntrada = Field(ModoEntrada.TEMPO_FIXO, description="Tipo de entrada")
    default_expiration: int = Field(1, ge=1, le=30, description="Expiração em minutos")
    fim_da_vela_time: str = Field("M1", description="Timeframe para fim da vela")
    modo_gale: ModoGale = Field(ModoGale.NORMAL, description="Modo de gale")

class SymbolsConfig(BaseModel):
    simbolos_ativos: Dict[str, bool] = Field(..., description="Símbolos ativos/inativos")

class TelegramConfig(BaseModel):
    chat_id: str = Field("", description="Chat ID do Telegram")
    telegram_ativado: bool = Field(False, description="Telegram ativado")
    bot_token: str = Field("", description="Token do bot Telegram")

# Modelo de resposta de transação
class Transaction(BaseModel):
    hora_abertura: datetime
    hora_fechamento: Optional[datetime] = None
    tipo_sinal: str
    entrada: float
    par: str
    gales: int
    direcao: str
    duracao: str
    resultado: Optional[str] = None
    profit: Optional[float] = None
    comentario: str = ""
    contract_id: Optional[str] = None

# Modelo de estatísticas
class Statistics(BaseModel):
    total_wins: int
    total_losses: int
    total_operacoes: int
    winrate: float
    lucro_total: float
    saldo_atual: float
    saldo_inicial: float

# Modelo de status do bot
class BotStatusResponse(BaseModel):
    status: BotStatus
    is_running: bool
    should_send_orders: bool
    saldo_atual: float
    lucro_total: float
    total_wins: int
    total_losses: int
    winrate: float
    ultima_atualizacao: datetime

# Modelo de configuração completa
class BotConfig(BaseModel):
    gerenciamento: GerenciamentoType = Field(GerenciamentoType.MASANIELLO)
    masaniello: MasanielloConfig = Field(default_factory=MasanielloConfig)
    ciclos: CiclosConfig = Field(default_factory=lambda: CiclosConfig(matriz_ciclos=[[0.0]*10 for _ in range(10)]))
    estrategias: EstrategiasConfig = Field(default_factory=EstrategiasConfig)
    abr: ABRConfig = Field(default_factory=ABRConfig)
    stops: StopConfig = Field(default_factory=StopConfig)
    modo_entrada: ModoEntradaConfig = Field(default_factory=ModoEntradaConfig)
    symbols: SymbolsConfig = Field(default_factory=lambda: SymbolsConfig(simbolos_ativos={symbol: True for symbol in symbols}))
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

# ================================
# INICIALIZAÇÃO DA API
# ================================

app = FastAPI(
    title="Binary Elite Bot API",
    description="API REST completa para controle do Binary Elite Trading Bot",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configuração CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Em produção, especifique os domínios permitidos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Armazenamento de conexões WebSocket ativas
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except:
                pass

manager = ConnectionManager()

# ================================
# ENDPOINTS DE CONFIGURAÇÃO
# ================================

@app.post("/api/v1/config/tokens")
async def set_tokens(config: TokenConfig):
    """Configura os tokens de acesso"""
    try:
        global demo_token, real_token
        demo_token = config.demo_token
        real_token = config.real_token
        
        # Salva os tokens
        salvar_tokens_api(config.demo_token, config.real_token)
        
        return {"message": "Tokens configurados com sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao configurar tokens: {str(e)}")

@app.get("/api/v1/config")
async def get_config() -> BotConfig:
    """Retorna a configuração atual completa do bot"""
    try:
        # Coleta todas as configurações globais
        config = BotConfig(
            gerenciamento=GerenciamentoType.MASANIELLO if gerenciamento_ativo == "Masaniello" else GerenciamentoType.CICLOS,
            masaniello=MasanielloConfig(
                risco=risco,
                total_operations=total_operations,
                wins=wins,
                payout=payout,
                min_entry=min_entry,
                numero_gales=NumeroDeGales,
                tipo=tipo,
                style=style
            ),
            ciclos=CiclosConfig(
                matriz_ciclos=configuracoes_gerenciamentos["Ciclos"]["matriz_ciclos"],
                linha_atual=configuracoes_gerenciamentos["Ciclos"]["linha_atual"],
                coluna_atual=configuracoes_gerenciamentos["Ciclos"]["coluna_atual"],
                modo_ataque_defesa=configuracoes_gerenciamentos["Ciclos"]["modo_ataque_defesa"],
                alvo_lucro=configuracoes_gerenciamentos["Ciclos"]["alvo_lucro"]
            ),
            estrategias=EstrategiasConfig(
                numero_confluencias=numero_confluencias,
                retracao_value=retracao_value,
                reversao_value=reversao_value,
                fluxo_active=fluxo_active,
                ml_strategy_active=ml_strategy_active,
                abr_strategy_active=abr_strategy_active,
                price_action_active=price_action_active,
                volume_profile_active=volume_profile_active,
                antiloss_ativado=antiloss_ativado,
                required_losses=required_losses,
                modo_antiloss=modo_antiloss
            ),
            abr=ABRConfig(
                sequencia_minima=SequenciaMinima,
                sequencia_maxima=SequenciaMaxima,
                winrate=Winrate
            ),
            stops=StopConfig(
                stop_win=STOP_WIN,
                stop_loss=STOP_LOSS
            ),
            modo_entrada=ModoEntradaConfig(
                tipo=ModoEntrada.TEMPO_FIXO if modo_entrada == "tempo_fixo" else ModoEntrada.FIM_DA_VELA,
                default_expiration=default_expiration,
                fim_da_vela_time=fim_da_vela_time,
                modo_gale=ModoGale.NORMAL if modo_gale == "normal" else ModoGale.ZIGZAG
            ),
            symbols=SymbolsConfig(simbolos_ativos=simbolos_ativos),
            telegram=TelegramConfig(
                chat_id=chat_id_value,
                telegram_ativado=telegram_ativado,
                bot_token=bot_token
            )
        )
        return config
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter configuração: {str(e)}")

@app.put("/api/v1/config")
async def update_config(config: BotConfig):
    """Atualiza a configuração completa do bot"""
    try:
        # Atualiza variáveis globais
        global gerenciamento_ativo, risco, total_operations, wins, payout, min_entry
        global NumeroDeGales, tipo, style, numero_confluencias, retracao_value
        global reversao_value, fluxo_active, ml_strategy_active, abr_strategy_active
        global price_action_active, volume_profile_active, antiloss_ativado
        global required_losses, modo_antiloss, SequenciaMinima, SequenciaMaxima
        global Winrate, STOP_WIN, STOP_LOSS, modo_entrada, default_expiration
        global fim_da_vela_time, modo_gale, simbolos_ativos, chat_id_value
        global telegram_ativado, bot_token

        # Atualiza gerenciamento
        gerenciamento_ativo = config.gerenciamento.value

        # Atualiza Masaniello
        risco = config.masaniello.risco
        total_operations = config.masaniello.total_operations
        wins = config.masaniello.wins
        payout = config.masaniello.payout
        min_entry = config.masaniello.min_entry
        NumeroDeGales = config.masaniello.numero_gales
        tipo = config.masaniello.tipo
        style = config.masaniello.style

        # Atualiza Ciclos
        configuracoes_gerenciamentos["Ciclos"].update({
            "matriz_ciclos": config.ciclos.matriz_ciclos,
            "linha_atual": config.ciclos.linha_atual,
            "coluna_atual": config.ciclos.coluna_atual,
            "modo_ataque_defesa": config.ciclos.modo_ataque_defesa,
            "alvo_lucro": config.ciclos.alvo_lucro
        })

        # Atualiza Estratégias
        numero_confluencias = config.estrategias.numero_confluencias
        retracao_value = config.estrategias.retracao_value
        reversao_value = config.estrategias.reversao_value
        fluxo_active = config.estrategias.fluxo_active
        ml_strategy_active = config.estrategias.ml_strategy_active
        abr_strategy_active = config.estrategias.abr_strategy_active
        price_action_active = config.estrategias.price_action_active
        volume_profile_active = config.estrategias.volume_profile_active
        antiloss_ativado = config.estrategias.antiloss_ativado
        required_losses = config.estrategias.required_losses
        modo_antiloss = config.estrategias.modo_antiloss

        # Atualiza ABR
        SequenciaMinima = config.abr.sequencia_minima
        SequenciaMaxima = config.abr.sequencia_maxima
        Winrate = config.abr.winrate

        # Atualiza Stops
        STOP_WIN = config.stops.stop_win
        STOP_LOSS = config.stops.stop_loss

        # Atualiza Modo de Entrada
        modo_entrada = config.modo_entrada.tipo.value
        default_expiration = config.modo_entrada.default_expiration
        fim_da_vela_time = config.modo_entrada.fim_da_vela_time
        modo_gale = config.modo_entrada.modo_gale.value

        # Atualiza Símbolos
        simbolos_ativos.update(config.symbols.simbolos_ativos)

        # Atualiza Telegram
        chat_id_value = config.telegram.chat_id
        telegram_ativado = config.telegram.telegram_ativado
        bot_token = config.telegram.bot_token

        # Salva as configurações
        save_configurations()
        salvar_configuracoes_gerenciamento()

        return {"message": "Configuração atualizada com sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar configuração: {str(e)}")

# ================================
# ENDPOINTS DE CONTROLE DO BOT
# ================================

@app.post("/api/v1/bot/start")
async def start_bot_api(background_tasks: BackgroundTasks):
    """Inicia o bot"""
    try:
        global is_running, should_send_orders
        
        if is_running and should_send_orders:
            return {"message": "Bot já está em execução", "status": "running"}

        # Inicia o bot em background
        background_tasks.add_task(start_bot_background)
        
        return {"message": "Bot iniciado com sucesso", "status": "starting"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao iniciar bot: {str(e)}")

@app.post("/api/v1/bot/stop")
async def stop_bot_api():
    """Para o bot"""
    try:
        global is_running, should_send_orders, stop_event
        
        if not is_running and not should_send_orders:
            return {"message": "Bot já está parado", "status": "stopped"}

        # Para o bot
        should_send_orders = False
        is_running = False
        stop_event.set()
        
        return {"message": "Bot parado com sucesso", "status": "stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao parar bot: {str(e)}")

@app.post("/api/v1/bot/pause")
async def pause_bot_api():
    """Pausa o bot"""
    try:
        global should_send_orders
        
        if not should_send_orders:
            return {"message": "Bot já está pausado", "status": "paused"}

        should_send_orders = False
        
        return {"message": "Bot pausado com sucesso", "status": "paused"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao pausar bot: {str(e)}")

@app.post("/api/v1/bot/resume")
async def resume_bot_api():
    """Resume o bot"""
    try:
        global should_send_orders, is_running
        
        if should_send_orders:
            return {"message": "Bot já está em execução", "status": "running"}

        if not is_running:
            raise HTTPException(status_code=400, detail="Bot não foi iniciado. Use /start primeiro")

        should_send_orders = True
        
        return {"message": "Bot resumido com sucesso", "status": "running"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao resumir bot: {str(e)}")

@app.get("/api/v1/bot/status")
async def get_bot_status() -> BotStatusResponse:
    """Retorna o status atual do bot"""
    try:
        total_operacoes = total_wins + total_losses
        winrate = (total_wins / total_operacoes * 100) if total_operacoes > 0 else 0
        
        # Determina o status
        if not is_running:
            status = BotStatus.STOPPED
        elif not should_send_orders:
            status = BotStatus.PAUSED
        else:
            status = BotStatus.RUNNING

        return BotStatusResponse(
            status=status,
            is_running=is_running,
            should_send_orders=should_send_orders,
            saldo_atual=saldo_atual,
            lucro_total=lucro_total,
            total_wins=total_wins,
            total_losses=total_losses,
            winrate=winrate,
            ultima_atualizacao=datetime.now()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter status: {str(e)}")

@app.post("/api/v1/bot/reset")
async def reset_bot_api():
    """Reseta o bot completamente"""
    try:
        reset_bot()
        return {"message": "Bot resetado com sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao resetar bot: {str(e)}")

@app.post("/api/v1/bot/reset-profit")
async def reset_profit_api():
    """Reseta apenas o lucro"""
    try:
        resetarlucro()
        return {"message": "Lucro resetado com sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao resetar lucro: {str(e)}")

# ================================
# ENDPOINTS DE TRANSAÇÕES
# ================================

@app.get("/api/v1/transactions")
async def get_transactions() -> List[Transaction]:
    """Retorna todas as transações"""
    try:
        transactions_list = []
        for t in statistics_transactions:
            transaction = Transaction(
                hora_abertura=t.get("Hora de Abertura"),
                hora_fechamento=t.get("Hora de Fechamento"),
                tipo_sinal=t.get("Tipo Sinal", ""),
                entrada=float(t.get("Entrada", 0)),
                par=t.get("Par", ""),
                gales=int(t.get("Gales", 0)),
                direcao=t.get("Direção", ""),
                duracao=t.get("Duração", ""),
                resultado=t.get("W/L"),
                profit=float(t.get("Profit", 0)),
                comentario=t.get("Comentário", ""),
                contract_id=t.get("Contract_ID")
            )
            transactions_list.append(transaction)
        
        return transactions_list
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter transações: {str(e)}")

@app.delete("/api/v1/transactions")
async def clear_transactions():
    """Limpa o histórico de transações"""
    try:
        clear_transactions_history()
        return {"message": "Histórico de transações limpo com sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao limpar transações: {str(e)}")

@app.get("/api/v1/statistics")
async def get_statistics() -> Statistics:
    """Retorna estatísticas gerais"""
    try:
        total_operacoes = total_wins + total_losses
        winrate = (total_wins / total_operacoes * 100) if total_operacoes > 0 else 0
        
        return Statistics(
            total_wins=total_wins,
            total_losses=total_losses,
            total_operacoes=total_operacoes,
            winrate=winrate,
            lucro_total=lucro_total,
            saldo_atual=saldo_atual,
            saldo_inicial=initial_balance
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter estatísticas: {str(e)}")

# ================================
# ENDPOINTS DE SÍMBOLOS
# ================================

@app.get("/api/v1/symbols")
async def get_symbols():
    """Retorna todos os símbolos disponíveis"""
    try:
        symbols_info = []
        for symbol in symbols:
            symbols_info.append({
                "symbol": symbol,
                "display_name": get_display_name(symbol),
                "active": simbolos_ativos.get(symbol, True)
            })
        return symbols_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter símbolos: {str(e)}")

@app.put("/api/v1/symbols/{symbol}/toggle")
async def toggle_symbol_api(symbol: str):
    """Ativa/desativa um símbolo"""
    try:
        if symbol not in symbols:
            raise HTTPException(status_code=404, detail="Símbolo não encontrado")
        
        simbolos_ativos[symbol] = not simbolos_ativos.get(symbol, True)
        save_configurations()
        
        return {
            "symbol": symbol,
            "active": simbolos_ativos[symbol],
            "message": f"Símbolo {symbol} {'ativado' if simbolos_ativos[symbol] else 'desativado'}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao alternar símbolo: {str(e)}")

# ================================
# ENDPOINTS DE VELAS
# ================================

@app.get("/api/v1/candles/{symbol}")
async def get_candles(symbol: str, limit: int = 100):
    """Retorna velas de um símbolo"""
    try:
        if symbol not in symbols:
            raise HTTPException(status_code=404, detail="Símbolo não encontrado")
        
        if symbol not in velas:
            return {"symbol": symbol, "candles": [], "count": 0}
        
        candles_data = list(velas[symbol])[-limit:]
        
        return {
            "symbol": symbol,
            "candles": candles_data,
            "count": len(candles_data)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter velas: {str(e)}")

# ================================
# ENDPOINTS DE SINAIS
# ================================

@app.post("/api/v1/signals/manual")
async def send_manual_signal(symbol: str, direction: SignalType, duration: int = 60):
    """Envia um sinal manual"""
    try:
        if symbol not in symbols:
            raise HTTPException(status_code=404, detail="Símbolo não encontrado")
        
        # Aqui você pode implementar a lógica para enviar sinal manual
        # Por exemplo, adicionando à fila de sinais pending
        
        return {
            "message": "Sinal manual enviado",
            "symbol": symbol,
            "direction": direction.value,
            "duration": duration
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao enviar sinal: {str(e)}")

# ================================
# WEBSOCKET PARA ATUALIZAÇÕES EM TEMPO REAL
# ================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Envia status atual a cada 5 segundos
            status = await get_bot_status()
            await websocket.send_text(json.dumps({
                "type": "status_update",
                "data": status.dict()
            }))
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ================================
# ENDPOINTS DE TESTE
# ================================

@app.get("/api/v1/health")
async def health_check():
    """Verifica se a API está funcionando"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }

@app.get("/api/v1/info")
async def get_info():
    """Retorna informações gerais do bot"""
    try:
        return {
            "bot_name": "Binary Elite",
            "version": CURRENT_VERSION,
            "symbols_count": len(symbols),
            "active_symbols": len([s for s, active in simbolos_ativos.items() if active]),
            "api_connected": api_autorizada if 'api_autorizada' in globals() else False,
            "websocket_connected": websocket_client is not None if 'websocket_client' in globals() else False
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter informações: {str(e)}")

# ================================
# FUNÇÕES AUXILIARES
# ================================

async def start_bot_background():
    """Inicia o bot em background"""
    try:
        await start_bot()
    except Exception as e:
        logger.error(f"Erro ao iniciar bot em background: {e}")

def salvar_tokens_api(demo_token: str, real_token: str):
    """Salva tokens via API"""
    try:
        config_dir = get_config_directory()
        token_file = os.path.join(config_dir, "token.json")
        
        with open(token_file, 'w') as f:
            json.dump({
                "demo_token": demo_token,
                "real_token": real_token,
            }, f, indent=2)
        
        logger.info("Tokens salvos com sucesso via API")
    except Exception as e:
        logger.error(f"Erro ao salvar tokens via API: {e}")
        raise

# ================================
# INICIALIZAÇÃO DO SERVIDOR
# ================================

async def startup_event():
    """Evento de inicialização"""
    logger.info("Iniciando Binary Elite Bot API...")
    
    # Carrega configurações
    try:
        load_all_configurations()
        load_transactions()
        logger.info("Configurações carregadas com sucesso")
    except Exception as e:
        logger.error(f"Erro ao carregar configurações: {e}")

async def shutdown_event():
    """Evento de encerramento"""
    logger.info("Encerrando Binary Elite Bot API...")
    
    # Para o bot se estiver rodando
    global is_running, should_send_orders, stop_event
    should_send_orders = False
    is_running = False
    stop_event.set()
    
    # Salva transações antes de encerrar
    try:
        save_transactions()
        logger.info("Transações salvas com sucesso")
    except Exception as e:
        logger.error(f"Erro ao salvar transações: {e}")

# Registra eventos de startup e shutdown
app.add_event_handler("startup", startup_event)
app.add_event_handler("shutdown", shutdown_event)

# ================================
# ENDPOINTS AVANÇADOS
# ================================

@app.get("/api/v1/presets/cycles")
async def get_cycle_presets():
    """Retorna presets de ciclos disponíveis"""
    try:
        step_presets = criar_ciclos_presets_step()
        normal_presets = criar_ciclos_presets_normal()
        
        return {
            "step_presets": step_presets,
            "normal_presets": normal_presets
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter presets: {str(e)}")

@app.post("/api/v1/presets/cycles/apply")
async def apply_cycle_preset(preset_type: str, group: str, cycle: str):
    """Aplica um preset de ciclo"""
    try:
        if preset_type == "step":
            presets = criar_ciclos_presets_step()
        elif preset_type == "normal":
            presets = criar_ciclos_presets_normal()
        else:
            raise HTTPException(status_code=400, detail="Tipo de preset inválido")
        
        if group not in presets or cycle not in presets[group]:
            raise HTTPException(status_code=404, detail="Preset não encontrado")
        
        matriz_preset = presets[group][cycle]
        
        # Expande matriz para 10x10
        nova_matriz = []
        for linha in matriz_preset:
            nova_linha = list(linha)
            while len(nova_linha) < 10:
                nova_linha.append(0.0)
            nova_matriz.append(nova_linha)
        
        while len(nova_matriz) < 10:
            nova_matriz.append([0.0] * 10)
        
        # Atualiza configuração
        configuracoes_gerenciamentos["Ciclos"]["matriz_ciclos"] = nova_matriz
        salvar_configuracoes_gerenciamento()
        
        return {
            "message": f"Preset {preset_type} {group} {cycle} aplicado com sucesso",
            "matriz": nova_matriz
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao aplicar preset: {str(e)}")

@app.get("/api/v1/analysis/{symbol}")
async def get_symbol_analysis(symbol: str):
    """Retorna análise técnica de um símbolo"""
    try:
        if symbol not in symbols:
            raise HTTPException(status_code=404, detail="Símbolo não encontrado")
        
        if symbol not in velas or len(velas[symbol]) < 100:
            return {"symbol": symbol, "analysis": None, "message": "Dados insuficientes"}
        
        # Executa análise
        indicadores = await analisar_symbol(symbol, velas[symbol], parametros_globais)
        
        if not indicadores:
            return {"symbol": symbol, "analysis": None, "message": "Falha na análise"}
        
        # Análise de sinal
        threshold = numero_confluencias * 1.35
        sinal = analisar_sinal(indicadores, pesos, threshold)
        
        return {
            "symbol": symbol,
            "analysis": {
                "rsi": indicadores.get("rsi"),
                "macd": indicadores.get("macd"),
                "signal": indicadores.get("signal"),
                "stoch_k": indicadores.get("stoch_k"),
                "stoch_d": indicadores.get("stoch_d"),
                "cci": indicadores.get("cci"),
                "adx": indicadores.get("adx"),
                "market_quality": indicadores.get("market_quality"),
                "signal_detected": sinal,
                "timestamp": datetime.now().isoformat()
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro na análise: {str(e)}")

@app.get("/api/v1/telegram/signals")
async def get_pending_telegram_signals():
    """Retorna sinais pendentes do Telegram"""
    try:
        if 'telegram_pending_signals' in globals():
            return {
                "pending_signals": telegram_pending_signals,
                "count": len(telegram_pending_signals)
            }
        return {"pending_signals": [], "count": 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter sinais Telegram: {str(e)}")

@app.delete("/api/v1/telegram/signals")
async def clear_telegram_signals():
    """Limpa sinais pendentes do Telegram"""
    try:
        global telegram_pending_signals
        if 'telegram_pending_signals' in globals():
            telegram_pending_signals.clear()
        return {"message": "Sinais Telegram limpos com sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao limpar sinais Telegram: {str(e)}")

@app.get("/api/v1/ml/status")
async def get_ml_status():
    """Retorna status das estratégias ML"""
    try:
        if not ml_strategy_active:
            return {"ml_active": False, "strategies": {}}
        
        ml_status = {}
        if 'trading_strategies' in globals():
            for symbol, strategy in trading_strategies.items():
                if hasattr(strategy, 'get_stats'):
                    ml_status[symbol] = strategy.get_stats()
                else:
                    ml_status[symbol] = {"status": "initialized"}
        
        return {
            "ml_active": ml_strategy_active,
            "strategies": ml_status,
            "total_strategies": len(ml_status)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter status ML: {str(e)}")

@app.get("/api/v1/abr/status")
async def get_abr_status():
    """Retorna status da estratégia ABR"""
    try:
        if not abr_strategy_active:
            return {"abr_active": False}
        
        if 'abr_strategy' in globals() and abr_strategy:
            status = abr_strategy.get_status()
            return {
                "abr_active": True,
                "config": {
                    "sequencia_minima": SequenciaMinima,
                    "sequencia_maxima": SequenciaMaxima,
                    "winrate_minimo": Winrate
                },
                "status": status
            }
        
        return {"abr_active": True, "status": "initializing"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter status ABR: {str(e)}")

@app.post("/api/v1/abr/reset")
async def reset_abr_strategy():
    """Reinicia a estratégia ABR"""
    try:
        if not abr_strategy_active:
            raise HTTPException(status_code=400, detail="Estratégia ABR não está ativa")
        
        reiniciar_abr_strategy()
        
        return {"message": "Estratégia ABR reiniciada com sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao reiniciar ABR: {str(e)}")

@app.get("/api/v1/account/info")
async def get_account_info():
    """Retorna informações da conta"""
    try:
        account_info = {
            "saldo_atual": saldo_atual,
            "saldo_inicial": initial_balance,
            "lucro_total": lucro_total,
            "api_autorizada": api_autorizada if 'api_autorizada' in globals() else False,
            "modo_conta": "Demo" if 'demo_token' in globals() and api_token == demo_token else "Real"
        }
        
        if 'api' in globals() and api:
            try:
                balance = api.get_balance()
                if balance is not None:
                    account_info["saldo_api"] = float(balance)
            except:
                account_info["saldo_api"] = None
        
        return account_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter info da conta: {str(e)}")

@app.post("/api/v1/account/switch")
async def switch_account(account_type: str):
    """Alterna entre conta Demo e Real"""
    try:
        if account_type not in ["Demo", "Real"]:
            raise HTTPException(status_code=400, detail="Tipo de conta inválido")
        
        global api_token
        if account_type == "Demo":
            api_token = demo_token
        else:
            api_token = real_token
        
        # Reconecta com nova conta
        await switch_account(account_type)
        
        return {
            "message": f"Alternado para conta {account_type} com sucesso",
            "account_type": account_type
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao alternar conta: {str(e)}")

# ================================
# ENDPOINTS DE LOGS E DEBUG
# ================================

@app.get("/api/v1/logs/recent")
async def get_recent_logs(lines: int = 100):
    """Retorna logs recentes"""
    try:
        log_file = "bot_log.txt"
        if not os.path.exists(log_file):
            return {"logs": [], "message": "Arquivo de log não encontrado"}
        
        with open(log_file, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        
        return {
            "logs": [line.strip() for line in recent_lines],
            "total_lines": len(recent_lines),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter logs: {str(e)}")

@app.get("/api/v1/debug/globals")
async def get_debug_globals():
    """Retorna variáveis globais para debug (apenas em desenvolvimento)"""
    try:
        debug_info = {
            "is_running": is_running if 'is_running' in globals() else None,
            "should_send_orders": should_send_orders if 'should_send_orders' in globals() else None,
            "api_autorizada": api_autorizada if 'api_autorizada' in globals() else None,
            "websocket_connected": websocket_client is not None if 'websocket_client' in globals() else None,
            "total_symbols": len(symbols) if 'symbols' in globals() else 0,
            "active_symbols": len([s for s, active in simbolos_ativos.items() if active]) if 'simbolos_ativos' in globals() else 0,
            "velas_loaded": len(velas) if 'velas' in globals() else 0,
            "transactions_count": len(statistics_transactions) if 'statistics_transactions' in globals() else 0,
            "gerenciamento_ativo": gerenciamento_ativo if 'gerenciamento_ativo' in globals() else None,
            "modo_entrada": modo_entrada if 'modo_entrada' in globals() else None,
            "modo_gale": modo_gale if 'modo_gale' in globals() else None,
            "ml_strategy_active": ml_strategy_active if 'ml_strategy_active' in globals() else None,
            "abr_strategy_active": abr_strategy_active if 'abr_strategy_active' in globals() else None,
            "telegram_ativado": telegram_ativado if 'telegram_ativado' in globals() else None
        }
        
        return debug_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter debug info: {str(e)}")

# ================================
# MODELOS ADICIONAIS PARA REQUESTS
# ================================

class ManualSignalRequest(BaseModel):
    symbol: str = Field(..., description="Símbolo para operar")
    direction: SignalType = Field(..., description="Direção da operação")
    duration: int = Field(60, ge=15, le=3600, description="Duração em segundos")
    force: bool = Field(False, description="Forçar sinal mesmo com validações")

class UpdateSymbolsRequest(BaseModel):
    symbols_config: Dict[str, bool] = Field(..., description="Configuração dos símbolos")

class TelegramTestRequest(BaseModel):
    message: str = Field(..., description="Mensagem de teste")

# ================================
# ENDPOINTS ADICIONAIS
# ================================

@app.post("/api/v1/signals/manual/advanced")
async def send_advanced_manual_signal(request: ManualSignalRequest):
    """Envia um sinal manual avançado com validações"""
    try:
        if request.symbol not in symbols:
            raise HTTPException(status_code=404, detail="Símbolo não encontrado")
        
        if not simbolos_ativos.get(request.symbol, False):
            raise HTTPException(status_code=400, detail="Símbolo não está ativo")
        
        if not request.force:
            # Validações de segurança
            if not is_running or not should_send_orders:
                raise HTTPException(status_code=400, detail="Bot não está em execução")
        
        # Aqui você implementaria a lógica para processar o sinal
        # Por exemplo, adicionando à fila de sinais ou executando diretamente
        
        return {
            "message": "Sinal manual avançado processado",
            "symbol": request.symbol,
            "direction": request.direction.value,
            "duration": request.duration,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao processar sinal: {str(e)}")

@app.put("/api/v1/symbols/bulk")
async def update_symbols_bulk(request: UpdateSymbolsRequest):
    """Atualiza múltiplos símbolos de uma vez"""
    try:
        updated_symbols = []
        
        for symbol, active in request.symbols_config.items():
            if symbol in symbols:
                simbolos_ativos[symbol] = active
                updated_symbols.append({
                    "symbol": symbol,
                    "active": active,
                    "display_name": get_display_name(symbol)
                })
        
        # Salva configurações
        save_configurations()
        
        return {
            "message": f"{len(updated_symbols)} símbolos atualizados",
            "updated_symbols": updated_symbols
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar símbolos: {str(e)}")

@app.post("/api/v1/telegram/test")
async def test_telegram(request: TelegramTestRequest):
    """Testa envio de mensagem para Telegram"""
    try:
        if not telegram_ativado:
            raise HTTPException(status_code=400, detail="Telegram não está ativado")
        
        if not chat_id_value or not bot_token:
            raise HTTPException(status_code=400, detail="Configurações do Telegram incompletas")
        
        # Envia mensagem de teste
        await enviar_mensagem_telegram(request.message, chat_id_value, bot_token)
        
        return {
            "message": "Mensagem de teste enviada para o Telegram",
            "telegram_message": request.message
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao testar Telegram: {str(e)}")

@app.get("/api/v1/strategies/active")
async def get_active_strategies():
    """Retorna estratégias ativas"""
    try:
        strategies = {
            "retracao": retracao_value if 'retracao_value' in globals() else False,
            "reversao": reversao_value if 'reversao_value' in globals() else False,
            "fluxo": fluxo_active if 'fluxo_active' in globals() else False,
            "ml_strategy": ml_strategy_active if 'ml_strategy_active' in globals() else False,
            "abr_strategy": abr_strategy_active if 'abr_strategy_active' in globals() else False,
            "price_action": price_action_active if 'price_action_active' in globals() else False,
            "volume_profile": volume_profile_active if 'volume_profile_active' in globals() else False,
            "antiloss": antiloss_ativado if 'antiloss_ativado' in globals() else False
        }
        
        active_count = sum(1 for active in strategies.values() if active)
        
        return {
            "strategies": strategies,
            "active_count": active_count,
            "total_strategies": len(strategies)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter estratégias: {str(e)}")

# ================================
# FUNÇÃO PRINCIPAL
# ================================

def run_api(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    """Executa o servidor da API"""
    uvicorn.run(
        "api:app" if reload else app,
        host=host,
        port=port,
        reload=reload,
        log_level="info"
    )

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Binary Elite Bot API")
    parser.add_argument("--host", default="0.0.0.0", help="Host para bind")
    parser.add_argument("--port", type=int, default=8000, help="Porta para bind")
    parser.add_argument("--reload", action="store_true", help="Ativar auto-reload")
    
    args = parser.parse_args()
    
    print(f"""
    ╔══════════════════════════════════════╗
    ║         Binary Elite Bot API         ║
    ║                v1.0.0                ║
    ╠══════════════════════════════════════╣
    ║  Server: http://{args.host}:{args.port}           ║
    ║  Docs:   http://{args.host}:{args.port}/docs     ║
    ║  ReDoc:  http://{args.host}:{args.port}/redoc    ║
    ╚══════════════════════════════════════╝
    """)
    
    run_api(host=args.host, port=args.port, reload=args.reload)