"""시험용 실시간 자막 서버 (FastAPI WebSocket).

  uvicorn gunggeum_stt.server:app --host 0.0.0.0 --port 8765

해설사  ws://host/ws/guide?session=S&location=광화문&langs=en,th[&vad=0][&close_ms=1500]
        - 바이너리 프레임: PCM 16 kHz·16 bit·모노
        - 텍스트 프레임(JSON): {"type":"location","location":"근정전"} / {"type":"mic","on":false} / {"type":"end"}
        - 받는 것: partial / final / gate / error 이벤트 (인식 문장 미리보기)
관람객  ws://host/ws/listen?session=S&lang=en
        - 받는 것: {"type":"subtitle","seq":n,"ko":"…","text":"…","linked":"…","ts":…} 와 {"type":"ended"}
        - 접속 시 {"type":"hello","last_seq":n} 을 보내 놓친 순번을 알 수 있게 함

REDIS_URL 이 있으면 자막을 채널 live:<session> 에도 JSON으로 발행한다 (백엔드 WebSocket 중계와 맞출 때 채널 이름만 바꾸면 됨).
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .pipeline import LivePipeline, PipelineConfig
from .vad import GateConfig

app = FastAPI(title="gunggeum live subtitles (test server)")
log = logging.getLogger("gunggeum_stt.server")
REDIS_URL = os.environ.get("REDIS_URL")
CHANNEL = os.environ.get("SUBTITLE_CHANNEL", "live:{session}")
TERMINOLOGY = os.environ.get("TRANSLATE_TERMINOLOGY", "gungeum-ko")


@dataclass
class Session:
    id: str
    languages: list[str]
    pipeline: LivePipeline
    listeners: dict[WebSocket, str] = field(default_factory=dict)
    last_seq: int = 0
    ended: bool = False


sessions: dict[str, Session] = {}
_redis = None


async def redis_client():
    global _redis
    if REDIS_URL and _redis is None:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(REDIS_URL)
    return _redis


async def broadcast(sess: Session, ev: dict):
    sess.last_seq = ev["seq"]
    for ws, lang in list(sess.listeners.items()):
        msg = {"type": "subtitle", "seq": ev["seq"], "ko": ev["ko"], "text": ev["text"].get(lang, ""),
               "linked": ev["links"].get(lang, ""), "ms": ev["ms"], "audio_end_abs": ev.get("audio_end_abs"), "ts": time.time()}
        try:
            await ws.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception:
            sess.listeners.pop(ws, None)
    r = await redis_client()
    if r is not None:
        await r.publish(CHANNEL.format(session=sess.id), json.dumps(ev, ensure_ascii=False))


async def pump(sess: Session, guide: WebSocket):
    async for ev in sess.pipeline.events():
        if ev["type"] == "subtitle":
            await broadcast(sess, ev)
        try:
            await guide.send_text(json.dumps(ev, ensure_ascii=False))
        except Exception:
            pass


@app.websocket("/ws/guide")
async def ws_guide(ws: WebSocket):
    await ws.accept()
    q = ws.query_params
    session_id = q.get("session", "default")
    langs = [l for l in q.get("langs", "en").split(",") if l]
    vad_on = q.get("vad", "1") not in ("0", "false")
    cfg = PipelineConfig(terminology=TERMINOLOGY or None, vad=GateConfig(enabled=vad_on),
                         close_after_ms=int(q.get("close_ms", 1500)))
    pipe = LivePipeline(langs, q.get("location"), cfg)
    sess = sessions.get(session_id)
    if sess is None:
        sess = sessions[session_id] = Session(session_id, langs, pipe)
    else:
        sess.pipeline, sess.languages, sess.ended = pipe, langs, False
    await pipe.start()
    pump_task = asyncio.create_task(pump(sess, ws))
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes"):
                await pipe.feed(msg["bytes"])
            elif msg.get("text"):
                ctl = json.loads(msg["text"])
                if ctl.get("type") == "location":
                    pipe.set_location(ctl.get("location"))
                elif ctl.get("type") == "mic" and not ctl.get("on", True):
                    await pipe.stop()
                    break
                elif ctl.get("type") == "end":
                    break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("guide socket error (session %s)", session_id)
    finally:
        try:
            if not pipe._stopped:
                await pipe.stop()
            await pump_task
        except Exception:
            log.exception("pipeline stop error (session %s)", session_id)
        sess.ended = True
        for l in list(sess.listeners):
            try:
                await l.send_text(json.dumps({"type": "ended", "last_seq": sess.last_seq}))
            except Exception:
                pass


@app.websocket("/ws/listen")
async def ws_listen(ws: WebSocket):
    await ws.accept()
    q = ws.query_params
    sess = sessions.get(q.get("session", "default"))
    if sess is None:
        await ws.send_text(json.dumps({"type": "error", "message": "no such session"}))
        await ws.close()
        return
    sess.listeners[ws] = q.get("lang", "en")
    await ws.send_text(json.dumps({"type": "hello", "last_seq": sess.last_seq, "languages": sess.languages}))
    try:
        while True:
            await ws.receive_text()  # ping 등은 무시
    except WebSocketDisconnect:
        pass
    finally:
        sess.listeners.pop(ws, None)


@app.get("/health")
async def health():
    return {"ok": True, "sessions": {k: {"languages": v.languages, "listeners": len(v.listeners), "last_seq": v.last_seq} for k, v in sessions.items()}}
