"""Adapter + Factory pattern: LLM/ajan saglayici bagimsizligi (bkz. docs/ARCHITECTURE.md SS3).

Iki somut Agent adapteri:
- OllamaAgentAdapter: yerel Ollama modelleriyle calisan cok-rollu adapter. Rol
  (orchestrator / tool_agent / router) yalnizca `ROLE_MODEL_MAP`'teki model adini
  degistirir; `orchestrator` ve `tool_agent` AYNI modeli (`hermes3:8b`) paylasir
  (Faz 6.2 - iki ayri 8B model 12GB VRAM'e sigmiyor, bkz. docs/ARCHITECTURE.md SS5);
  `router` intent siniflandirma icin ayri, kucuk/hizli bir model (`qwen2.5:3b`)
  kullanir (cift-8B cagri gecikmesini azaltir).
- ClaudeCodeAdapter: dis destek (agir kod/mimari isi) - yerel `claude` CLI'i
  non-interaktif `-p` modunda alt surec olarak calistirir (Faz 6.3). anthropic
  SDK / ANTHROPIC_API_KEY YOLU DEGIL (kullanici karari, bkz. docs/ROADMAP.md Faz
  6.3). `claude -p` VARSAYILAN izinlerle: kod tabanini okur, analiz/plan/kod-metni
  uretir ama dosya DEGISTIRMEZ.
"""

import logging
import os
import subprocess
from typing import Literal, Optional

import httpx
import ollama

from src.jarvis.agents.base import Agent, AgentToolResponse, ToolCall
from src.jarvis.core.paths import PROJECT_ROOT
from src.jarvis.tools.subprocess_utils import _API_KEY_ENV_VARS

logger = logging.getLogger("jarvis.adapters")

ORCHESTRATOR_MODEL_NAME = "hermes3:8b"
# 2026-08-29: "tek model" (hermes3 hem router hem chat) DENENDI ve GERI ALINDI -
# hermes3:8b routing bataryasi 18/27 (qwen2.5:3b: 26/27); hermes sik sik tool
# cagirmak yerine sohbete kaciyor. qwen2.5:3b kucuk ama tool-secim isinde
# belirgin daha iyi. Mini router KALIYOR.
ROUTER_MODEL_NAME = "qwen2.5:3b"

# deep_reasoning bu haritada YOK - o yerel bir model degil (ClaudeCodeAdapter).
ROLE_MODEL_MAP: dict[str, str] = {
    "orchestrator": ORCHESTRATOR_MODEL_NAME,
    "tool_agent": ORCHESTRATOR_MODEL_NAME,
    "router": ROUTER_MODEL_NAME,
}

# Router (qwen2.5:3b, ~2.2 GB) keep_alive. TAKAS:
#  - "0"  -> qwen her turda VRAM'den cikar (routing ~2.5 sn reload), ama XTTS/
#           hermes'e ~2.2 GB yer acar (VRAM %98'e dayaniyordu - TTS spike riski).
#  - "30s"/"2m" -> aktif konusmada qwen SICAK (routing ~0.5 sn), ama VRAM daha dolu.
# Varsayilan "30s"; TTS ilk-chunk spike'lari donerse `JARVIS_ROUTER_KEEP_ALIVE=0`
# ile canli ayarlanabilir (kod degisikligi yok). `_OLLAMA_TIMEOUT_S` read-timeout'u
# swap sirasinda takilmayi zaten net bir hataya ceviriyor.
_ROUTER_KEEP_ALIVE = os.environ.get("JARVIS_ROUTER_KEEP_ALIVE", "30s").strip()

# Ollama HTTP cagrilarinin read-timeout'u. ollama paketinin varsayilan client'i
# TIMEOUT'SUZ - Ollama ( or. VRAM baskisi altinda model yuklerken) takilirsa
# `ollama.chat` cagrisi SONSUZA kadar bloklar ve ana dongu Ctrl+C'ye kadar
# donar (canli testte gorulen asil bug). Bu client bir read-timeout dayatir;
# asimda httpx.TimeoutException firlar (respond/call_tools yakalar, streaming
# yolda brain/llm.py yakalar).
_OLLAMA_TIMEOUT_S = 90.0
_CLIENT = ollama.Client(timeout=httpx.Timeout(_OLLAMA_TIMEOUT_S, connect=5.0))

