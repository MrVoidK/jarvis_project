"""MCP sunucularina (File System, SQLite, GitHub, ...) baglanip araclarini
yerel Tool sozlesmesine sarmalayan istemci (bkz. docs/ARCHITECTURE.md SS9).

SADECE bilgi/veri erisimi icin - hicbir MCP araci OS kontrolu yapmaz; bu
sinir (a) config/mcp_servers.yaml'daki sunucu SECIMIYLE (yerelde terminal/
launch_app/media calistiran bir MCP sunucusu buraya ASLA configure
edilmemeli) ve (b) tools/registry.py'nin TOOL_REGISTRY'yi hic degistirmeden
ayri bir `all_tools()` view'iyla korunuyor - bkz. o dosyanin docstring'i.

ASYNC<->SYNC KOPRUSU (bilincli mimari karar): `mcp` SDK'si asyncio tabanli,
proje ise (ears/mouth/brain ile ayni ilke, bkz. CLAUDE.md Kod Stili) tamamen
senkron. Her sunucu alt-sureci BIR KEZ baslatilip (ears/mouth'un modul-
seviyesi Singleton yukleme deseniyle ayni ruhta, bkz. docs/ARCHITECTURE.md
SS3 "Zaten var olan ortuk desenler") kalici bir arka-plan thread'indeki TEK
event-loop'ta canli tutulur; senkron Tool.execute() cagrilari
`asyncio.run_coroutine_threadsafe()` ile bu loop'a kopruleniyor - her tool
cagrisinda YENIDEN subprocess baslatmak (npx'in kendi baslama gecikmesi +
MCP handshake) kabul edilemez derecede yavas olurdu.

FAIL-SOFT: bir sunucuya baglanti/kesif basarisiz olursa (npx yok, paket
indirilemedi, handshake timeout...) SADECE o sunucu icin uyari loglanir,
digerleri ve uygulamanin geri kalani calismaya devam eder (bkz.
core/mcp_config.py modul docstring'i, ayni fail-soft ilkesi).

BILINEN SINIRLAMA (security-reviewer bulgusu): `call_tool()` senkron oldugu
icin `core/app.py:_execute_tool()` onu ana thread'de, `_CALL_TIMEOUT_SECONDS`
kadar (bugun ~30sn) BLOKLAYARAK cagirir - bu sure boyunca Ears/ana dongu de
durur. Bu, `core/app.py:run_jarvis()` docstring'indeki "TEK bir bloklayici
model cagrisini yarida kesemez" sinirlamasiyla AYNI kategoriden (Whisper/
Ollama/XTTS cagrilari da ayni sekilde bloklar) - MCP icin ayrica kotu
niyetli/donmus bir sunucunun bunu tekrarlanan cagrilarla kotuye kullanmasi
mumkun; tam cozumu (`Tool.execute()` imzasina `stop_event` eklemek, TUM
tool'lari etkileyen bir degisiklik) bu turun kapsami disinda birakildi.
"""

import asyncio
import logging
import threading
from contextlib import AsyncExitStack
from typing import Callable, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool as MCPToolSchema

from src.jarvis.core.guardrail.base import GuardrailChain
from src.jarvis.core.guardrail.input_checks import InputInjectionCheck
from src.jarvis.core.mcp_config import MCPServerConfig, load_mcp_servers_config
from src.jarvis.tools.base import Tool
from src.jarvis.tools.mcp_tool import MCPTool, build_mcp_tool_name

logger = logging.getLogger("jarvis.adapters.mcp_client_adapter")

# Sunucu basina baglanti/kesif ust siniri (npx ilk calistirmada paketi
# indirebilir - bu yuzden diger ag cagrilarina gore cömert tutuldu).
_CONNECT_TIMEOUT_SECONDS = 30.0
# Tek bir call_tool cagrisi icin ust sinir.
_CALL_TIMEOUT_SECONDS = 30.0
# Arka plan event-loop thread'inin hazir olmasini bekleme ust siniri.
_THREAD_READY_TIMEOUT_SECONDS = 5.0

# security-reviewer bulgusu ("tool poisoning"/rug-pull): bir MCP sunucusunun
# `name`/`description`/parametre aciklamalari, HICBIR filtreden gecmeden
# router LLM'in promptuna giriyordu (adapters/tool_schema.py:build_function_schema).
# Sunucu tarafinda (veya `npx -y` ile cekilen bir paket surumunde) degisen bir
# aciklama, "bu araci cagirirken sunu da yap" tarzinda modeli manipule edebilir -
# bu yuzden kesif aninda, kullanici girdisiyle AYNI injection taramasindan
# geciriliyor; takilan bir arac SESSIZCE atlanir (discovered'a hic girmez).
_DESCRIPTION_GUARDRAIL = GuardrailChain([InputInjectionCheck()])


