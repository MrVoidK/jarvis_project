"""Adapter + Factory pattern: LLM/ajan saglayici bagimsizligi (bkz. docs/ARCHITECTURE.md SS3).

Iki somut Agent adapteri:
- OllamaAgentAdapter: yerel Ollama modelleriyle calisan cok-rollu adapter. Rol
  (orchestrator / tool_agent / router) yalnizca `ROLE_MODEL_MAP`'teki model adini
  degistirir; `orchestrator` ve `tool_agent` AYNI modeli (`hermes3:8b`) paylasir
  (Faz 6.2 - iki ayri 8B model 12GB VRAM'e sigmiyor, bkz. docs/ARCHITECTURE.md SS5);
  `router` intent siniflandirma icin ayri, kucuk/hizli bir model (`qwen2.5:3b`)
  kullanir (cift-8B cagri gecikmesini azaltir).
- ClaudeCodeAdapter: dis destek (agir kod/mimari isi) - su an STUB. Faz 6.3'te
  `run_command`/`terminal_tool` uzerinden `claude` CLI olarak baglanacak (anthropic
  SDK/API key YOLU DEGIL - kullanici karari, bkz. docs/ROADMAP.md Faz 6.3).
"""

import logging
from typing import Literal, Optional

import httpx
import ollama

from src.jarvis.agents.base import Agent, AgentToolResponse, ToolCall

logger = logging.getLogger("jarvis.adapters")

ORCHESTRATOR_MODEL_NAME = "hermes3:8b"
ROUTER_MODEL_NAME = "qwen2.5:3b"

# Rol -> yerel Ollama model adi. orchestrator + tool_agent paylasimli (Faz 6.2 -
# VRAM butcesi); router ayri kucuk model. deep_reasoning bu haritada YOK - o
# yerel bir model degil (ClaudeCodeAdapter).
ROLE_MODEL_MAP: dict[str, str] = {
    "orchestrator": ORCHESTRATOR_MODEL_NAME,
    "tool_agent": ORCHESTRATOR_MODEL_NAME,
    "router": ROUTER_MODEL_NAME,
}

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


def check_ollama_connection(model_name: str = ORCHESTRATOR_MODEL_NAME) -> tuple[bool, str]:
    """Boot sirasinda Ollama'nin erisilebilir oldugunu HAFIFCE dogrular.

    `ollama.show(model_name)` sadece model metadata'sini ister - gercek bir
    `ollama.chat(...)` (chat completion) cagirmadan hem sunucunun ayakta
    oldugunu HEM istenen modelin cekilmis oldugunu tek istekte dogrular.
    Hata siniflandirmasi OllamaAgentAdapter.respond()'daki ile AYNI
    (bkz. yukarida) - iki ayri cagri yeri ayni hata sinifina karsi ayni
    mesaji versin diye.
    """
    try:
        ollama.show(model_name)
        return True, f"Ollama bağlantısı doğrulandı ({model_name})."
    except (httpx.ConnectError, ConnectionError):
        return False, _connection_error_message(model_name)
    except ollama.ResponseError as exc:
        if exc.status_code == 404:
            return False, _model_not_found_message(model_name)
        raise


