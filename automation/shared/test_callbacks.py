from types import SimpleNamespace

from shared import callbacks


def test_deliver_retries_when_waiting_webhook_is_not_registered(monkeypatch):
    responses = iter([404, 200])

    def post(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(status_code=next(responses))

    monkeypatch.setattr(callbacks.httpx, "post", post)
    monkeypatch.setattr(callbacks, "RETRY_DELAYS_S", (0.0,))

    assert callbacks.deliver("http://n8n/webhook-waiting/62", {"state": "done"})


def test_deliver_reports_non_success_response_as_failed(monkeypatch):
    def post(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(status_code=400)

    monkeypatch.setattr(callbacks.httpx, "post", post)
    monkeypatch.setattr(callbacks, "RETRY_DELAYS_S", ())

    assert not callbacks.deliver("http://n8n/webhook-waiting/62", {"state": "done"})