def _text_to_scan(mcp_tool: MCPToolSchema, description: str) -> str:
    """Tarama icin isim + aciklama + (varsa) parametre aciklamalarini birlestirir."""
    schema = mcp_tool.input_schema or {}
    param_descriptions = " ".join(
        str(prop.get("description", ""))
        for prop in (schema.get("properties") or {}).values()
        if isinstance(prop, dict)
    )
    return " ".join([mcp_tool.name, description, param_descriptions])


def _wrap_mcp_tool(
    server: MCPServerConfig, mcp_tool: MCPToolSchema, call_fn: Callable[[dict], str]
) -> Optional[MCPTool]:
    """Bir MCP `Tool` tanimini (varsa `allowed_tools`'a gore filtreleyerek) `MCPTool`'a cevirir.

    Saf/IO'suz - hicbir sunucuya baglanmiyor, sadece zaten elde olan bir
    `mcp.types.Tool` nesnesini esliyor. Bu, `_connect_all()`'in gercek bir
    subprocess/handshake gerektiren kismindan AYRI tutulmasini saglar ki
    `tests/test_mcp_tool.py` gercek bir MCP sunucusu olmadan (dogrudan bir
    `MCPToolSchema(...)` nesnesiyle) bu esleme+filtreleme mantigini test edebilsin.
    """
    if server.allowed_tools is not None and mcp_tool.name not in server.allowed_tools:
        return None

    description = (mcp_tool.description or f"MCP tool: {mcp_tool.name}")[:500]

    safety = _DESCRIPTION_GUARDRAIL.run(_text_to_scan(mcp_tool, description))
    if not safety.allowed:
        logger.warning(
            "MCP: '%s' sunucusunun '%s' aracı güvenlik taramasına takıldı (%s) - "
            "araç ATLANDI (olası 'tool poisoning').",
            server.name, mcp_tool.name, safety.reason,
        )
        return None

    schema = mcp_tool.input_schema or {}
    return MCPTool(
        name=build_mcp_tool_name(server.name, mcp_tool.name),
        description=description,
        parameters_schema=schema.get("properties", {}) or {},
        required_parameters=list(schema.get("required") or []),
        risk_level=server.default_risk_level,
        call_fn=call_fn,
    )


