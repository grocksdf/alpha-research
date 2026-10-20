import httpx

from alpharesearch.brain import BrainClient


def test_async_simulation_flow():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/authentication":
            return httpx.Response(201, json={"token": "ok"})
        if path == "/simulations" and request.method == "POST":
            return httpx.Response(201, headers={"Location": "/simulations/1"})
        if path == "/simulations/1":
            return httpx.Response(200, json={"status": "COMPLETE", "alpha": {"id": "a1"}})
        if path == "/alphas/a1":
            return httpx.Response(200, json={"sharpe": 1.8, "fitness": 1.2, "turnover": 0.2})
        if path == "/alphas/a1/check":
            return httpx.Response(200, json={"checks": [{"name": "SHARPE", "status": "PASS"}]})
        return httpx.Response(404)

    client = BrainClient(
        email="user@example.com",
        password="password",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = client.simulate("rank(close)", {"region": "USA"})
    finally:
        client.close()
    assert result.alpha_id == "a1"
    assert result.checks[0]["status"] == "PASS"
