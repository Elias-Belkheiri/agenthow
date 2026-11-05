import httpx
import pytest


@pytest.fixture
def mock_model_http(monkeypatch):
    """Exercise real SDK translation, replacing only the HTTP network boundary."""
    def install(respond):
        requests = []

        def handle_request(transport, request):
            requests.append(request)
            response = respond(request)
            response.request = request
            return response

        monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', handle_request)
        return requests

    return install
