"""Agent - butun Faz 2 adapterlerinin uydugu ortak sozlesme.

core.dispatcher ve (ileride) core.app, hangi saglayicinin (yerel Ollama modeli
veya dis bir API) arkada calistigini hic bilmeden sadece bu arayuzle konusur -
Factory pattern ile birlikte (bkz. adapters.agent_factory.AgentFactory) yeni
bir saglayici eklemek, mevcut cagiran kodu hic degistirmeden yeni bir Agent
alt sinifi eklemekten ibarettir (bkz. docs/ARCHITECTURE.md SS3).
"""

from abc import ABC, abstractmethod
from typing import Optional


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

    @abstractmethod
    def supports_tools(self) -> bool:
        """Bu ajanin gercek tool-calling (fonksiyon cagirma) destekleyip desteklemedigini bildirir.

        core.dispatcher, bir intent arac kullanimi gerektiriyorsa sadece
        supports_tools() True donen bir ajana yonlendirebilir (bkz. Faz 3).
        """
        raise NotImplementedError