class MCPClientAdapter:
    """Configured MCP sunucularina baglanip araclarini kesfeden/calistiran istemci."""

    def __init__(self, servers: Optional[list[MCPServerConfig]] = None) -> None:
        # None => gercek config/mcp_servers.yaml okunur; testler kendi sahte
        # MCPServerConfig listesini gecebilir (gercek npx/subprocess olmadan).
        self._servers = servers if servers is not None else load_mcp_servers_config()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._serve_future: Optional["asyncio.Future[None]"] = None
        self._sessions: dict[str, ClientSession] = {}
        self._servers_by_name: dict[str, MCPServerConfig] = {s.name: s for s in self._servers}
        self._tools_cache: dict[str, Tool] = {}
        self._started = False
        self._start_lock = threading.Lock()

    def _ensure_started(self) -> None:
        """Arka plan event-loop'unu ve tum sunucu baglantilarini (idempotent) baslatir."""
        if self._started:
            return
        with self._start_lock:
            if self._started:
                return
            self._started = True

            if not self._servers:
                logger.info("MCP: yapılandırılmış/etkin sunucu yok, MCP katmanı boş kalacak.")
                return

            thread_ready = threading.Event()

            def _run_loop() -> None:
                loop = asyncio.new_event_loop()
                self._loop = loop
                asyncio.set_event_loop(loop)
                thread_ready.set()
                loop.run_forever()

            self._thread = threading.Thread(
                target=_run_loop, name="mcp-client-adapter", daemon=True
            )
            self._thread.start()
            thread_ready.wait(timeout=_THREAD_READY_TIMEOUT_SECONDS)

            # _serve() sunuculara baglanip DAHA SONRA shutdown() cagrilana kadar
            # bekleyen, TEK bir uzun-omurlu coroutine/Task - bkz. asagidaki
            # docstring'de anlatilan "ayni Task'ta ac/kapa" zorunlulugu.
            connect_done = threading.Event()
            self._serve_future = asyncio.run_coroutine_threadsafe(
                self._serve(connect_done), self._loop
            )
            finished = connect_done.wait(
                timeout=_CONNECT_TIMEOUT_SECONDS * max(len(self._servers), 1)
            )
            if not finished:
                logger.error("MCP: sunuculara bağlanma zaman aşımına uğradı.")

    async def _serve(self, connect_done: threading.Event) -> None:
        """Sunuculara baglanip kesfeder, SONRA shutdown() cagrilana kadar bekler.

        ONEMLI (anyio/asyncio kisitlamasi): `stdio_client`/`ClientSession`'in
        ic tarafinda kullandigi anyio cancel scope'lari, ACILDIGI Task ile
        AYNI Task icinde KAPATILMAK zorunda ("Attempted to exit cancel scope
        in a different task than it was entered in" hatasi, gercek testte
        gorulup buraya duzeltildi). Bu yuzden AsyncExitStack'i ayri bir
        "connect" ve ayri bir "close" coroutine'ine BOLMUYORUZ - TEK bir
        coroutine hem acip hem (bir shutdown sinyali bekledikten sonra)
        kapatiyor; `shutdown()` sadece `self._shutdown_event`'i set ederek
        bu coroutine'in `async with` bloğundan çıkmasını (ve böylece aynı
        Task içinde temizlik yapmasını) tetikliyor.
        """
        self._shutdown_event = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                discovered: dict[str, Tool] = {}

                for server in self._servers:
                    try:
                        params = StdioServerParameters(
                            command=server.command, args=server.args, env=server.env
                        )
                        read, write = await stack.enter_async_context(stdio_client(params))
                        session = await stack.enter_async_context(ClientSession(read, write))
                        await session.initialize()
                        self._sessions[server.name] = session

                        result = await session.list_tools()
                        added = 0
                        for mcp_tool in result.tools:
                            wrapped = _wrap_mcp_tool(
                                server, mcp_tool, self._make_call_fn(server.name, mcp_tool.name)
                            )
                            if wrapped is not None:
                                discovered[wrapped.name] = wrapped
                                added += 1

                        logger.info(
                            "MCP: '%s' sunucusuna bağlanıldı, %d araç açıldı (toplam %d).",
                            server.name, added, len(result.tools),
                        )
                    except Exception as exc:
                        # Bir sunucunun basarisiz olmasi digerlerini/uygulamanin
                        # geri kalanini durdurmamali (bkz. modul docstring'i, FAIL-SOFT).
                        logger.error("MCP: '%s' sunucusuna bağlanılamadı: %s", server.name, exc)

                self._tools_cache = discovered
                connect_done.set()

                await self._shutdown_event.wait()
        finally:
            # Guvenlik agi: yukaridaki blokta connect_done.set()'e ULASAMADAN
            # beklenmeyen bir hata olursa bile _ensure_started() sonsuza kadar
            # beklemesin.
            connect_done.set()

    def _make_call_fn(self, server_name: str, mcp_tool_name: str) -> Callable[[dict], str]:
        def _call(arguments: dict) -> str:
            return self.call_tool(server_name, mcp_tool_name, arguments)

        return _call

    async def _call_tool_async(self, server_name: str, mcp_tool_name: str, arguments: dict) -> str:
        session = self._sessions.get(server_name)
        if session is None:
            raise RuntimeError(f"MCP sunucusu '{server_name}' bağlı değil.")

        # security-reviewer bulgusu: allowlist eskiden SADECE kesif anında
        # (_wrap_mcp_tool) uygulanıyordu - call_tool() herhangi bir isimle
        # çağrılabilirdi (bugün pratikte sömürülemez, çünkü Router sadece
        # zaten filtrelenmiş isimleri görüyor, ama derinlemesine savunma
        # için ikinci bir katman: ileride eklenecek bir yol - orn. otonom
        # görev zinciri, Faz 6 - doğrudan adapter.call_tool() çağırırsa bile
        # allowlist dışı bir araç asla çalıştırılamaz).
        server = self._servers_by_name.get(server_name)
        if server is not None and server.allowed_tools is not None and mcp_tool_name not in server.allowed_tools:
            raise RuntimeError(
                f"'{mcp_tool_name}' aracı '{server_name}' sunucusunun allowlist'inde değil."
            )

        # asyncio.wait_for: cagiran taraf (call_tool()) zaman asiminda
        # future.cancel() cagirsa bile, iptal DOGRUDAN burada garanti altina
        # alinir - session.call_tool() suresiz askida kalan/kotu niyetli bir
        # sunucuda sonsuza kadar canli kalan bir Task birakmaz (security-reviewer
        # bulgusu: eskiden hicbir iptal yoktu, donmus bir sunucu tekrar tekrar
        # cagrildikca kalici Task sizintisina yol aciyordu).
        result = await asyncio.wait_for(
            session.call_tool(mcp_tool_name, arguments), timeout=_CALL_TIMEOUT_SECONDS
        )
        text_parts = [
            block.text for block in result.content if getattr(block, "type", None) == "text"
        ]
        text = "\n".join(text_parts).strip()
        if result.is_error:
            raise RuntimeError(text or f"MCP aracı '{mcp_tool_name}' hata döndürdü.")
        return text

    def call_tool(self, server_name: str, mcp_tool_name: str, arguments: dict) -> str:
        """Senkron köprü - `MCPTool.execute()` tarafından çağrılır (bkz. modül docstring'i)."""
        self._ensure_started()
        if self._loop is None:
            raise RuntimeError("MCP adaptörü başlatılamadı (bağlı sunucu yok).")

        future = asyncio.run_coroutine_threadsafe(
            self._call_tool_async(server_name, mcp_tool_name, arguments), self._loop
        )
        try:
            return future.result(timeout=_CALL_TIMEOUT_SECONDS + 1.0)
        except TimeoutError:
            # _call_tool_async'in kendi ic `wait_for`'u normalde bu noktadan
            # once zaten patlamis olmali (+1.0s pay) - buraya sadece o
            # mekanizma da tikanirsa dusulur; future.cancel() coroutine'i/Task'i
            # gercekten sonlandirmaya calisir (bkz. yukaridaki yorum).
            future.cancel()
            raise

    def discover_tools(self) -> dict[str, Tool]:
        """Keşfedilen tüm MCP araçlarını (`mcp_<sunucu>_<ad>` -> `MCPTool`) döndürür.

        Sonuç CACHE'lenir - her çağrıda sunuculara yeniden bağlanılmaz (bkz.
        modül docstring'i, async<->sync köprü notu). `tools/registry.py:
        all_tools()` her Router turunda bunu çağırır - bu yüzden ucuz olmalı.
        """
        self._ensure_started()
        return dict(self._tools_cache)

    def shutdown(self) -> None:
        """Tüm MCP alt-süreçlerini/oturumlarını kapatır, arka plan thread'ini durdurur.

        `_shutdown_event`'i set etmek, `_serve()`'ün (hâlâ AYNI Task'ta)
        `AsyncExitStack`'ten çıkıp gerçek temizliği yapmasını tetikler - bkz.
        `_serve()` docstring'indeki anyio cancel-scope notu.
        """
        if self._loop is None or self._shutdown_event is None:
            return

        self._loop.call_soon_threadsafe(self._shutdown_event.set)
        if self._serve_future is not None:
            try:
                self._serve_future.result(timeout=10.0)
            except Exception as exc:
                logger.warning("MCP: kapatırken hata: %s", exc)

        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._loop = None
        self._started = False


