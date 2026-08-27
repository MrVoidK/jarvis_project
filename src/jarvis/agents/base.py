"""Agent - butun Faz 2 adapterlerinin uydugu ortak sozlesme.

core.dispatcher ve (ileride) core.app, hangi saglayicinin (yerel Ollama modeli
veya dis bir API) arkada calistigini hic bilmeden sadece bu arayuzle konusur -
Factory pattern ile birlikte (bkz. adapters.agent_factory.AgentFactory) yeni
bir saglayici eklemek, mevcut cagiran kodu hic degistirmeden yeni bir Agent
alt sinifi eklemekten ibarettir (bkz. docs/ARCHITECTURE.md SS3).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


@dataclass
class ToolCall:
    """Modelin secip cagirmaya karar verdigi TEK bir fonksiyon + argumanlari."""

    name: str
    arguments: dict[str, Any]


@dataclass
class AgentToolResponse:
    """`call_tools()`'un donus degeri - modelin sectigi (varsa) tool cagrilari."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    content: Optional[str] = None


class Agent(ABC):
    """Tum ajan adapterlerinin (Orkestrator/Hermes/Claude Code) uygulamasi gereken arayuz."""

    @abstractmethod
    def respond(self, prompt: str, context: Optional[list[dict]] = None) -> str:
        """Verilen prompt'a (ve varsa onceki mesaj gecmisine) tek, tam bir yanit dondurur.

        `context`, `[{"role": "user"|"assistant"|"system", "content": str}, ...]`
        formatinda - core.llm.think_and_respond_stream'in kullandigi `history`
        sozlesmesiyle ayni sekilde, boylece ileride adapterler o gecmisi
        dogrudan devralabilir.
        """
        raise NotImplementedError

    def respond_stream(
        self, prompt: str, context: Optional[list[dict]] = None
    ) -> Iterator[str]:
        """Yaniti PARCA PARCA (cumle degil, ham metin chunk'lari) uretir.

        Varsayilan implementasyon native streaming'i olmayan adapterler icin
        respond()'un tam yanitini TEK bir chunk olarak yield eder; gercek
        streaming yapan adapterler (yerel Ollama) bunu override eder.

        respond()/call_tools()'un aksine saglayici hatalarini (httpx.ConnectError,
        ollama.ResponseError vb.) YUTMAZ - bu generator'un tek tuketicisi
        brain/llm.py:think_and_respond_stream, kendi TR/EN hata siniflandirmasi
        ve history mantigina sahip; ham hata ona propagate edilir (bkz.
        docs/jarvis-mimari-v2-multiagent-entegrasyon.md SS2.4).
        """
        yield self.respond(prompt, context)

    @abstractmethod
    def supports_tools(self) -> bool:
        """Bu ajanin gercek tool-calling (fonksiyon cagirma) destekleyip desteklemedigini bildirir.

        core.dispatcher, bir intent arac kullanimi gerektiriyorsa sadece
        supports_tools() True donen bir ajana yonlendirebilir (bkz. Faz 3).
        """
        raise NotImplementedError

    @abstractmethod
    def call_tools(
        self, prompt: str, tools: list[dict], context: Optional[list[dict]] = None
    ) -> AgentToolResponse:
        """Modelin, verilen `tools` (Ollama/OpenAI-stili function-calling semasi,
        bkz. adapters/tool_schema.py) arasindan (varsa) SECTIGI cagriyi dondurur.

        `tools: list[dict]`'in saglayici-stili formati "sizdirmasi" bilincli bir
        pragmatizm: bu format zaten fiili endustri standardi (Ollama, OpenAI,
        Anthropic tool_use hepsi ayni iskeleti kullaniyor) - dorduncu bir
        saglayici ihtimaline karsi bugunden asiri soyutlama (YAGNI ihlali)
        yapilmiyor (bkz. docs/ARCHITECTURE.md SS3).
        """
        raise NotImplementedError
