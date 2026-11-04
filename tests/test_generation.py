from generation import (
    CloudLLMBackend,
    GenerationBackend,
    GenerationRequest,
    GenerationResult,
    GenerationRouter,
    LocalModelBackend,
    ScriptedBackend,
)
class RecordingBackend(GenerationBackend):
    def __init__(self, name, handled, text):
        self.name = name
        self.handled = handled
        self.text = text
        self.calls = 0
    def generate(self, request):
        self.calls += 1
        return GenerationResult(
            text=self.text if self.handled else "",
            backend=self.name,
            handled=self.handled,
        )
def request():
    return GenerationRequest(text="hello", language="en")
def test_scripted_response_short_circuits_local_and_cloud():
    scripted = ScriptedBackend({"en": {"greeting": "Hello!"}})
    local = RecordingBackend("local", True, "local")
    cloud = RecordingBackend("cloud", True, "cloud")
    result = GenerationRouter([scripted, local, cloud]).generate(
        GenerationRequest("hello", "en", {"intent": "greeting"})
    )
    assert result.handled is True
    assert result.backend == "scripted"
    assert result.text == "Hello!"
    assert local.calls == 0
    assert cloud.calls == 0
def test_local_handles_after_scripted_declines():
    scripted = ScriptedBackend({"en": {"greeting": "Hello!"}})
    local = RecordingBackend("local", True, "local reply")
    cloud = RecordingBackend("cloud", True, "cloud reply")
    result = GenerationRouter([scripted, local, cloud]).generate(request())
    assert result.backend == "local"
    assert result.text == "local reply"
    assert local.calls == 1
    assert cloud.calls == 0
def test_cloud_handles_after_scripted_and_local_decline():
    scripted = ScriptedBackend()
    local = LocalModelBackend()
    cloud = RecordingBackend("cloud", True, "cloud reply")
    result = GenerationRouter([scripted, local, cloud]).generate(request())
    assert result.backend == "cloud"
    assert result.text == "cloud reply"
    assert cloud.calls == 1
def test_router_returns_clean_failure_when_no_backend_handles():
    result = GenerationRouter(
        [
            RecordingBackend("scripted", False, ""),
            RecordingBackend("local", False, ""),
            RecordingBackend("cloud", False, ""),
        ]
    ).generate(request())
    assert result.handled is False
    assert result.text == ""
    assert result.backend == "none"
    assert result.metadata["reason"] == "No backend handled the request"
def test_cloud_backend_is_provider_agnostic():
    class FakeProvider:
        def generate(self, request):
            return "provider response"
    result = CloudLLMBackend(FakeProvider()).generate(request())
    assert result.handled is True
    assert result.backend == "cloud"
    assert result.text == "provider response"