_default_adapter: Optional[MCPClientAdapter] = None
_default_adapter_lock = threading.Lock()


def get_default_adapter() -> MCPClientAdapter:
    """Modül-seviyesi Singleton (bkz. modül docstring'i) - `tools/registry.py` bunu kullanır."""
    global _default_adapter
    with _default_adapter_lock:
        if _default_adapter is None:
            _default_adapter = MCPClientAdapter()
        return _default_adapter


if __name__ == "__main__":
    # Manuel doğrulama girişi - `python -m src.jarvis.tools.spotify`nin OAuth
    # test deseniyle aynı ilke: mikrofon/tam pipeline gerektirmeden,
    # config/mcp_servers.yaml'daki sunuculara gerçekten bağlanıp keşfedilen
    # araçları listeler (bkz. docs/plans/... doğrulama adımı).
    from src.jarvis.core.console import setup_logging

    setup_logging()
    adapter = MCPClientAdapter()
    tools = adapter.discover_tools()
    print(f"\n{len(tools)} MCP aracı keşfedildi:\n")
    for tool_name, tool in tools.items():
        print(f"- {tool_name} (risk={tool.risk_level.value}): {tool.description}")
        print(f"  parametreler: {list(tool.parameters_schema.keys())}, zorunlu: {tool.required_parameters}")
    adapter.shutdown()