class OllamaAgentAdapter(Agent):
    """Yerel Ollama modelleriyle calisan cok-rollu adapter (orchestrator / tool_agent /
    router). Rol farki = `model_name` (bkz. ROLE_MODEL_MAP) + cagiran tarafin
    verdigi sistem promptu (`context`); ayri bir sinif gerekmez."""

    def __init__(self, model_name: str = ORCHESTRATOR_MODEL_NAME) -> None:
        self._model_name = model_name

    def respond(self, prompt: str, context: Optional[list[dict]] = None) -> str:
        messages = list(context or [])
        messages.append({"role": "user", "content": prompt})
        try:
            response = ollama.chat(model=self._model_name, messages=messages)
            return response["message"]["content"].strip()
        except (httpx.ConnectError, ConnectionError):
            logger.error("Ajan: Ollama'ya baglanilamadi (%s).", self._model_name)
            return _connection_error_message(self._model_name)
        except ollama.ResponseError as exc:
            if exc.status_code == 404:
                logger.error("Ajan: model bulunamadi (%s).", self._model_name)
                return _model_not_found_message(self._model_name)
            raise

    def respond_stream(self, prompt: str, context: Optional[list[dict]] = None):
        """Gercek streaming: `ollama.chat(stream=True)`'in her chunk'inin ham
        icerigini yield eder. BILINCLI OLARAK try/except YOK - tuketici
        (brain/llm.py:think_and_respond_stream) kendi hata siniflandirmasina
        sahip, ham httpx/ollama hatasi ona propagate ediliyor (base.py
        respond_stream docstring'i + v2 SS2.4)."""
        messages = list(context or [])
        messages.append({"role": "user", "content": prompt})
        for chunk in ollama.chat(model=self._model_name, messages=messages, stream=True):
            yield chunk["message"]["content"]

    def call_tools(
        self, prompt: str, tools: list[dict], context: Optional[list[dict]] = None
    ) -> AgentToolResponse:
        messages = list(context or [])
        messages.append({"role": "user", "content": prompt})
        try:
            # `temperature` dusuk (varsayilan Ollama degeri degil, ACIKCA
            # dusuruluyor) - kucuk yerel router modelleri (once llama3.1:8b, Faz
            # 6.2'den sonra qwen2.5:3b) duz sohbet/vedalasma gibi arac
            # GEREKTIRMEYEN girdilerde bile SIK SIK bir arac secip (ör.
            # "Görüşürüz." -> read_notes) halusinasyon uretiyordu. Dusuk
            # sicaklik, function-calling egitiminin "hep bir arac sec"
            # onyargisini tamamen ortadan kaldirmaz ama modelin en-olasi
            # (genelde "arac yok") secimine daha tutarli sekilde sadik
            # kalmasini sagliyor - tool-secim karari zaten deterministik/
            # tekrarlanabilir olmali, yaratici cesitlilige hicbir ihtiyac yok.
            response = ollama.chat(
                model=self._model_name, messages=messages, tools=tools, options={"temperature": 0.1}
            )
        except (httpx.ConnectError, ConnectionError):
            logger.error("Ajan (tool-calling): Ollama'ya baglanilamadi (%s).", self._model_name)
            return AgentToolResponse(content=_connection_error_message(self._model_name))
        except ollama.ResponseError as exc:
            if exc.status_code == 404:
                logger.error("Ajan (tool-calling): model bulunamadi (%s).", self._model_name)
                return AgentToolResponse(content=_model_not_found_message(self._model_name))
            raise

        raw_calls = response["message"].get("tool_calls") or []
        # security-reviewer bulgusu (Faz 3.3): raw_calls'un beklenen bicimde
        # olacaginin garantisi yok - bozuk/beklenmedik bir yanit burada
        # yakalanmazsa KeyError/TypeError, classify() -> _handle_turn() ->
        # run_jarvis() zincirinde HICBIR yerde yakalanmadan yukari cikar
        # (run_jarvis()'in try/except'i SADECE KeyboardInterrupt yakalar) ve
        # tum Jarvis surecini cokertir. Bozuk bir tool_call, "hicbir arac
        # secilmedi" (bos liste) sayilir - fail-safe, crash degil.
        try:
            tool_calls = [
                ToolCall(name=call["function"]["name"], arguments=dict(call["function"]["arguments"]))
                for call in raw_calls
            ]
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("Orkestrator (tool-calling): beklenmeyen tool_call bicimi: %s", exc)
            tool_calls = []
        content = (response["message"].get("content") or "").strip() or None
        return AgentToolResponse(tool_calls=tool_calls, content=content)

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

    def call_tools(
        self, prompt: str, tools: list[dict], context: Optional[list[dict]] = None
    ) -> AgentToolResponse:
        raise NotImplementedError(
            "ClaudeCodeAdapter henuz baglanmadi: 'pip install anthropic' ile SDK'yi kurup "
            "_client() metodunu Anthropic() ile doldurun ve .env'e ANTHROPIC_API_KEY ekleyin."
        )

    def supports_tools(self) -> bool:
        return True


AgentRole = Literal["orchestrator", "tool_agent", "router", "deep_reasoning"]


class AgentFactory:
    """Role -> Agent eslemesi (Factory pattern) - cagiran kod hicbir zaman somut adapter
    sinifini gormez, sadece `AgentFactory.create(role)` ile bir `Agent` alir.

    orchestrator / tool_agent / router -> OllamaAgentAdapter (rol farki = model,
    bkz. ROLE_MODEL_MAP); deep_reasoning -> ClaudeCodeAdapter (yerel degil).
    """

    @staticmethod
    def create(role: AgentRole) -> Agent:
        if role in ROLE_MODEL_MAP:
            return OllamaAgentAdapter(model_name=ROLE_MODEL_MAP[role])
        if role == "deep_reasoning":
            return ClaudeCodeAdapter()
        raise ValueError(f"Bilinmeyen ajan rolu: {role!r}")