# Baglanti/model hatalarinda kullanicaya donecek TR/EN mesaj - src/jarvis/brain/llm.py'deki
# think_and_respond_stream'in hata deseniyle bilincli olarak ayni (iki ayri LLM cagri yeri
# ayni hata sinifina karsi ayni bicimde davransin diye).
def _connection_error_message(model_name: str) -> str:
    return (
        f"Ollama servisine bağlanamıyorum ({model_name}), çalıştığından emin olun (ollama serve). "
        f"I can't reach Ollama for {model_name} - make sure it's running (ollama serve)."
    )


def _timeout_message(model_name: str) -> str:
    return (
        f"{model_name} zamanında yanıt vermedi (muhtemelen VRAM yetersiz). "
        f"{model_name} didn't respond in time (likely low on VRAM)."
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

    def __init__(
        self,
        model_name: str = ORCHESTRATOR_MODEL_NAME,
        keep_alive: "str | None" = None,
    ) -> None:
        self._model_name = model_name
        # keep_alive: model VRAM'de bosta ne kadar kalsin (Ollama'ya iletilir).
        # None -> Ollama varsayilani (5 dk). Router icin kisa tutuluyor (bkz.
        # ROLE_MODEL_MAP kullanimi) - kucuk model, konusma bitince ~2 dk sonra
        # VRAM'den cikip ~2 GB serbest birakiyor, aktif konusmada hot kaliyor.
        self._keep_alive = keep_alive

    def _chat_kwargs(self) -> dict:
        return {"keep_alive": self._keep_alive} if self._keep_alive is not None else {}

    def respond(self, prompt: str, context: Optional[list[dict]] = None) -> str:
        messages = list(context or [])
        messages.append({"role": "user", "content": prompt})
        try:
            response = _CLIENT.chat(
                model=self._model_name, messages=messages, **self._chat_kwargs()
            )
            return response["message"]["content"].strip()
        except (httpx.ConnectError, ConnectionError):
            logger.error("Ajan: Ollama'ya baglanilamadi (%s).", self._model_name)
            return _connection_error_message(self._model_name)
        except httpx.TimeoutException:
            logger.error("Ajan: Ollama yanit vermedi (%s, %.0fs timeout).", self._model_name, _OLLAMA_TIMEOUT_S)
            return _timeout_message(self._model_name)
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
        # _CLIENT read-timeout dayatir: ilk token gelmezse (Ollama takilirsa)
        # httpx.TimeoutException firlar ve tuketici (brain/llm.py) yakalar -
        # eskiden timeout'suz ollama.chat sonsuza kadar bloklardi.
        for chunk in _CLIENT.chat(
            model=self._model_name, messages=messages, stream=True, **self._chat_kwargs()
        ):
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
            response = _CLIENT.chat(
                model=self._model_name,
                messages=messages,
                tools=tools,
                options={"temperature": 0.1},
                **self._chat_kwargs(),
            )
        except (httpx.ConnectError, ConnectionError):
            logger.error("Ajan (tool-calling): Ollama'ya baglanilamadi (%s).", self._model_name)
            return AgentToolResponse(content=_connection_error_message(self._model_name))
        except httpx.TimeoutException:
            logger.error("Ajan (tool-calling): Ollama yanit vermedi (%s, %.0fs).", self._model_name, _OLLAMA_TIMEOUT_S)
            return AgentToolResponse(content=_timeout_message(self._model_name))
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


_CLAUDE_CLI = "claude"
_CLAUDE_TIMEOUT_S = 120.0
_CLAUDE_CLI_ERROR = (
    "Claude Code CLI'ını çalıştıramadım (claude komutu PATH'te mi?). "
    "Couldn't run the Claude Code CLI (is 'claude' on PATH?)."
)
_CLAUDE_TIMEOUT_MSG = (
    "Claude Code çok uzun sürdü, durdurdum. "
    "Claude Code took too long, so I stopped it."
)


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Zaman asimina ugrayan bir alt surecin TUM surec agacini oldurur (Windows
    `taskkill /F /T`). Ayni desen terminal_tool.py ve web_ui_process.py'de de var
    - bilincli 3. kopya (ileride core/proc.py'ye cikarilabilir); process.kill()
    tek basina sadece dogrudan cocugu (node.exe) oldurur, `claude`'un baslattigi
    MCP alt surecleri yetim kalirdi."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("taskkill basarisiz (%s) - sadece dogrudan surec olduruluyor.", exc)
    finally:
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


class ClaudeCodeAdapter(Agent):
    """Agir kod/mimari isini yerel `claude` CLI'a (Claude Code, non-interaktif
    `-p` modu) devreder - anthropic SDK / API key YOLU DEGIL (kullanici karari,
    bkz. docs/ROADMAP.md Faz 6.3).

    `claude -p` VARSAYILAN izinlerle calisir: kod tabanini okur, analiz/plan/
    kod-metni uretir ama dosya DEGISTIRMEZ (-p modunda yazma/bash otomatik
    reddedilir). respond() ana dongude BLOKLAR (en fazla _CLAUDE_TIMEOUT_S) -
    dis surec, isbirlikci iptali yok; non-blocking varyant Faz 6.7
    (CreateProjectTool + spawn_detached).
    """

    def __init__(self, cli_path: str = _CLAUDE_CLI, timeout: float = _CLAUDE_TIMEOUT_S) -> None:
        self._cli_path = cli_path
        self._timeout = timeout

    def respond(self, prompt: str, context: Optional[list[dict]] = None) -> str:
        # context su an kullanilmiyor: delegate_code branch (core/app.py) sadece
        # tek bir `task` metni geciriyor; `claude -p` zaten tek bir prompt string
        # alir (mesaj listesi degil).
        # `claude -p` ASLA API key ile calismamali - kullanicinin ~/.claude
        # abonelik oturumuna dusmeli (kullanici karari). `spawn_detached`'in
        # (tools/subprocess_utils.py) yaptigi env temizligi burada da yapiliyor
        # (tutarlilik - backlog #1): .env'de/ortamda bir key olsa bile cocuk
        # onu gormesin.
        child_env = os.environ.copy()
        for _key in _API_KEY_ENV_VARS:
            child_env.pop(_key, None)
        try:
            proc = subprocess.Popen(
                [self._cli_path, "-p", prompt],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=PROJECT_ROOT,
                env=child_env,
            )
        except (FileNotFoundError, OSError) as exc:
            logger.error("ClaudeCodeAdapter: claude CLI baslatilamadi: %s", exc)
            return _CLAUDE_CLI_ERROR
        try:
            stdout, stderr = proc.communicate(timeout=self._timeout)
        except subprocess.TimeoutExpired:
            logger.error("ClaudeCodeAdapter: claude -p zaman asimi (%.0fs)", self._timeout)
            _kill_process_tree(proc)
            return _CLAUDE_TIMEOUT_MSG

        out = (stdout or "").strip()
        if proc.returncode != 0:
            logger.error(
                "ClaudeCodeAdapter: claude -p exit %s: %s", proc.returncode, (stderr or "")[:300]
            )
            return out or _CLAUDE_CLI_ERROR
        return out or "Claude Code bir yanıt üretmedi. Claude Code returned no output."

    def call_tools(
        self, prompt: str, tools: list[dict], context: Optional[list[dict]] = None
    ) -> AgentToolResponse:
        raise NotImplementedError(
            "ClaudeCodeAdapter.call_tools() uygulanmiyor - Claude Code kendi tool-use'unu "
            "ICERIDE yapar, Ollama-stili bir semayla surulmez. respond() kullanin."
        )

    def supports_tools(self) -> bool:
        # Bizim call_tools() arayuzumuzu uygulamiyor (Claude Code araclarini kendi
        # icinde kullanir) - dispatcher bir intent'i buraya YONLENDIRMEMELI.
        return False


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
            keep_alive = _ROUTER_KEEP_ALIVE if role == "router" else None
            return OllamaAgentAdapter(model_name=ROLE_MODEL_MAP[role], keep_alive=keep_alive)
        if role == "deep_reasoning":
            return ClaudeCodeAdapter()
        raise ValueError(f"Bilinmeyen ajan rolu: {role!r}")
