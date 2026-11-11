# SPDX-License-Identifier: MIT
"""MBDSDR 电脑端手机 WebSocket 服务。

接 Flutter 手机端：
- 收手机 GPS / 指南针 / IMU 遥测
- 每 5 秒用手机位置 + sgp4 算可见卫星，回推 satellite_passes
- 收手机 chat 文本，回 ai_command

启动：python -m mbdsdr_ai.mobile_server
"""
import asyncio
import json
import time
import logging

log = logging.getLogger("mbdsdr.mobile_server")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765


class MobileHub:
    """管理手机连接和最新遥测。"""

    def __init__(self):
        self.clients = set()
        self.last_fix = {"lat": None, "lon": None, "alt": None}
        self.last_heading = 0.0

    async def register(self, ws):
        self.clients.add(ws)

    async def unregister(self, ws):
        self.clients.discard(ws)

    async def broadcast(self, msg: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send(json.dumps(msg, ensure_ascii=False))
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unregister(ws)

    async def handle(self, ws):
        await self.register(ws)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                await self.on_message(ws, msg)
        finally:
            await self.unregister(ws)

    async def on_message(self, ws, msg: dict):
        mtype = msg.get("type")
        if mtype == "handshake":
            log.info("mobile handshake: %s", msg.get("payload", {}).get("device"))
            await ws.send(json.dumps({"type": "hello", "ok": True}))
        elif mtype == "telemetry":
            kind = msg.get("kind")
            p = msg.get("payload", {})
            if kind == "fix":
                self.last_fix.update({k: p.get(k) for k in ("lat", "lon", "alt") if p.get(k) is not None})
            elif kind == "heading":
                self.last_heading = p.get("deg", self.last_heading)
        elif mtype == "chat":
            text = msg.get("payload", {}).get("text", "")
            # AI 回复留给主 Agent 内核接；这里先回一个占位确认
            await ws.send(json.dumps({
                "type": "ai_command",
                "payload": {"text": f"已收到：{text}（AI 内核待接入）"},
            }, ensure_ascii=False))


def compute_passes(lat: float, lon: float):
    """用 sgp4 算当前可见卫星。

    返回 ``(passes, error)``：``error`` 非空表示**服务不可用**（如时间基准
    缺失），此时 ``passes`` 为空但手机端必须显示原因——不能把故障渲染成
    「当前没有可见卫星」。
    """
    try:
        from mbdsdr_ai import orbit
    except Exception as e:
        log.warning("orbit import failed: %s", e)
        return [], f"轨道模块不可用：{e}"
    try:
        # 注意：orbit.visible_satellites 的阈值参数名是 min_elevation。
        # 曾误写 min_el 致 TypeError 被 except 吞掉、过境计算恒返回空列表
        # （phase66 覆盖测试补全时实证发现并修复）。
        return orbit.visible_satellites(lat, lon, min_elevation=0), None
    except orbit.TimeBasisUnavailable as e:
        log.warning("mobile passes unavailable: %s", e)
        return [], f"时间基准不可用，无法计算可见卫星：{e}"
    except Exception as e:
        log.warning("mobile passes failed: %s", e)
        return [], f"可见卫星计算失败：{e}"


async def pointing_loop(hub: MobileHub, interval: float = 5.0):
    """周期把卫星过境推给手机。"""
    while True:
        await asyncio.sleep(interval)
        if hub.last_fix["lat"] is None:
            continue
        passes, error = compute_passes(hub.last_fix["lat"], hub.last_fix["lon"])
        payload = {"passes": passes, "status": "unavailable" if error else "ok"}
        if error:
            payload["error"] = error
        await hub.broadcast({"type": "satellite_passes", "payload": payload})


async def serve(host=DEFAULT_HOST, port=DEFAULT_PORT):
    import websockets
    hub = MobileHub()
    async with websockets.serve(hub.handle, host, port):
        log.info("mobile server on ws://%s:%d", host, port)
        await pointing_loop(hub)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
