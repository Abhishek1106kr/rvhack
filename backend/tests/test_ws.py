"""The WebSocket transport end to end: a scripted client talks to the real server."""

import json

from fastapi.testclient import TestClient

from app.adapters.base import Adapters
from app.adapters.fake import EchoLLM, ToneTTS
from app.events import EventAdapter
from app.main import create_app
from app.runtime import Runtime


def client_for_test() -> TestClient:
    runtime = Runtime(make_adapters=lambda: Adapters(llm=EchoLLM(word_delay_s=0), tts=ToneTTS()))
    return TestClient(create_app(runtime))


def receive_until(ws, event_type: str, on_audio=None) -> list[dict]:
    events: list[dict] = []
    while True:
        message = json.loads(ws.receive_text())
        if message["type"] == "audio" and on_audio:
            on_audio(message)
        if message["type"] == "event":
            events.append(message["event"])
            if message["event"]["event_type"] == event_type:
                return events


def test_text_turn_over_websocket_with_playback_acks() -> None:
    client = client_for_test()
    with client.websocket_connect("/ws/session") as ws:
        started = receive_until(ws, "SESSION_STATE_CHANGED")
        session_id = started[0]["session_id"]
        assert started[0]["adapters"] == {
            "vad": None,
            "stt": None,
            "llm": "echo-llm",
            "tts": "tone-tts",
        }

        ws.send_text(json.dumps({"type": "text", "text": "hello arc"}))

        def ack_finished_sentences(audio: dict) -> None:
            if audio["is_last"]:
                ws.send_text(
                    json.dumps(
                        {
                            "type": "ack",
                            "turn_id": audio["turn_id"],
                            "sentence_id": audio["sentence_id"],
                        }
                    )
                )

        events = receive_until(ws, "AGENT_TURN_FINISHED", on_audio=ack_finished_sentences)
        finished = events[-1]
        assert finished["outcome"] == "completed"
        assert finished["spoken_text"].startswith("I heard: hello arc.")
        assert finished["sentences_acked"] == finished["sentences_total"] == 3

    trace = client.get(f"/sessions/{session_id}/trace")
    assert trace.status_code == 200
    events = [EventAdapter.validate_json(line) for line in trace.text.splitlines()]
    assert events[-1].event_type == "SESSION_STATE_CHANGED"
    assert events[-1].current == "IDLE"  # disconnect closed the session


def test_invalid_client_message_is_reported_not_fatal() -> None:
    with client_for_test().websocket_connect("/ws/session") as ws:
        receive_until(ws, "SESSION_STATE_CHANGED")
        ws.send_text('{"type": "launch_missiles"}')
        error = receive_until(ws, "ERROR")[-1]
        assert error["stage"] == "transport" and error["recoverable"] is True
        ws.send_text(json.dumps({"type": "text", "text": "still alive?"}))
        assert receive_until(ws, "AGENT_TURN_STARTED")[-1]["user_text"] == "still alive?"


def test_unknown_session_trace_is_404() -> None:
    assert client_for_test().get("/sessions/nope/trace").status_code == 404
