"""Adapter + Factory pattern: LLM/ajan saglayici bagimsizligi (bkz. docs/ARCHITECTURE.md SS3).

Uc somut Agent adapteri:
- LlamaOrchestratorAdapter: ana orkestrator (dogal dil anlama, intent siniflandirma) - mevcut
  yerel `llama3.1:8b` modelini kullanir.
- HermesAgentAdapter: gercek agentic/tool-calling gorevleri icin AYRI bir yerel model
  (`hermes3:8b`) - Ollama'nin model swap/keep_alive mekanizmasi sayesinde ayni anda
  VRAM'de iki 8B modelin birden tutulmasi gerekmez (bkz. docs/ARCHITECTURE.md SS5).
- ClaudeCodeAdapter: dis API (Anthropic) - su an STUB, `anthropic` SDK'si kurulu degil ve
  ANTHROPIC_API_KEY .env'de tanimli degil (CLAUDE.md'nin sir yonetimi ilkesiyle tutarli,
  sahte/gomulu bir anahtar yazilmadi); respond() cagrilirsa ne yapilmasi gerektigini
  soyleyen net bir NotImplementedError firlatir.
"""

import logging
from typing import Literal, Optional

import httpx
import ollama

from src.jarvis.agents.base import Agent

logger = logging.getLogger("jarvis.adapters")

ORCHESTRATOR_MODEL_NAME = "llama3.1:8b"
HERMES_MODEL_NAME = "hermes3:8b"

# Baglanti/model hatalarinda kullanicaya donecek TR/EN mesaj - src/jarvis/brain/llm.py'deki
# think_and_respond_stream'in hata deseniyle bilincli olarak ayni (iki ayri LLM cagri yeri
# ayni hata sinifina karsi ayni bicimde davransin diye).
def _connection_error_message(model_name: str) -> str:
    return (
        f"Ollama servisine bağlanamıyorum ({model_name}), çalıştığından emin olun (ollama serve). "
        f"I can't reach Ollama for {model_name} - make sure it's running (ollama serve)."
    )


def _model_not_found_message(model_name: str) -> str:
    return (
        f"'{model_name}' modeli bulunamadı, 'ollama pull {model_name}' ile indirin. "
        f"Model '{model_name}' not found - pull it with 'ollama pull {model_name}'."
    )


class LlamaOrchestratorAdapter(Agent):
    """Ana orkestrator: dogal dil diyalog + intent siniflandirma icin `llama3.1:8b`."""

    def __init__(self, model_name: str = ORCHESTRATOR_MODEL_NAME) -> None:
        self._model_name = model_name

    def respond(self, prompt: str, context: Optional[list[dict]] = None) -> str:
        messages = list(context or [])
        messages.append({"role": "user", "content": prompt})
        try:
            response = ollama.chat(model=self._model_name, messages=messages)
            return response["message"]["content"].strip()
        except (httpx.ConnectError, ConnectionError):
            logger.error("Orkestrator: Ollama'ya baglanilamadi (%s).", self._model_name)
            return _connection_error_message(self._model_name)
        except ollama.ResponseError as exc:
            if exc.status_code == 404:
                logger.error("Orkestrator: model bulunamadi (%s).", self._model_name)
                return _model_not_found_message(self._model_name)
            raise

    def supports_tools(self) -> bool:
        return False


class HermesAgentAdapter(Agent):
    """Gercek agentic/tool-calling ajani: ayri bir Ollama modeli (`hermes3:8b`).

    Su an icin sadece diyalog (respond()) baglanmis durumda; gercek tool-calling
    (dosya islemleri, terminal komutlari vb.) Faz 3'te core.tools ile bagli.
    """

    def __init__(self, model_name: str = HERMES_MODEL_NAME) -> None:
        self._model_name = model_name

    def respond(self, prompt: str, context: Optional[list[dict]] = None) -> str:
        messages = list(context or [])
        messages.append({"role": "user", "content": prompt})
        try:
            response = ollama.chat(model=self._model_name, messages=messages)
            return response["message"]["content"].strip()
        except (httpx.ConnectError, ConnectionError):
            logger.error("Hermes: Ollama'ya baglanilamadi (%s).", self._model_name)
            return _connection_error_message(self._model_name)
        except ollama.ResponseError as exc:
            if exc.status_code == 404:
                logger.error("Hermes: model bulunamadi (%s).", self._model_name)
                return _model_not_found_message(self._model_name)
            raise

    def supports_tools(self) -> bool:
        return True


class ClaudeCodeAdapter(Agent):
    """Dis destek: agir bilissel/kod-mimari gorevleri icin Claude Code'a (Anthropic API) devir.

    STUB: `anthropic` paketi requirements.txt'te yok ve ANTHROPIC_API_KEY .env'de tanimli
    degil. Gercek baglanti ayri bir gorev (paket kurulumu + .env + gercek istek/hata
    yonetimi) gerektirir - burada sadece Agent sozlesmesini karsilayan, ne eksik oldugunu
    acikca soyleyen bir yer tutucu var.
    """

    def respond(self, prompt: str, context: Optional[list[dict]] = None) -> str:
        raise NotImplementedError(
            "ClaudeCodeAdapter henuz baglanmadi: 'pip install anthropic' ile SDK'yi kurup "
            "_client() metodunu Anthropic() ile doldurun ve .env'e ANTHROPIC_API_KEY ekleyin."
        )

    def supports_tools(self) -> bool:
        return True


AgentRole = Literal["orchestrator", "tool_agent", "deep_reasoning"]


class AgentFactory:
    """Role -> Agent eslemesi (Factory pattern) - cagiran kod hicbir zaman somut adapter
    sinifini gormez, sadece `AgentFactory.create(role)` ile bir `Agent` alir.
    """

    @staticmethod
    def create(role: AgentRole) -> Agent:
        if role == "orchestrator":
            return LlamaOrchestratorAdapter()
        if role == "tool_agent":
            return HermesAgentAdapter()
        if role == "deep_reasoning":
            return ClaudeCodeAdapter()
        raise ValueError(f"Bilinmeyen ajan rolu: {role!r}")
